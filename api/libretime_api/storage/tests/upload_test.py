"""
Uploading a file, and what the station is left with.

The message handed to the analyzer is checked field by field against what php
sends (``Application_Model_RabbitMq::SendMessageToAnalyzer``), because the two
services only agree by convention: the analyzer reads ``tmp_file_path``,
``import_directory``, ``original_filename``, ``file_id`` and ``options``.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

# The fixtures below are asked for their effect - storage isolation, and catching a
# publish that should not have happened - not for their value
# pylint: disable=unused-argument
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from libretime_api.core.models import Preference, User
from libretime_api.core.models.role import Role
from libretime_api.storage.models import File, Library

FIXTURE = Path(__file__).resolve().parents[2] / "_fixtures" / "song.mp3"

PASSWORD = "upload-pass"


@pytest.fixture()
def storage(tmp_path, settings) -> Iterator[Path]:
    """
    A storage directory of this test's own, so it never touches a station's.

    The previous path is put back by hand rather than trusted to the settings
    fixture: that one restores the attribute on ``settings``, and ``CONFIG`` is an
    object whose contents are being changed here. Left alone, the mutation outlives
    the test and every later test that reads a file out of the configured storage
    starts looking in a directory that no longer exists - which is a confusing way
    to fail a test that has nothing to do with uploads.
    """
    config = settings.CONFIG
    previous = config.storage.path

    root = tmp_path / "libretime"
    root.mkdir()
    config.storage.path = str(root)

    try:
        yield root
    finally:
        config.storage.path = previous


@pytest.fixture()
def published(monkeypatch) -> list:
    messages: list = []

    def fake(*args):
        messages.append(args)

    monkeypatch.setattr(
        "libretime_api.storage.views.upload.send_message_to_analyzer", fake
    )

    return messages


def upload_as(user, *, name="song.mp3", content=None, data=None, client=None):
    client = client or APIClient()

    if user is None:
        from django.conf import settings

        client.credentials(
            HTTP_AUTHORIZATION=f"Api-Key {settings.CONFIG.general.api_key}"
        )
    else:
        client.force_authenticate(user=user)

    # Not "content or default": an empty upload is the case being tested,
    # and a falsy b"" would quietly become the fixture.
    body = FIXTURE.read_bytes() if content is None else content
    payload = {"file": SimpleUploadedFile(name, body)}

    if data:
        payload.update(data)

    return client.post("/api/v2/upload", payload, format="multipart")


def make_user(role: Role, username: str) -> User:
    return User.objects.create_user(
        role=role,
        username=username,
        password=PASSWORD,
        email=f"{username}@example.org",
        first_name="Up",
        last_name="Loader",
    )


@pytest.mark.django_db()
def test_a_host_uploads_and_owns_the_result(storage, published):
    host = make_user(Role.HOST, "uploader")

    response = upload_as(host)

    assert response.status_code == 201, response.data

    file = File.objects.get(pk=response.json()["id"])

    assert file.owner == host
    assert file.track_title == "song.mp3"
    assert file.hidden is True
    assert file.import_status == File.ImportStatus.PENDING
    assert file.mime == "audio/mpeg"
    assert file.size == FIXTURE.stat().st_size
    # The checksum is of the bytes that arrived, which is how the analyzer later
    # notices a duplicate of something already in the library.
    import hashlib

    assert file.md5 == hashlib.md5(FIXTURE.read_bytes()).hexdigest()


@pytest.mark.django_db()
def test_the_file_lands_in_storage_where_the_analyzer_can_read_it(storage, published):
    upload_as(make_user(Role.HOST, "uploader"))

    file = File.objects.get()
    target = storage / "organize" / f"{file.pk}-song.mp3"

    assert target.exists()
    assert target.read_bytes() == FIXTURE.read_bytes()
    # The analyzer may be another container, and php chmods the moved file the same
    # way for the same reason.
    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.django_db()
def test_the_analyzer_is_told_the_shape_php_sends(storage, published):
    host = make_user(Role.HOST, "uploader")
    library = Library.objects.create(
        name="Music", code="MUS", description="", analyze_cue_points=True
    )
    Preference.objects.create(key="tracktype_default", value=str(library.pk))

    upload_as(host)

    tmp_file_path, import_directory, original_filename, file_id, cue_points = published[
        0
    ]
    file = File.objects.get()

    assert Path(tmp_file_path) == storage / "organize" / f"{file.pk}-song.mp3"
    assert import_directory == f"{storage}/imported/{host.pk}"
    assert original_filename == "song.mp3"
    assert file_id == file.pk
    # From the station's default library, which is what the legacy upload reads when
    # the browser has not chosen one in a cookie.
    assert cue_points is True


@pytest.mark.django_db()
def test_a_chosen_library_overrides_the_default(storage, published):
    Library.objects.create(
        name="Music", code="MUS", description="", analyze_cue_points=True
    )
    spoken = Library.objects.create(
        name="Spoken", code="SPK", description="", analyze_cue_points=False
    )

    upload_as(make_user(Role.HOST, "uploader"), data={"library": spoken.pk})

    assert File.objects.get().library == spoken
    assert published[0][4] is False


@pytest.mark.django_db()
def test_no_library_means_no_options_on_the_wire(storage, published):
    upload_as(make_user(Role.HOST, "uploader"))

    assert published[0][4] is None
    assert File.objects.get().library is None


@pytest.mark.django_db()
def test_an_import_without_a_person_is_still_attributed(storage, published):
    """
    A script posting with the api key is not a user. php attributes such an import
    to the station's first admin so that the file is findable and editable by
    somebody, and that is kept.
    """
    make_user(Role.ADMIN, "another-admin")
    make_user(Role.SUPERADMIN, "z-superadmin")

    response = upload_as(None)

    assert response.status_code == 201, response.data

    # The earliest admin account in the station - which may be one the installer
    # created rather than one this test did, so the rule is named rather than a
    # particular row.
    first_admin = (
        User.objects.filter(role__in=(Role.ADMIN, Role.SUPERADMIN))
        .order_by("pk")
        .first()
    )

    assert File.objects.get().owner == first_admin


@pytest.mark.django_db()
@pytest.mark.parametrize("name", ["song.exe", "no-extension", "song.txt", "song.wma"])
def test_what_the_station_cannot_play_is_refused(storage, published, name):
    response = upload_as(make_user(Role.HOST, "uploader"), name=name)

    assert response.status_code == 400
    assert "accepted" in response.json()
    assert not File.objects.exists()
    assert not published


@pytest.mark.django_db()
def test_an_empty_file_is_refused(storage, published):
    response = upload_as(make_user(Role.HOST, "uploader"), name="song.mp3", content=b"")

    assert response.status_code == 400
    assert not File.objects.exists()


@pytest.mark.django_db()
def test_a_request_with_no_file_part_says_so(storage, published):
    client = APIClient()
    client.force_authenticate(user=make_user(Role.HOST, "uploader"))

    response = client.post("/api/v2/upload", {"library": "1"}, format="multipart")

    assert response.status_code == 400


@pytest.mark.django_db()
def test_a_guest_uploads_nothing(storage, published):
    response = upload_as(make_user(Role.GUEST, "visitor"))

    assert response.status_code == 403
    assert not File.objects.exists()


@pytest.mark.django_db()
def test_a_parent_reference_in_a_filename_is_not_a_path(storage, published):
    upload_as(make_user(Role.HOST, "uploader"), name="../../etc/evil.mp3")

    file = File.objects.get()

    assert file.track_title == "evil.mp3"
    assert list((storage / "organize").iterdir()) == [
        storage / "organize" / f"{file.pk}-evil.mp3"
    ]


@pytest.mark.django_db()
def test_a_refusal_to_read_leaves_a_failed_row_behind(storage, monkeypatch):
    """
    The bytes arrived, so the station knows about them even though the import did
    not start: the library's "failed" filter is where an operator looks.
    """

    def boom(*_args):
        raise RuntimeError("rabbitmq is down")

    monkeypatch.setattr(
        "libretime_api.storage.views.upload.send_message_to_analyzer", boom
    )

    # The failure is raised rather than folded into a response, as it is in php.
    # The test client re-raises whatever a view lets escape, which is the same fact
    # seen from outside.
    with pytest.raises(RuntimeError, match="rabbitmq is down"):
        upload_as(make_user(Role.HOST, "uploader"))

    file = File.objects.get()

    assert file.import_status == File.ImportStatus.FAILED
    assert file.hidden is True
