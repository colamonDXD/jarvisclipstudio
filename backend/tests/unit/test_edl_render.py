"""Unit tests for EDL rendering module."""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.media.edl_render import (
    check_nvenc_available,
    get_video_encoder,
    TRANSITION_MAP,
    build_transition_filter,
    build_kenburns_filter,
    build_punch_in_filter,
    build_drawtext_filter,
    build_overlay_filter,
    build_edl_filter_graph,
)
from src.auto_director.edl import load_profile, build_edl


def test_check_nvenc_available():
    """Test NVENC availability check runs without error."""
    # Just verify it runs and returns a boolean
    result = check_nvenc_available()
    assert isinstance(result, bool)
    print(f"NVENC available: {result}")


def test_get_video_encoder():
    """Test video encoder selection."""
    encoder, args = get_video_encoder(use_nvenc=False)
    assert encoder == "libx264"
    assert "-c:v" in args
    assert "libx264" in args

    # Test with nvenc (will fall back to libx264 if not available)
    encoder, args = get_video_encoder(use_nvenc=True)
    assert encoder in ("h264_nvenc", "libx264")
    assert "-c:v" in args
    print(f"Selected encoder: {encoder}")


def test_transition_map():
    """Test transition type mapping."""
    assert TRANSITION_MAP["hard_cut"] is None
    assert TRANSITION_MAP["cut"] is None
    assert TRANSITION_MAP["cross_dissolve"] == "fade"
    assert TRANSITION_MAP["fade"] == "fade"
    assert TRANSITION_MAP["slide"] == "slideleft"


def test_build_transition_filter_hard_cut():
    """Test hard cut transition filter."""
    filter_str, out_label = build_transition_filter(
        "hard_cut", 0.05, 5.0, "[v0]", "[v1]", "vx1"
    )
    assert "concat=n=2:v=1:a=0" in filter_str
    assert "xfade" not in filter_str
    assert out_label == "[vx1]"


def test_build_transition_filter_crossfade():
    """Test crossfade transition filter."""
    filter_str, out_label = build_transition_filter(
        "cross_dissolve", 0.8, 5.0, "[v0]", "[v1]", "vx1"
    )
    assert "xfade=transition=fade:duration=0.800000:offset=5.000000" in filter_str
    assert out_label == "[vx1]"


def test_build_kenburns_filter():
    """Test Ken Burns filter construction."""
    filter_str = build_kenburns_filter(
        0.0, 10.0, 1.0, 1.05, "[in]", "[out]"
    )
    assert "zoompan" in filter_str
    # Check the zoom expression is constructed correctly
    assert "z='if(isnan(it)" in filter_str
    assert "1.0" in filter_str
    assert "1.05" in filter_str
    assert "min(it/10.000,1)" in filter_str
    assert "[out]" in filter_str


def test_build_punch_in_filter():
    """Test punch-in filter construction."""
    filter_str = build_punch_in_filter(
        5.0, 1.25, 0.5, "[in]", "[out]"
    )
    assert "zoompan" in filter_str
    assert "z='if(lt(t,5.000),1,if(lt(t,5.500),1.25,1))" in filter_str
    assert "[out]" in filter_str


def test_build_drawtext_filter():
    """Test drawtext filter for info cards."""
    filter_str = build_drawtext_filter(
        "42 percent growth", 1.0, 4.0, "[in]", "[out]"
    )
    assert "drawtext" in filter_str
    assert "text='42 percent growth'" in filter_str
    assert "enable='between(t\\,1.000\\,4.000)'" in filter_str
    assert "[out]" in filter_str


def test_build_overlay_filter():
    """Test overlay filter for reference images."""
    input_args, filter_str = build_overlay_filter(
        Path("/tmp/test.png"), 2.0, 5.0, "[in]", "[out]"
    )
    assert "-i" in input_args
    # Path may be converted on Windows, check for the filename
    assert any("test.png" in arg for arg in input_args)
    assert "overlay" in filter_str
    assert "enable='between(t\\,2.000\\,5.000)'" in filter_str
    assert "[out]" in filter_str


