"""Beat density: audit math, switch, Plain exemption, one-punch repair, QC surface."""
from __future__ import annotations

import hashlib
import subprocess

import pytest

from src.media import beat_density as BD


# --- Pure analysis -----------------------------------------------------------

def test_all_change_clip_is_dense():
    audit = BD.analyse_beat_density([25.0] * 79, 40.0)
    assert audit["ok"] is True
    assert audit["longest_static_s"] == 0.0
    assert audit["longest_static"] is None
    assert audit["static_stretches"] == []
    assert audit["changes"] == 79
    assert audit["beat_score"] == 1.0


def test_known_dead_stretch_is_located():
    # 10 s action, 20 s frozen (39 static steps at 2 fps), 10 s action.
    diffs = [25.0] * 20 + [0.0] * 39 + [25.0] * 20
    audit = BD.analyse_beat_density(diffs, 40.0)
    assert audit["ok"] is False
    assert audit["longest_static"] == {"start": 10.0, "end": 30.0, "seconds": 20.0}
    assert audit["longest_static_s"] == 20.0
    assert audit["overlong_static_s"] == 16.5
    assert audit["beat_score"] == pytest.approx(1.0 - 16.5 / 40.0, abs=0.001)


def test_short_static_runs_pass():
    diffs = [25.0] * 10 + [0.0] * 5 + [25.0] * 20  # 3.0 s hold at 2 fps
    audit = BD.analyse_beat_density(diffs, 18.0)
    assert audit["ok"] is True
    assert audit["longest_static_s"] == 3.0
    assert audit["beat_score"] == 1.0


def test_threshold_boundary_step_counts_as_static():
    at = BD.analyse_beat_density([BD.BEAT_STATIC_MAD_MAX] * 10, 5.5)
    assert at["changes"] == 0
    above = BD.analyse_beat_density([BD.BEAT_STATIC_MAD_MAX + 0.01] * 10, 5.5)
    assert above["changes"] == 10


def test_stretch_at_the_end_is_capped_at_duration():
    diffs = [25.0] * 10 + [0.0] * 30
    audit = BD.analyse_beat_density(diffs, 20.0)
    assert audit["longest_static"]["start"] == 5.0
    assert audit["longest_static"]["end"] == 20.0
    assert audit["longest_static_s"] == 15.0


def test_sub_second_runs_are_not_listed_but_still_measure():
    audit = BD.analyse_beat_density([0.0] * 5, 1.0, fps=10.0)
    assert audit["static_stretches"] == []
    assert audit["longest_static_s"] == 0.6


def test_empty_series_is_a_pass():
    audit = BD.analyse_beat_density([], 12.0)
    assert audit["ok"] is True
    assert audit["longest_static_s"] == 0.0
    assert audit["beat_score"] == 1.0


# --- Switch ------------------------------------------------------------------

def test_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("BEAT_DENSITY", raising=False)
    assert BD.beat_density_enabled() is False
    monkeypatch.setenv("BEAT_DENSITY", "on")
    assert BD.beat_density_enabled() is True
    monkeypatch.setenv("BEAT_DENSITY", "off")
    assert BD.beat_density_enabled() is False


# --- Repair planning ----------------------------------------------------------

def _audit(**overrides):
    base = {
        "ok": False,
        "duration_s": 40.0,
        "longest_static": {"start": 10.0, "end": 30.0, "seconds": 20.0},
        "longest_static_s": 20.0,
    }
    base.update(overrides)
    return base


def test_plan_centres_the_punch_in_the_stretch():
    plan = BD.plan_beat_repair(_audit(), plain=False)
    assert plan["at"] == 19.0
    assert plan["seconds"] == BD.BEAT_PUNCH_SECONDS
    assert plan["zoom"] == BD.BEAT_PUNCH_ZOOM
    assert plan["stretch"]["seconds"] == 20.0


def test_plan_clamps_at_the_clip_end():
    plan = BD.plan_beat_repair(
        _audit(longest_static={"start": 36.0, "end": 40.0, "seconds": 4.0}),
        plain=False,
    )
    assert plan["at"] == 38.0 - BD.BEAT_PUNCH_SECONDS / 2
    assert plan["at"] + plan["seconds"] <= 40.0


def test_no_plan_when_passing_plain_or_short():
    assert BD.plan_beat_repair(_audit(ok=True), plain=False) is None
    assert BD.plan_beat_repair(_audit(), plain=True) is None
    assert BD.plan_beat_repair(_audit(duration_s=5.0), plain=False) is None
    assert BD.plan_beat_repair(None, plain=False) is None


# --- Real media ---------------------------------------------------------------

