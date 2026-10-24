"""Schedule the fixture playlists with the Python port."""

import django

django.setup()

from datetime import datetime, timezone  # noqa: E402

from libretime_api.schedule.models import Playlist, ShowInstance  # noqa: E402
from libretime_api.schedule.services.scheduler import append_playlist  # noqa: E402

# Same instants the legacy side uses for "now": every fixture instance starts in
# 2031, so none of them is treated as already started.
NOW = datetime(2031, 1, 1, tzinfo=timezone.utc)

CASES = [(8001, 8101), (8002, 8101), (8003, 8102)]

for instance_id, playlist_id in CASES:
    append_playlist(
        ShowInstance.objects.get(pk=instance_id),
        Playlist.objects.get(pk=playlist_id),
        now=NOW,
    )
    print(f"{instance_id} {playlist_id}|ok")
