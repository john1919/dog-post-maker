"""Offline checks for source bounds, aspect-ratio crop safety and timing."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dog_post_maker.video import candidate_segments, photo_head_box, select_video_segments, viewport


class VideoTests(unittest.TestCase):
    def test_candidate_sampling_stays_in_source(self):
        for duration in (.63, 2, 3, 9.8, 24.97):
            candidates = candidate_segments(duration)
            self.assertLessEqual(sum(len(c["sample_times"]) for c in candidates), 6)
            for candidate in candidates:
                self.assertTrue(0 <= candidate["start"] < candidate["end"] <= duration)
                self.assertTrue(all(candidate["start"] <= t < candidate["end"] for t in candidate["sample_times"]))

    def test_portrait_crop_retains_head_without_bars(self):
        for size in ((536, 671), (1080, 1350)):
            for head in [dict(left=.6, top=.2, right=.8, bottom=.5),
                         dict(left=.0, top=.0, right=.2, bottom=.2),
                         dict(left=.8, top=.7, right=1., bottom=1.)]:
                for zoom in (1, 1.04, 1.16):
                    l, t, r, b = viewport(1536, 2048, head, size, zoom=zoom)
                    self.assertAlmostEqual((r - l) / (b - t), size[0] / size[1])
                    self.assertTrue(0 <= l <= head["left"] * 1536)
                    self.assertTrue(head["right"] * 1536 <= r <= 1536 + 1e-6)
                    self.assertTrue(0 <= t <= head["top"] * 2048)
                    self.assertTrue(head["bottom"] * 2048 <= b <= 2048 + 1e-6)

    def test_impossible_crop_stops(self):
        with self.assertRaises(ValueError):
            viewport(1920, 1080, dict(left=0, top=.2, right=1, bottom=.8), (1080, 1350))

    def test_side_photo_crop_protects_muzzle_end_of_body(self):
        score = dict(pose="side", head_box=dict(left=.62, top=.35, right=.82, bottom=.58),
                     dog_box=dict(left=.18, top=.38, right=.89, bottom=.87))
        head = photo_head_box(score)
        self.assertEqual(head["right"], .89)
        for size in ((536, 671), (1080, 1350)):
            for zoom in (1, 1.04):
                crop = viewport(1984, 1692, head, size, zoom=zoom)
                self.assertGreaterEqual(crop[2], .89 * 1984)

    def test_no_source_videos_needs_no_ai_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            result=select_video_segments([],folder,folder/'.env')
            self.assertEqual(result['clips'],[])
            self.assertEqual(result['cost_usd'],0)
            self.assertEqual(select_video_segments([],folder,folder/'.env'),result)


if __name__ == "__main__":
    unittest.main()
