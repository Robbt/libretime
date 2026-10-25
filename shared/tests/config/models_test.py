import pytest
from pydantic import ValidationError

from libretime_shared.config._models import (
    AudioAAC,
    AudioMP3,
    AudioOGG,
    AudioOpus,
    GeneralConfig,
    StreamConfig,
)


def test_general_config_timezone():
    defaults = {
        "public_url": "http://localhost:8080",
        "api_key": "api_key",
        "secret_key": "secret_key",
    }
    GeneralConfig(**defaults, timezone="UTC")
    GeneralConfig(**defaults, timezone="Europe/Berlin")
    with pytest.raises(ValidationError):
        GeneralConfig(**defaults, timezone="Europe/Invalid")


def test_general_config_show_instance_populator():
    """Defaults to the legacy service, so existing installs change nothing."""
    defaults = {
        "public_url": "http://localhost:8080",
        "api_key": "api_key",
        "secret_key": "secret_key",
    }
    assert GeneralConfig(**defaults).show_instance_populator == "legacy"

    assert (
        GeneralConfig(**defaults, show_instance_populator="api").show_instance_populator
        == "api"
    )

    with pytest.raises(ValidationError):
        GeneralConfig(**defaults, show_instance_populator="playout")


@pytest.mark.parametrize(
    "audio",
    [
        (AudioAAC),
        (AudioMP3),
        (AudioOGG),
        (AudioOpus),
    ],
)
def test_audio(audio):
    audio(bitrate=32)
    audio(bitrate=320)
    with pytest.raises(ValidationError):
        audio(bitrate=11)
    with pytest.raises(ValidationError):
        audio(bitrate=321)


def test_stream_config():
    icecast_output = {
        "mount": "mount",
        "source_password": "hackme",
        "audio": {"format": "ogg", "bitrate": 256},
    }
    assert StreamConfig(outputs={"icecast": [icecast_output] * 3})
    with pytest.raises(ValidationError):
        StreamConfig(outputs={"icecast": [icecast_output] * 4})

    shoutcast_output = {
        "source_password": "hackme",
        "audio": {"format": "mp3", "bitrate": 256},
    }
    assert StreamConfig(outputs={"shoutcast": [shoutcast_output]})
    with pytest.raises(ValidationError):
        StreamConfig(outputs={"shoutcast": [shoutcast_output] * 2})

    system_output = {
        "kind": "alsa",
    }
    assert StreamConfig(outputs={"system": [system_output]})
    with pytest.raises(ValidationError):
        StreamConfig(outputs={"system": [system_output] * 2})

    config = StreamConfig(
        outputs={
            "icecast": [icecast_output],
            "shoutcast": [shoutcast_output],
            "system": [system_output],
        }
    )
    assert len(config.outputs.icecast) == 1
    assert len(config.outputs.shoutcast) == 1
    assert len(config.outputs.system) == 1


def test_general_config_auto_playlist_builder():
    """Defaults to the legacy poll, so existing installs change nothing."""
    defaults = {
        "public_url": "http://localhost:8080",
        "api_key": "api_key",
        "secret_key": "secret_key",
    }
    assert GeneralConfig(**defaults).auto_playlist_builder == "legacy"

    assert (
        GeneralConfig(**defaults, auto_playlist_builder="api").auto_playlist_builder
        == "api"
    )

    with pytest.raises(ValidationError):
        GeneralConfig(**defaults, auto_playlist_builder="celery")
