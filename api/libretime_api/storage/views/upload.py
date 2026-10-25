"""
Taking an audio file into the library.

The legacy equivalent is ``POST /rest/media``, which the library's upload widget
posts to: ``CcFiles::createFromUpload`` checks the extension and the disk, saves a
``cc_files`` row owned by the uploading user, moves the bytes out of php's temp
directory into the shared storage, and tells the analyzer on RabbitMQ that there is
something to read. The row is deliberately incomplete at that point - the title is
the filename and the metadata is empty - because reading a file's tags is the
analyzer's job, and it is a different machine in a different container.

This endpoint does the same five things, and inherits the analyzer's contract from
:mod:`libretime_api.notify.analyzer`, whose messages are byte-identical to the ones
php sends.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ...core.models import Preference, User
from ...core.models.role import Role
from ...notify.analyzer import send_message_to_analyzer
from ...permissions import IsSystemTokenOrUser
from ..models import File, Library
from ..serializers import FileSerializer

logger = logging.getLogger(__name__)

# The extensions a station accepts, taken from FileDataHelper: the audio mime map
# it keeps, minus windows media audio, plus opus - which is not a real mime type
# and is in that list specifically so .opus uploads work. Anything the analyzer can
# read is decided by the analyzer; this is only the first, cheap filter.
UPLOADABLE_EXTENSIONS = {
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp2": "audio/mp2",
    ".mp1": "audio/mp1",
    ".au": "audio/basic",
    ".opus": "audio/opus",
}

# The directory uploads land in before the analyzer reads them, named after the
# file's id so that two uploads of the same filename cannot collide.
ORGANIZE_DIRECTORY = "organize"
IMPORTED_DIRECTORY = "imported"


def _storage_path() -> Path:
    return Path(settings.CONFIG.storage.path)


def _disk_is_full(storage: Path) -> bool:
    """
    Whether there is no room left, which is what the legacy check asks.

    ``Application_Model_Systemstatus::isDiskOverQuota`` compares used against total
    and only refuses when the disk is genuinely full - it is not a quota in the
    administrative sense, despite its name - so this mirrors that rather than
    inventing a threshold the station does not have.
    """
    try:
        usage = shutil.disk_usage(storage)
    except OSError:
        # Cannot tell; let the write fail loudly rather than refuse every upload
        # because a stat call did not work.
        return False

    used = usage.total - usage.free

    return used > 0 and used >= usage.total


def _safe_name(original: str) -> str:
    """
    The filename on its own, without whatever came before it.

    The legacy code keeps directory parts in the name so that files imported over
    ftp can be tidied out of the folder they arrived in. An upload arriving here
    comes from a browser, and a name containing a separator or a parent reference
    is a path to write somewhere nobody meant, so this takes the basename and drops
    control characters. It is a deliberate difference from php, and the direction of
    it is the point.
    """
    name = Path(original.replace("\x00", "")).name

    return "".join(character for character in name if character.isprintable()).strip(
        " ."
    )


def _owner(request) -> User | None:
    """
    Who the file belongs to: the uploader, or the station's first admin.

    The second half is the legacy behaviour for callers with no session - the
    analyzer queue, a script posting with the api key - whose imports have always
    been attributed to a person so that somebody can find and edit them later.
    A superadmin counts: ``CcFiles::getOwnerId`` looks for ``type='A'`` and would
    find nobody on a station whose only admin is an ``'S'``.
    """
    if request.user is not None and request.user.is_authenticated:
        return request.user

    return (
        User.objects.filter(role__in=(Role.ADMIN, Role.SUPERADMIN))
        .order_by("pk")
        .first()
    )


def _library(request) -> Library | None:
    """
    Which library - what php calls a track type - the file is filed under.

    The request may say; otherwise the station's default applies, which is the
    ``tt_upload`` cookie for a browser in the legacy interface and the
    ``tracktype_default`` preference for everybody else. A cookie is not something
    an api client should have to set, so only the preference is read here, and a
    client that wants a specific library says so.
    """
    submitted = request.data.get("library")

    if submitted in (None, ""):
        default = Preference.objects.filter(key="tracktype_default").first()
        submitted = default.value if default else None

    if submitted in (None, ""):
        return None

    try:
        return Library.objects.filter(pk=int(submitted)).first()
    except (TypeError, ValueError):
        raise ValidationError("library must identify a library") from None


class UploadView(APIView):
    """
    ``POST /api/v2/upload`` with a multipart ``file`` field.

    Returns the created ``cc_files`` row, which at this point knows its filename,
    its size, its checksum and its owner and nothing else - the metadata arrives
    when the analyzer has read the file. A caller holding ``add_file`` may upload;
    a guest may not, and a request carrying an api key imports on behalf of the
    station rather than of a person.
    """

    parser_classes = [MultiPartParser]
    permission_classes = [IsSystemTokenOrUser]
    model_permission_name = "file"

    @extend_schema(
        request=OpenApiTypes.BINARY,
        responses={201: FileSerializer},
        description=(
            "A multipart body holding one file. The optional ``library`` field puts "
            "it in a library other than the station's default."
        ),
    )
    def post(self, request):
        upload = request.data.get("file")

        if not isinstance(upload, UploadedFile):
            return Response(
                {"file": "No file was sent. Use a multipart form with a file field."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = _safe_name(upload.name or "")
        extension = Path(name).suffix.lower()

        if not name or extension not in UPLOADABLE_EXTENSIONS:
            return Response(
                {
                    "name": "That is not a file this station can take.",
                    "accepted": sorted(UPLOADABLE_EXTENSIONS),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if upload.size == 0:
            return Response(
                {"file": "The file is empty."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        storage = _storage_path()

        if _disk_is_full(storage):
            return Response(
                {"detail": "Disk quota reached."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        owner = _owner(request)
        library = _library(request)

        file = File.objects.create(
            owner=owner,
            library=library,
            track_title=name,
            mime=UPLOADABLE_EXTENSIONS[extension],
            size=upload.size,
            hidden=True,
            import_status=File.ImportStatus.PENDING,
            # cc_files.utime is "timestamp without time zone". Checked against a
            # live database rather than assumed: an aware UTC value is stored as
            # that same wall clock and read back naive, so this stays in the
            # convention the rest of the api writes in, without the warning Django
            # raises for a naive datetime under USE_TZ.
            created_at=datetime.now(tz=UTC),
        )

        try:
            target = self._store(upload, storage, file.pk, name)
            file.md5 = hashlib.md5(
                target.read_bytes(), usedforsecurity=False
            ).hexdigest()
            file.save(update_fields=["md5"])

            send_message_to_analyzer(
                str(target),
                str(storage / IMPORTED_DIRECTORY / str(owner.pk if owner else "")),
                name,
                file.pk,
                library.analyze_cue_points if library else None,
            )
        except Exception:  # noqa: BLE001
            # The row stays, marked failed, exactly as php leaves it: the upload is
            # a fact about the station, and the library's "failed" filter is where
            # somebody goes looking for it. Losing the row would lose the filename
            # too, which is all anybody has to go on.
            logger.exception("could not import the uploaded file %s", name)
            file.import_status = File.ImportStatus.FAILED
            file.hidden = True
            file.save(update_fields=["import_status", "hidden"])

            raise

        return Response(FileSerializer(file).data, status=status.HTTP_201_CREATED)

    @staticmethod
    def _store(upload: UploadedFile, storage: Path, file_id: int, name: str) -> Path:
        """
        Move the uploaded bytes into shared storage.

        The analyzer may be another machine, so the file has to leave this
        process's temp directory. It is written under the name the analyzer is told
        about, and made readable, which is what php does after its own move.
        """
        organize = storage / ORGANIZE_DIRECTORY
        organize.mkdir(parents=True, exist_ok=True)

        target = organize / f"{file_id}-{name}"

        with target.open("wb") as handle:
            for chunk in upload.chunks():
                handle.write(chunk)

        target.chmod(0o644)

        return target