def _segment(path, source, seconds):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
         "-t", str(seconds), "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "26", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _clip_with_dead_stretch(path, action=6, dead=12, tail=6):
    """action | frozen slate | action, 540x960 @30fps, with a tone track."""
    work = path.parent
    a = _segment(work / "a.mp4", "testsrc2=size=540x960:rate=30", action)
    b = _segment(
        work / "b.mp4",
        "color=c=0x304050:size=540x960:rate=30,drawbox=x=120:y=400:w=300:h=120:c=white:t=6",
        dead,
    )
    c = _segment(work / "c.mp4", "testsrc2=size=540x960:rate=30", tail)
    lst = work / "list.txt"
    lst.write_text(f"file '{a.name}'\nfile '{b.name}'\nfile '{c.name}'\n")
    total = action + dead + tail
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={total}",
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
         "-shortest", str(path)],
        check=True,
    )
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_audit_finds_the_dead_stretch(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAT_DENSITY", "on")
    clip = _clip_with_dead_stretch(tmp_path / "clip.mp4")
    audit = BD.beat_density_for(clip)
    assert audit is not None
    assert audit["ok"] is False
    assert audit["longest_static_s"] == pytest.approx(12.0, abs=1.0)
    assert audit["longest_static"]["start"] == pytest.approx(6.0, abs=1.0)
    assert audit["longest_static"]["end"] == pytest.approx(18.0, abs=1.0)


def test_audit_off_returns_none_and_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("BEAT_DENSITY", raising=False)
    clip = _clip_with_dead_stretch(tmp_path / "clip.mp4")
    digest = _sha(clip)
    assert BD.beat_density_for(clip) is None
    assert BD.audit_and_repair_beat_density(clip, {"segments": [{}]}) is None
    assert _sha(clip) == digest


def test_repair_splits_the_dead_stretch(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAT_DENSITY", "on")
    monkeypatch.setattr(
        "src.media.edl_render.check_nvenc_available", lambda: False, raising=False
    )
    clip = _clip_with_dead_stretch(tmp_path / "clip.mp4")
    record = BD.audit_and_repair_beat_density(clip, {"segments": [{}]})
    assert record["repair"]["applied"] is True
    assert record["repair"]["at"] == pytest.approx(11.0, abs=1.5)
    after = record["after"]
    assert after is not None
    assert after["longest_static_s"] < record["audit"]["longest_static_s"] - 3.0
    assert after["beat_score"] > record["audit"]["beat_score"]


def test_plain_clip_is_audited_but_never_repaired(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAT_DENSITY", "on")
    clip = _clip_with_dead_stretch(tmp_path / "clip.mp4")
    digest = _sha(clip)
    record = BD.audit_and_repair_beat_density(clip, {"template": "plain"})
    assert record["audit"]["ok"] is False
    assert record["repair"]["applied"] is False
    assert "Plain" in record["repair"]["reason"]
    assert record["after"] is None
    assert _sha(clip) == digest


def test_audit_of_missing_file_is_none_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAT_DENSITY", "on")
    assert BD.beat_density_for(tmp_path / "nope.mp4") is None
    assert BD.audit_and_repair_beat_density(tmp_path / "nope.mp4", {}) is None


# --- QC surface ----------------------------------------------------------------

def _record(ok=True, applied=False):
    audit = {
        "ok": ok,
        "longest_static_s": 2.0 if ok else 12.0,
        "max_static_s": BD.BEAT_MAX_STATIC_S,
        "beat_score": 1.0 if ok else 0.71,
    }
    record = {"audit": audit, "repair": None, "after": None}
    if applied:
        record["repair"] = {"applied": True, "at": 11.0}
        record["after"] = {"longest_static_s": 5.5}
    elif not ok:
        record["repair"] = {"applied": False, "reason": "Plain — just cuts"}
    return record


def test_qc_check_states():
    from src.media.clip_qc import ClipQcContext, check_beat_density

    passed = check_beat_density(ClipQcContext(beat_density=_record(ok=True)))
    assert passed["name"] == "beat_density" and passed["status"] == "pass"

    warned = check_beat_density(ClipQcContext(beat_density=_record(ok=False)))
    assert warned["status"] == "warn"
    assert warned["evidence"]["longest_static_seconds"] == 12.0

    repaired = check_beat_density(
        ClipQcContext(beat_density=_record(ok=False, applied=True))
    )
    assert repaired["status"] == "warn"
    assert repaired["evidence"]["repair_applied"] is True
    assert repaired["evidence"]["longest_static_after_seconds"] == 5.5


def test_qc_report_only_carries_the_check_when_recorded(tmp_path):
    from src.media.clip_qc import ClipQcContext, run_clip_qc

    clip = _segment(tmp_path / "plain.mp4", "testsrc2=size=270x480:rate=30", 4)
    bare = run_clip_qc(clip, ClipQcContext(expected_duration=4.0))
    assert "beat_density" not in {c["name"] for c in bare["checks"]}
    with_record = run_clip_qc(
        clip, ClipQcContext(expected_duration=4.0, beat_density=_record(ok=True))
    )
    names = {c["name"] for c in with_record["checks"]}
    assert "beat_density" in names