def test_build_edl_filter_graph_podcast():
    """Test EDL filter graph for podcast profile (hard cuts + punch-in)."""
    profile = load_profile("podcast_news")

    clip = {"id": "clip1", "start": 0, "end": 30, "duration": 30}
    transcript_words = [
        {"word": "The", "start": 0.5, "end": 0.6},
        {"word": "truth", "start": 0.6, "end": 0.8},
        {"word": "is", "start": 0.8, "end": 0.9},
        {"word": "we", "start": 0.9, "end": 1.0},
        {"word": "need", "start": 1.0, "end": 1.2},
        {"word": "42", "start": 1.2, "end": 1.4},
        {"word": "percent", "start": 1.4, "end": 1.7},
        {"word": "growth", "start": 1.7, "end": 2.0},
    ]
    energy_peaks = [2.5, 10.0, 20.0]

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    # Build filter graph
    extra_inputs, filter_complex, encoder_args, map_label = build_edl_filter_graph(
        edl, Path("/tmp/dummy.mp4")
    )

    # Verify filter graph contains expected elements
    assert "trim=" in filter_complex
    assert "setpts=PTS-STARTPTS" in filter_complex
    assert "concat=n=2:v=1:a=0" in filter_complex or "xfade" in filter_complex
    assert "zoompan" in filter_complex  # punch-in keyframes
    assert "drawtext" in filter_complex  # info card for "42 percent"
    assert map_label.startswith("[v")
    assert "-c:v" in encoder_args

    print("Podcast EDL filter graph:")
    print(filter_complex[:500] + "...")


def test_build_edl_filter_graph_travel():
    """Test EDL filter graph for travel profile (fade + ken_burns)."""
    profile = load_profile("travel_vlog")

    clip = {"id": "clip2", "start": 0, "end": 60, "duration": 60}
    transcript_words = [
        {"word": "Tokyo", "start": 1.0, "end": 1.3},
        {"word": "is", "start": 1.3, "end": 1.4},
        {"word": "beautiful", "start": 1.4, "end": 1.8},
    ]
    energy_peaks = [15.0, 30.0]

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    extra_inputs, filter_complex, encoder_args, map_label = build_edl_filter_graph(
        edl, Path("/tmp/dummy.mp4")
    )

    # Verify filter graph contains expected elements
    assert "trim=" in filter_complex
    assert "xfade=transition=fade" in filter_complex  # cross_dissolve -> fade
    assert "zoompan" in filter_complex  # ken_burns + punch-in
    assert "duration=0.800000" in filter_complex  # 0.8s fade duration
    assert map_label.startswith("[v")

    print("Travel EDL filter graph:")
    print(filter_complex[:500] + "...")


def test_build_edl_filter_graph_no_overlays():
    """Test EDL filter graph with no overlays (meme_comedy)."""
    profile = load_profile("meme_comedy")

    clip = {"id": "clip3", "start": 0, "end": 15, "duration": 15}
    transcript_words = [{"word": "funny", "start": 0, "end": 0.5}]
    energy_peaks = []

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    extra_inputs, filter_complex, encoder_args, map_label = build_edl_filter_graph(
        edl, Path("/tmp/dummy.mp4")
    )

    # No info cards, no reference images
    assert "drawtext" not in filter_complex or "info_card" not in str(edl.get("overlays", []))
    assert "overlay" not in filter_complex or "reference_image" not in str(edl.get("overlays", []))

    print("Meme comedy EDL filter graph (no overlays):")
    print(filter_complex[:500] + "...")


def test_no_edl_regression():
    """Test that rendering without EDL is not affected (regression test).

    This test verifies the EDL rendering module can be imported and used
    without affecting the existing render_reframed_clip_ffmpeg function.
    """
    try:
        from src.media.reframing import render_reframed_clip_ffmpeg
        # Verify the function exists and is callable
        assert callable(render_reframed_clip_ffmpeg)
        print("Regression test passed: render_reframed_clip_ffmpeg still accessible")
    except ImportError as e:
        if "fcntl" in str(e):
            # Expected on Windows - fcntl is Unix-only
            print("Regression test skipped on Windows (fcntl not available)")
            return
        raise


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        ("check_nvenc_available", test_check_nvenc_available),
        ("get_video_encoder", test_get_video_encoder),
        ("transition_map", test_transition_map),
        ("build_transition_filter_hard_cut", test_build_transition_filter_hard_cut),
        ("build_transition_filter_crossfade", test_build_transition_filter_crossfade),
        ("build_kenburns_filter", test_build_kenburns_filter),
        ("build_punch_in_filter", test_build_punch_in_filter),
        ("build_drawtext_filter", test_build_drawtext_filter),
        ("build_overlay_filter", test_build_overlay_filter),
        ("build_edl_filter_graph_podcast", test_build_edl_filter_graph_podcast),
        ("build_edl_filter_graph_travel", test_build_edl_filter_graph_travel),
        ("build_edl_filter_graph_no_overlays", test_build_edl_filter_graph_no_overlays),
        ("no_edl_regression", test_no_edl_regression),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n--- Results: {passed} passed, {failed} failed ---")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)