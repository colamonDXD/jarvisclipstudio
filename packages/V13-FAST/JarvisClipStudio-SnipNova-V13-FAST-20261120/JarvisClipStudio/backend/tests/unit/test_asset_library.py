"""Variant pools for music moods / SFX cues and the shipped-asset licence manifest."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.media import asset_library as al
from src.media.edl_render import _resolve_asset_file


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture(autouse=True)
def _variants_on(monkeypatch):
    monkeypatch.delenv("ASSET_VARIANTS", raising=False)


def test_pool_is_stem_plus_library_folder(tmp_path):
    stem = _touch(tmp_path / "comedy.mp3")
    a = _touch(tmp_path / "library" / "comedy" / "a.mp3")
    b = _touch(tmp_path / "library" / "comedy" / "b.mp3")
    _touch(tmp_path / "library" / "comedy" / "notes.txt")
    assert al.variant_pool(tmp_path, "comedy") == [stem, a, b]


def test_pick_is_stable_per_seed_and_varies_across_seeds(tmp_path):
    for i in range(6):
        _touch(tmp_path / "library" / "whoosh" / f"w{i}.wav")
    first = al.pick_variant(tmp_path, "whoosh", "clip-1|sfx|0")
    assert first == al.pick_variant(tmp_path, "whoosh", "clip-1|sfx|0")
    picks = {al.pick_variant(tmp_path, "whoosh", f"clip-{n}|sfx|0") for n in range(40)}
    assert len(picks) > 1


def test_pack_folder_replaces_generic_pool(tmp_path):
    _touch(tmp_path / "impact.wav")
    _touch(tmp_path / "library" / "impact" / "generic.wav")
    boom = _touch(tmp_path / "library" / "meme" / "impact" / "boom.wav")
    assert al.variant_pool(tmp_path, "impact", pack="meme") == [boom]
    # An empty / missing pack falls back to the generic pool.
    assert len(al.variant_pool(tmp_path, "impact", pack="nope")) == 2


def test_off_switch_restores_stem_only(tmp_path, monkeypatch):
    stem = _touch(tmp_path / "comedy.mp3")
    _touch(tmp_path / "library" / "comedy" / "a.mp3")
    _touch(tmp_path / "library" / "epic" / "e.mp3")
    monkeypatch.setenv("ASSET_VARIANTS", "off")
    assert al.pick_variant(tmp_path, "comedy", "s") is None
    assert _resolve_asset_file(tmp_path, "comedy") == stem
    assert _resolve_asset_file(tmp_path, "epic") is None


def test_folder_only_mood_resolves_when_on(tmp_path):
    e = _touch(tmp_path / "library" / "epic" / "e.mp3")
    assert _resolve_asset_file(tmp_path, "epic") == e


def test_explicit_filenames_and_paths_never_use_variants(tmp_path):
    _touch(tmp_path / "library" / "x.mp3" / "a.mp3")
    assert al.variant_pool(tmp_path, "x.mp3") == []
    assert al.variant_pool(tmp_path, "../etc") == []
    assert al.variant_pool(tmp_path, "a/b") == []


def _manifest(tmp_path: Path, files: dict) -> None:
    (tmp_path / al.MANIFEST_FILENAME).write_text(json.dumps({"files": files}), encoding="utf-8")


def test_manifest_clean_library_passes(tmp_path):
    f = _touch(tmp_path / "sfx" / "library" / "pop" / "p.wav", b"pop")
    _manifest(tmp_path, {
        "sfx/library/pop/p.wav": al.manifest_entry(f, license="CC0-1.0", source="kenney"),
    })
    assert al.check_manifest(tmp_path) == []


def test_manifest_flags_missing_wrong_licence_and_altered(tmp_path):
    good = _touch(tmp_path / "music" / "a.mp3", b"a")
    altered = _touch(tmp_path / "music" / "b.mp3", b"b")
    _touch(tmp_path / "sfx" / "c.wav", b"c")
    entries = {
        "music/a.mp3": al.manifest_entry(good, license="CC-BY-NC-4.0", source="somewhere"),
        "music/b.mp3": al.manifest_entry(altered, license="CC0-1.0", source="freepd"),
    }
    _manifest(tmp_path, entries)
    altered.write_bytes(b"changed")
    problems = "\n".join(al.check_manifest(tmp_path))
    assert "music/a.mp3: licence 'CC-BY-NC-4.0' not allowed" in problems
    assert "music/b.mp3: sha256 does not match" in problems
    assert "sfx/c.wav: no manifest entry" in problems


def test_edl_carries_profile_sfx_pack():
    import json as _json
    from src.auto_director.edl import build_edl

    profile = _json.loads(
        (Path(__file__).resolve().parents[2] / "src" / "auto_director" / "profiles" / "meme_comedy.json")
        .read_text(encoding="utf-8")
    )
    edl = build_edl({"id": "c1", "start": 0.0, "end": 20.0, "duration": 20.0}, [], profile, [])
    assert edl["audio"]["sfx_pack"] == "meme"
