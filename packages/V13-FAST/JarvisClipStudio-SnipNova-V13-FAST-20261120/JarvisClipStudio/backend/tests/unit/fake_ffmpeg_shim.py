"""A PATH shim for ffmpeg/ffprobe whose behaviour is driven by a file.

Installs two executable scripts named ``ffmpeg`` and ``ffprobe`` into a
directory the test puts first on PATH, so code that shells out by bare
name (everything in src.media, via run_ffmpeg_command) talks to the shim.
The shim appends every invocation (full argv, JSON) to ``calls.log`` in
the state dir and reads its orders from the ``scenario`` file there, so a
test can also change the scenario mid-flight.

On Windows a bare name is resolved by CreateProcess, which only appends
``.exe`` -- a shebang script named ``ffmpeg`` is never found, so those
tests used the real ffmpeg by accident. When pip's vendored distlib is
available the installer also writes ``ffmpeg.exe``/``ffprobe.exe``
launchers around the same source, and the tests exercise the shim as
written. Without pip the scripts are still installed and the suite keeps
its pre-existing Windows behaviour.

Scenarios:
- ``ok``: everything succeeds; ffprobe reports a valid probe clip.
- ``no_listing``: the encoder list does not mention h264_nvenc.
- ``oneframe_fail``: the one-frame probe encode exits 1.
- ``realistic_fail``: the realistic probe encode exits 1 with driver
  stderr ("Cannot load libnvidia-encode.so.1").
- ``probe_bad_output``: the realistic encode "succeeds" but ffprobe
  reports the wrong geometry.
- ``nvenc_render_fail``: production renders fail on h264_nvenc with an
  OpenEncodeSessionEx error and succeed on libx264.
- ``nvenc_render_fail_cpu_fail``: production renders fail on both
  encoders -- NVENC with an encode-session error, libx264 with a
  marker-free error.
- ``filter_fail``: production renders fail on any encoder with a
  filter-graph error that names no encoder.
"""

import io
import json
import os
import platform
import sys
import zipfile
from pathlib import Path

SHIM_SOURCE = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = Path(os.environ["FAKE_FFMPEG_STATE_DIR"])
with (state / "calls.log").open("a") as fh:
    fh.write(json.dumps(sys.argv) + "\n")
scenario = (state / "scenario").read_text().strip()
tool = Path(sys.argv[0]).name
if tool.lower().endswith(".exe"):
    tool = tool[:-4]
args = sys.argv[1:]

if tool == "ffprobe":
    if scenario == "probe_bad_output":
        payload = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360}
            ],
            "format": {"duration": "5.600"},
        }
    else:
        payload = {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "5.600"},
        }
    print(json.dumps(payload))
    sys.exit(0)

# ffmpeg from here on.
if "-encoders" in args:
    if scenario == "no_listing":
        print(" V..... libx264  libx264 H.264 / AVC encoder")
    else:
        print(" V..... h264_nvenc  NVIDIA NVENC H.264 encoder")
    sys.exit(0)

if any("color=size=256x256" in arg for arg in args):
    # The one-frame probe pre-filter.
    if scenario == "oneframe_fail":
        print("one-frame probe encode exploded", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)

if any(arg.endswith("nvenc_probe.mp4") for arg in args):
    # The realistic probe encode. The probe now passes its graph inline
    # (-filter_complex, as production does) instead of with
    # -filter_complex_script, which FFmpeg 8-era builds removed.
    if scenario == "realistic_fail":
        print("Cannot load libnvidia-encode.so.1", file=sys.stderr)
        sys.exit(1)
    Path(args[-1]).touch()
    sys.exit(0)

# Anything else is a production render.
if scenario == "nvenc_render_fail" and "h264_nvenc" in args:
    print("[nvenc @ 0x55] OpenEncodeSessionEx failed: unsupported device", file=sys.stderr)
    sys.exit(1)
if scenario == "nvenc_render_fail_cpu_fail":
    if "h264_nvenc" in args:
        print("OpenEncodeSessionEx failed: unsupported device", file=sys.stderr)
    else:
        print("encoder exploded for unrelated reasons", file=sys.stderr)
    sys.exit(1)
if scenario == "filter_fail":
    print("No such filter: 'bogusfilter'", file=sys.stderr)
    sys.exit(1)
Path(args[-1]).touch()
sys.exit(0)
"""


def _windows_launcher(bin_dir: Path, name: str) -> None:
    """Add a bare-name-resolvable ``<name>.exe`` for the same shim.

    CreateProcess appends only ``.exe``, so a shebang file called
    ``ffmpeg`` is invisible to ``subprocess.run(["ffmpeg", ...])`` on
    Windows. pip's vendored distlib ships the console-script launcher and
    the zipapp format: launcher + shebang + zip(__main__.py). If pip is
    not installed this is a no-op and the suite behaves as it did before.
    """
    try:
        from pip._vendor import distlib
    except ImportError:
        return
    machine = platform.machine().lower()
    launcher_name = "t64-arm.exe" if machine in ("arm64", "aarch64") else "t64.exe"
    launcher = Path(distlib.__file__).parent / launcher_name
    if not launcher.is_file():
        return
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("__main__.py", SHIM_SOURCE)
    shebang = ("#!" + sys.executable + "\n").encode()
    (bin_dir / f"{name}.exe").write_bytes(
        launcher.read_bytes() + shebang + payload.getvalue()
    )


def install_fake_ffmpeg(tmp_path: Path, scenario: str, monkeypatch) -> Path:
    """Write the shim, put it first on PATH, and return the state dir."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    state_dir = tmp_path / "fake-ffmpeg-state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "scenario").write_text(scenario)
    for name in ("ffmpeg", "ffprobe"):
        script = bin_dir / name
        script.write_text(SHIM_SOURCE)
        script.chmod(0o755)
        if os.name == "nt":
            _windows_launcher(bin_dir, name)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_FFMPEG_STATE_DIR", str(state_dir))
    return state_dir


def tool_name(call: list) -> str:
    """The shim program a logged call ran (``ffmpeg``, not ``ffmpeg.exe``)."""
    name = Path(call[0]).name
    return name[:-4] if name.lower().endswith(".exe") else name


def set_scenario(state_dir: Path, scenario: str) -> None:
    (Path(state_dir) / "scenario").write_text(scenario)


def read_calls(state_dir: Path) -> list:
    """Every shim invocation so far, as argv lists, oldest first."""
    log = Path(state_dir) / "calls.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def ffmpeg_calls(state_dir: Path) -> list:
    return [c for c in read_calls(state_dir) if tool_name(c) == "ffmpeg"]


def is_probe_call(call: list) -> bool:
    """The realistic NVENC probe, identified by its output name.

    The probe passes its filter graph inline now, so an output-name match
    is the only stable marker; production renders never use this name.
    """
    return bool(call) and Path(call[-1]).name == "nvenc_probe.mp4"


def render_calls(state_dir: Path) -> list:
    """Production renders only (not probes): ffmpeg with -filter_complex."""
    return [
        c
        for c in ffmpeg_calls(state_dir)
        if "-filter_complex" in c and not is_probe_call(c)
    ]
