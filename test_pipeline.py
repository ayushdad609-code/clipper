#!/usr/bin/env python3
import os
import sys
import json
import subprocess
from subtitles import generate_ass_subtitles, check_subtitle_filter_support
from cutter import compute_crop_parameters, render_clip, get_video_metadata, filter_and_rank_candidates
from selector import extract_json_array, snap_to_segments, split_transcript_into_chunks

def test_crop_math():
    print("Testing crop math...")
    # Landscape 1920x1080 -> 9:16
    cw, ch, cx, cy = compute_crop_parameters(1920, 1080)
    assert ch == 1080
    assert abs(cw - int(1080 * 9 / 16)) <= 2
    assert cw % 2 == 0
    assert 0 <= cx <= (1920 - cw)
    print("Crop math passed.")

def test_json_extractor():
    print("Testing defensive JSON extraction...")
    # Markdown fences
    fenced = "```json\n[{\"start\": 10, \"end\": 35, \"score\": 8, \"title\": \"Test\", \"hook\": \"Hook\"}]\n```"
    res = extract_json_array(fenced)
    assert res is not None and len(res) == 1
    assert res[0]["title"] == "Test"

    # Trailing commas and surrounding text
    dirty = "Sure! Here is the output:\n[{\"start\": 5, \"end\": 25, \"score\": 7, \"title\": \"Trailing\", \"hook\": \"H\", },]\nHope you like it!"
    res2 = extract_json_array(dirty)
    assert res2 is not None and len(res2) == 1
    assert res2[0]["title"] == "Trailing"
    print("JSON extraction passed.")

def test_snapping():
    print("Testing boundary snapping...")
    segs = [
        {"start": 10.2, "end": 14.8, "text": "Hello"},
        {"start": 15.0, "end": 45.3, "text": "World"}
    ]
    s, e = snap_to_segments(10.0, 45.0, segs)
    assert s == 10.2
    assert e == 45.3
    print("Boundary snapping passed.")

def test_candidate_ranking_and_overlaps():
    print("Testing candidate ranking and overlap removal...")
    candidates = [
        {"start": 10.0, "end": 40.0, "score": 7, "title": "Clip A", "hook": "A"},
        {"start": 15.0, "end": 42.0, "score": 9, "title": "Clip B (Higher score, overlaps A)", "hook": "B"},
        {"start": 60.0, "end": 90.0, "score": 8, "title": "Clip C (No overlap)", "hook": "C"},
        {"start": 100.0, "end": 105.0, "score": 10, "title": "Too short", "hook": "D"}, # 5s duration < 20s
    ]
    selected = filter_and_rank_candidates(candidates, min_duration=20.0, max_duration=50.0, num_clips=2)
    assert len(selected) == 2
    assert selected[0]["title"] == "Clip B (Higher score, overlaps A)"
    assert selected[1]["title"] == "Clip C (No overlap)"
    print("Ranking and overlap removal passed.")

def test_full_render():
    print("Testing render and ffprobe output verification...")
    # Generate a 35s test video if needed
    test_src = "tmp_test_src.mp4"
    if not os.path.exists(test_src):
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=35:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=35",
            "-c:v", "libx264", "-c:a", "aac",
            test_src
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    meta = get_video_metadata(test_src)
    dummy_segments = [
        {
            "start": 5.0,
            "end": 30.0,
            "text": "This is a full pipeline verification test for short-form clips.",
            "words": [
                {"word": "This", "start": 5.0, "end": 5.5},
                {"word": "is", "start": 5.5, "end": 6.0},
                {"word": "a", "start": 6.0, "end": 6.3},
                {"word": "full", "start": 6.3, "end": 7.0},
                {"word": "pipeline", "start": 7.0, "end": 8.0},
                {"word": "verification", "start": 8.0, "end": 9.5},
                {"word": "test.", "start": 9.5, "end": 11.0}
            ]
        }
    ]
    cand = {"start": 5.0, "end": 30.0, "score": 9.5, "title": "Integration Test Clip", "hook": "This is a full pipeline"}
    out_clip = "output/integration_test_clip.mp4"
    render_clip(test_src, cand, dummy_segments, out_clip, meta)

    # Verify with ffprobe
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height,duration",
        "-of", "json",
        out_clip
    ]
    probe_data = json.loads(subprocess.check_output(probe_cmd).decode("utf-8"))
    streams = probe_data.get("streams", [])
    v_stream = next((s for s in streams if s["codec_type"] == "video"), None)
    a_stream = next((s for s in streams if s["codec_type"] == "audio"), None)

    assert v_stream is not None, "Missing video stream"
    assert a_stream is not None, "Missing audio stream"
    assert v_stream["width"] == 720, f"Expected width 720, got {v_stream['width']}"
    assert v_stream["height"] == 1280, f"Expected height 1280, got {v_stream['height']}"

    dur = float(v_stream["duration"])
    assert 20.0 <= dur <= 50.0, f"Duration {dur}s outside expected 20-50s range"

    # Cleanup test src
    if os.path.exists(test_src):
        os.remove(test_src)

    print(f"Full render passed! Clip duration: {dur:.2f}s, Resolution: 720x1280, Audio: OK.")

if __name__ == "__main__":
    test_crop_math()
    test_json_extractor()
    test_snapping()
    test_candidate_ranking_and_overlaps()
    test_full_render()
    print("\n✅ ALL PIPELINE TESTS PASSED!")
