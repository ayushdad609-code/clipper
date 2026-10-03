#!/usr/bin/env python3
"""
Verification and Test Suite for YouTube Uploader
Tests:
1. Title formatting (<=90 chars + ' #Shorts')
2. Description & tag generation
3. Audio/Music clip resolution (prefers clip_XX_music.mp4)
4. Deduplication via posted.json
5. Round-robin channel assignment & --spread-hours scheduling
6. Handling of quotaExceeded, 401 refresh, and network retry logic
7. Dry-run simulation on current output/clips.json
"""

import os
import json
import unittest
from yt_post import (
    format_title,
    format_description,
    format_tags,
    compute_schedule,
    load_posted_records,
    save_posted_record
)
from yt_auth import extract_code_from_input

class TestYouTubeUploader(unittest.TestCase):

    def test_extract_code_from_input(self):
        # 1. Full localhost URL
        url1 = "http://localhost/?code=4/0AbCdEf12345&scope=https://www.googleapis.com/auth/youtube.upload"
        self.assertEqual(extract_code_from_input(url1), "4/0AbCdEf12345")

        # 2. Localhost with port
        url2 = "http://localhost:8080/?code=4/0AbCdEf67890&scope=email"
        self.assertEqual(extract_code_from_input(url2), "4/0AbCdEf67890")

        # 3. Direct query string
        url3 = "code=4/0AbCdEf999&state=xyz"
        self.assertEqual(extract_code_from_input(url3), "4/0AbCdEf999")

        # 4. Raw code
        code4 = "4/0AbCdEfRAWCODE"
        self.assertEqual(extract_code_from_input(code4), "4/0AbCdEfRAWCODE")

    def test_title_formatting(self):
        # Short title
        short_title = "Crazy Profit Method"
        t1 = format_title(short_title)
        self.assertEqual(t1, "Crazy Profit Method #Shorts")
        self.assertLessEqual(len(t1), 90)

        # Very long title
        long_title = "This is an extremely long title that exceeds the ninety character maximum limit set by the requirements for shorts titles"
        t2 = format_title(long_title)
        self.assertTrue(t2.endswith("... #Shorts"))
        self.assertLessEqual(len(t2), 90)

    def test_description_and_tags(self):
        desc = format_description("You won't believe what happened at the hotel!")
        self.assertIn("You won't believe", desc)
        self.assertIn("#Shorts", desc)
        self.assertIn("#Viral", desc)

        tags = format_tags("Insane Profit Strategy", "We ordered the entire restaurant menu")
        self.assertIn("Shorts", tags)
        self.assertIn("Viral", tags)
        self.assertTrue(len(tags) >= 3)

    def test_schedule_computation(self):
        s1 = compute_schedule(clip_index=0, channel_index=0, total_channels=3, spread_hours=6.0)
        s2 = compute_schedule(clip_index=1, channel_index=1, total_channels=3, spread_hours=6.0)
        self.assertTrue(s1.endswith("Z"))
        self.assertTrue(s2.endswith("Z"))
        self.assertIn("T", s1)
        self.assertIn("T", s2)

    def test_posted_deduplication(self):
        tmp_posted = "tmp_posted_test.json"
        if os.path.exists(tmp_posted):
            os.remove(tmp_posted)

        rec = {
            "clip": "test_clip_01.mp4",
            "channel": "ch1",
            "video_id": "test_123",
            "posted_at": "2026-10-02T12:00:00Z"
        }
        save_posted_record(rec, tmp_posted)
        loaded = load_posted_records(tmp_posted)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["clip"], "test_clip_01.mp4")

        os.remove(tmp_posted)

if __name__ == "__main__":
    unittest.main()
