import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from src import font_registry

CHECKER_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "assets" / "check_fonts.py"
)
_spec = importlib.util.spec_from_file_location("check_fonts_tool", CHECKER_PATH)
assert _spec is not None and _spec.loader is not None
check_fonts_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_fonts_module)

FONTS_DIR = Path(__file__).resolve().parents[2] / "fonts"
MANIFEST_PATH = FONTS_DIR / "FONT_MANIFEST.json"
NEW_FONT_STEMS = {
    "LuckiestGuy-Regular",
    "LilitaOne-Regular",
    "TitanOne-Regular",
    "Bungee-Regular",
    "PermanentMarker-Regular",
    "PaytoneOne-Regular",
    "PassionOne-Regular",
    "RubikMonoOne-Regular",
    "Chewy-Regular",
    "Fredoka-Variable",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_font(fonts_dir: Path, name: str, data: bytes) -> Path:
    path = fonts_dir / name
    path.write_bytes(data)
    return path


def _entry(name: str, data: bytes, license_name: str = "OFL-1.1") -> dict:
    return {
        "file": name,
        "sha256": _sha256(data),
        "family": name.removesuffix(".ttf"),
        "license": license_name,
        "source_url": "https://example.invalid/font",
    }


def _write_manifest(fonts_dir: Path, entries: list[dict]) -> Path:
    path = fonts_dir / "FONT_MANIFEST.json"
    path.write_text(
        json.dumps({"source": "test", "fonts": entries}), encoding="utf-8"
    )
    return path


def test_checker_passes_on_a_clean_folder(tmp_path):
    data = b"clean-font"
    _write_font(tmp_path, "Clean.ttf", data)
    _write_manifest(tmp_path, [_entry("Clean.ttf", data)])

    assert check_fonts_module.check_fonts(tmp_path) == []


def test_checker_fails_on_a_missing_entry(tmp_path):
    first = b"entry-one"
    second = b"entry-two"
    _write_font(tmp_path, "One.ttf", first)
    _write_font(tmp_path, "Two.ttf", second)
    _write_manifest(tmp_path, [_entry("One.ttf", first)])

    problems = check_fonts_module.check_fonts(tmp_path)

    assert any("missing manifest entry: Two.ttf" in problem for problem in problems)
    assert all("One.ttf" not in problem for problem in problems)


def test_checker_fails_on_a_wrong_licence(tmp_path):
    data = b"unverified-font"
    _write_font(tmp_path, "Mystery.ttf", data)
    _write_manifest(tmp_path, [_entry("Mystery.ttf", data, "UNVERIFIED")])

    problems = check_fonts_module.check_fonts(tmp_path)

    assert any(
        "licence not allowed: Mystery.ttf is UNVERIFIED" in problem
        for problem in problems
    )


def test_checker_fails_on_an_altered_file(tmp_path):
    original = b"original-bytes"
    altered = b"tampered-bytes"
    font = _write_font(tmp_path, "Altered.ttf", original)
    _write_manifest(tmp_path, [_entry("Altered.ttf", original)])
    font.write_bytes(altered)

    problems = check_fonts_module.check_fonts(tmp_path)

    assert any("sha256 mismatch: Altered.ttf" in problem for problem in problems)


def test_checker_cli_exits_one_on_a_problem(tmp_path):
    data = b"cli-font"
    font = _write_font(tmp_path, "Cli.ttf", data)
    _write_manifest(tmp_path, [_entry("Cli.ttf", data)])

    clean = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--fonts-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr

    font.write_bytes(b"changed")
    broken = subprocess.run(
        [sys.executable, str(CHECKER_PATH), "--fonts-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert broken.returncode == 1
    assert "PROBLEM: sha256 mismatch: Cli.ttf" in broken.stdout


def test_bundled_manifest_covers_every_font_and_flags_only_the_known_gap():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = manifest["fonts"]
    bundled = {
        path.name
        for path in FONTS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".ttf", ".otf"}
    }

    assert {entry["file"] for entry in entries} == bundled
    # 35 before THEBOLDFONT.ttf was dropped (licence UNVERIFIED, Sanji
    # 2026-11-18): every bundled font is now provably OFL or Apache.
    assert len(entries) == 34

    unverified = {
        entry["file"]
        for entry in entries
        if entry["license"] not in {"OFL-1.1", "Apache-2.0"}
    }
    assert unverified == set()
    assert "THEBOLDFONT.ttf" not in bundled

    problems = check_fonts_module.check_fonts(FONTS_DIR, MANIFEST_PATH)
    assert problems == []


async def test_get_fonts_lists_every_new_display_font(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(font_registry, "get_font_languages", lambda _path: [])

    response = await client.get("/fonts", headers=auth_headers)

    assert response.status_code == 200
    names = {font["name"] for font in response.json()["fonts"]}
    assert NEW_FONT_STEMS <= names
    assert len(names) >= 34  # 35 before THEBOLDFONT.ttf was dropped (licence UNVERIFIED)
