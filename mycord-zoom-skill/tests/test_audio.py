"""The audio plane: creation, routing, and — above all — teardown.

The teardown tests are the point of this file. A leftover null sink silently mutes the
operator's machine, so "the default was restored" is asserted at the *process* level (what
`pactl` was asked to do, and in what order) as well as at the state level.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import module_args
from fake_pactl import OPERATOR_SINK, OPERATOR_SOURCE, FakePactl

from mycord_zoom.audio import (
    DEFAULT_MIC_SINK,
    DEFAULT_MIC_SOURCE,
    DEFAULT_SPEAKER_SINK,
    AudioPlane,
    PlaneSpec,
    temporary_plane,
)
from mycord_zoom.errors import AudioError, Unconfigured
from mycord_zoom.state import AudioSession, StateError, StateStore

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- creation


def test_up_creates_three_modules_in_dependency_order(
    plane: AudioPlane, fake_pactl: FakePactl
) -> None:
    plane.up()

    loaded = module_args(fake_pactl)
    assert [call[0] for call in loaded] == [
        "module-null-sink",
        "module-null-sink",
        "module-remap-source",
    ]
    # The remap-source must come after the sink it remaps, or it fails on a real server.
    sink_args, mic_args, source_args = loaded
    assert f"sink_name={DEFAULT_SPEAKER_SINK}" in sink_args
    assert f"sink_name={DEFAULT_MIC_SINK}" in mic_args
    assert f"master={DEFAULT_MIC_SINK}.monitor" in source_args
    assert f"source_name={DEFAULT_MIC_SOURCE}" in source_args


def test_up_records_the_operator_defaults_before_loading_anything(
    plane: AudioPlane, fake_pactl: FakePactl
) -> None:
    plane.up()

    # The two `get-default-*` calls must precede the first `load-module`, because after the
    # load there is no way to learn what the defaults used to be.
    first_load = next(
        index for index, call in enumerate(fake_pactl.called) if call and call[0] == "load-module"
    )
    gets = [
        index
        for index, call in enumerate(fake_pactl.called)
        if call and call[0] in ("get-default-sink", "get-default-source")
    ]
    assert gets, "the defaults were never read"
    assert max(gets) < first_load

    session = plane._store.load()  # noqa: SLF001 - asserting the persisted record
    assert session is not None
    assert session.previous_default_sink == OPERATOR_SINK
    assert session.previous_default_source == OPERATOR_SOURCE


def test_up_does_not_change_the_default_routing(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    plane.up()

    # Creating devices must be inert: nothing is routed until `route --to-agent`.
    assert fake_pactl.state["default_sink"] == OPERATOR_SINK
    assert fake_pactl.state["default_source"] == OPERATOR_SOURCE
    assert not any(call[:1] == ["set-default-sink"] for call in fake_pactl.called)


def test_up_is_idempotent(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    first = plane.up()
    second = plane.up()

    assert first.modules == second.modules
    assert len(module_args(fake_pactl)) == 3, "a second `up` must not stack another plane"


def test_up_rebuilds_when_the_session_outlived_the_modules(
    plane: AudioPlane, fake_pactl: FakePactl
) -> None:
    session = plane.up()
    # Simulate a PipeWire restart: modules gone, defaults back to the operator's.
    fake_pactl.write_state(
        modules={},
        sinks={"alsa_output.fake_speaker": 50},
        sources={"alsa_output.fake_speaker.monitor": 50, "alsa_input.fake_mic": 51},
    )
    fake_pactl.log_path.write_text("")

    rebuilt = plane.up()

    assert rebuilt.previous_default_sink == session.previous_default_sink
    assert len(module_args(fake_pactl)) == 3


def test_up_refuses_when_default_already_points_at_the_plane(
    store: StateStore, fake_pactl: FakePactl
) -> None:
    # Defaults pointing at our names with no session recorded means "previous" would be our
    # own device, so a restore would leave the operator muted.
    fake_pactl.write_state(
        sinks={"alsa_output.fake_speaker": 50, DEFAULT_SPEAKER_SINK: 60},
        sources={"alsa_input.fake_mic": 51, DEFAULT_MIC_SOURCE: 61},
        default_sink=DEFAULT_SPEAKER_SINK,
        default_source=DEFAULT_MIC_SOURCE,
    )
    with pytest.raises(Unconfigured, match="no session is recorded"):
        AudioPlane(store).up()


def test_up_unloads_a_half_built_plane_when_the_source_fails(
    store: StateStore, fake_pactl: FakePactl, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The remap-source is the step most likely to fail (a bad master name, a server limit).
    monkeypatch.setenv("FAKE_PACTL_FAIL", "module-remap-source")
    plane = AudioPlane(store)

    with pytest.raises(AudioError):
        plane.up()

    assert module_args(fake_pactl)[0][0] == "module-null-sink"
    assert fake_pactl.state["modules"] == {}, "the sinks must not survive a failed up"
    assert plane._store.load() is None  # noqa: SLF001
    assert fake_pactl.state["default_sink"] == OPERATOR_SINK


# --------------------------------------------------------------------------- routing


def test_route_to_agent_moves_both_directions(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    plane.up()
    plane.route("to-agent")

    assert fake_pactl.state["default_sink"] == DEFAULT_SPEAKER_SINK
    assert fake_pactl.state["default_source"] == DEFAULT_MIC_SOURCE


def test_route_to_mixer_restores_both_directions(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    plane.up()
    plane.route("to-agent")
    plane.route("to-mixer")

    assert fake_pactl.state["default_sink"] == OPERATOR_SINK
    assert fake_pactl.state["default_source"] == OPERATOR_SOURCE


def test_route_without_a_plane_is_unconfigured(plane: AudioPlane) -> None:
    with pytest.raises(Unconfigured, match="audio up"):
        plane.route("to-agent")


def test_route_rejects_an_unknown_direction(plane: AudioPlane) -> None:
    plane.up()
    with pytest.raises(AudioError, match="unknown routing direction"):
        plane.route("sideways")


# --------------------------------------------------------------------------- teardown


def test_down_restores_the_defaults(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    plane.up()
    plane.route("to-agent")

    plane.down()

    assert fake_pactl.state["default_sink"] == OPERATOR_SINK
    assert fake_pactl.state["default_source"] == OPERATOR_SOURCE
    assert fake_pactl.state["sinks"] == {"alsa_output.fake_speaker": 50}
    assert fake_pactl.state["sources"] == {
        "alsa_output.fake_speaker.monitor": 50,
        "alsa_input.fake_mic": 51,
    }


def test_down_restores_defaults_before_unloading_modules(
    plane: AudioPlane, fake_pactl: FakePactl
) -> None:
    plane.up()
    plane.route("to-agent")
    fake_pactl.log_path.write_text("")

    plane.down()

    calls = [call[0] for call in fake_pactl.called if call]
    first_restore = calls.index("set-default-sink")
    first_unload = calls.index("unload-module")
    # Restoring first means a failed unload still leaves the operator hearing their audio.
    assert first_restore < first_unload


def test_down_is_idempotent(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    plane.up()
    plane.down()
    result = plane.down()

    assert result["changed"] is False
    assert fake_pactl.state["default_sink"] == OPERATOR_SINK


def test_down_clears_the_recorded_session(store: StateStore, plane: AudioPlane) -> None:
    plane.up()
    plane.down()
    assert store.load() is None


def test_down_repoints_live_streams_off_the_plane(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    plane.up()
    plane.route("to-agent")
    # A stream that started while routed keeps playing into the plane: `set-default-*` only
    # affects new streams, so teardown has to move it explicitly.
    sink_index = fake_pactl.state["sinks"][DEFAULT_SPEAKER_SINK]
    fake_pactl.write_state(**{"sink-inputs": [{"index": 901, "target": sink_index, "client": 42}]})

    plane.down()

    assert fake_pactl.state["sink-inputs"][0]["target"] == 50, "the live stream stayed on the plane"
    assert any(call[0] == "move-sink-input" for call in fake_pactl.called)


def test_down_after_an_interrupted_run_still_restores(
    store: StateStore, fake_pactl: FakePactl
) -> None:
    # Simulate SIGKILL: the session file exists with the operator's defaults, the plane is
    # still loaded, and the process that recorded it is long gone.
    fake_pactl.write_state(
        sinks={"alsa_output.fake_speaker": 50, DEFAULT_SPEAKER_SINK: 200},
        sources={"alsa_input.fake_mic": 51, DEFAULT_MIC_SOURCE: 201},
        default_sink=DEFAULT_SPEAKER_SINK,
        default_source=DEFAULT_MIC_SOURCE,
        modules={
            "200": {"name": "module-null-sink", "args": [f"sink_name={DEFAULT_SPEAKER_SINK}"]},
            "201": {"name": "module-remap-source", "args": [f"source_name={DEFAULT_MIC_SOURCE}"]},
        },
    )
    store.save(
        AudioSession(
            speaker_sink=DEFAULT_SPEAKER_SINK,
            mic_sink=DEFAULT_MIC_SINK,
            mic_source=DEFAULT_MIC_SOURCE,
            modules={"speaker": 200, "mic_source": 201},
            previous_default_sink=OPERATOR_SINK,
            previous_default_source=OPERATOR_SOURCE,
            routed="to-agent",
        )
    )

    AudioPlane(store).recover()

    assert fake_pactl.state["default_sink"] == OPERATOR_SINK
    assert fake_pactl.state["default_source"] == OPERATOR_SOURCE
    assert fake_pactl.state["modules"] == {}


def test_temporary_plane_restores_even_when_the_body_raises(
    plane: AudioPlane, fake_pactl: FakePactl
) -> None:
    with pytest.raises(RuntimeError, match="boom"), temporary_plane(plane) as active:
        active.route("to-agent")
        assert fake_pactl.state["default_sink"] == DEFAULT_SPEAKER_SINK
        raise RuntimeError("boom")

    assert fake_pactl.state["default_sink"] == OPERATOR_SINK
    assert fake_pactl.state["default_source"] == OPERATOR_SOURCE
    assert fake_pactl.state["modules"] == {}


def test_down_reports_problems_instead_of_pretending(
    plane: AudioPlane, fake_pactl: FakePactl, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane.up()
    plane.route("to-agent")
    monkeypatch.setenv("FAKE_PACTL_FAIL", "unload-module")

    with pytest.raises(AudioError, match="teardown incomplete"):
        plane.down()

    # The defaults are still back, which is the important half, and the session is kept so
    # `recover` can retry the unload.
    assert fake_pactl.state["default_sink"] == OPERATOR_SINK
    assert plane._store.load() is not None  # noqa: SLF001


# --------------------------------------------------------------------------- state file


def test_state_file_is_written_0600_and_directory_0700(
    store: StateStore, plane: AudioPlane
) -> None:
    plane.up()
    assert not (store.path.stat().st_mode & 0o077)
    assert not (store.root.stat().st_mode & 0o077)


def test_a_foreign_version_is_refused_rather_than_guessed(store: StateStore) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"version": 99, "speaker_sink": "x"}')
    with pytest.raises(StateError, match="another version"):
        store.load()


def test_corrupt_json_is_refused(store: StateStore) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json")
    with pytest.raises(StateError, match="not valid JSON"):
        store.load()


def test_custom_spec_names_are_honoured(store: StateStore, fake_pactl: FakePactl) -> None:
    spec = PlaneSpec(speaker_sink="z_spk", mic_sink="z_mic", mic_source="z_source")
    AudioPlane(store, spec=spec).up()
    assert "z_spk" in fake_pactl.state["sinks"]
    assert "z_source" in fake_pactl.state["sources"]


def test_status_reports_where_audio_points(plane: AudioPlane) -> None:
    plane.up()
    plane.route("to-agent")
    status = plane.status()
    assert status.running is True
    assert status.routed is True
    assert status.previous_default_sink == OPERATOR_SINK


def test_pcm_commands_name_the_right_devices(plane: AudioPlane, fake_pactl: FakePactl) -> None:
    plane.up()
    mic_argv = plane.mic_command()
    speaker_argv = plane.speaker_command()

    # Speaking goes to the mic sink (the remap-source Zoom records from)...
    assert any(arg == f"--device={DEFAULT_MIC_SINK}" for arg in mic_argv)
    # ...and listening comes from the speaker sink's monitor, never from the mic sink, or
    # the agent would transcribe itself.
    assert any(arg == f"--device={DEFAULT_SPEAKER_SINK}.monitor" for arg in speaker_argv)
    assert not any(DEFAULT_MIC_SINK in arg for arg in speaker_argv)


def test_missing_pactl_is_not_installed(tmp_path: Path) -> None:
    from mycord_zoom.errors import NotInstalled

    blank = tmp_path / "empty-bin"
    blank.mkdir()
    plane = AudioPlane(StateStore(tmp_path / "state"), pactl=None)
    plane._pactl = None  # noqa: SLF001
    import os

    original = os.environ["PATH"]
    os.environ["PATH"] = str(blank)
    try:
        with pytest.raises(NotInstalled):
            _ = plane.pactl
    finally:
        os.environ["PATH"] = original
