"""Offline checks for equal grids, staggered reveals and a genuinely still hold."""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dog_post_maker.animated_collage import AnimatedFrames, grid_layout, title_tile


class AnimatedCollageTests(unittest.TestCase):
    def test_equal_tiles_fill_feed_format(self):
        for size, expected in [((1080, 1350), (536, 671))]:
            tile, positions = grid_layout(size)
            self.assertEqual(tile, expected)
            self.assertEqual(positions[3], (tile[0] + 8, tile[1] + 8))
            self.assertEqual(positions[3][0] + tile[0], size[0])
            self.assertEqual(positions[3][1] + tile[1], size[1])

    def test_wave_before_text(self):
        early = np.asarray(title_tile("Azra in NL", (536, 671), .7))
        complete = np.asarray(title_tile("Azra in NL", (536, 671), 1.5))
        self.assertTrue(np.all(early[:220] == 255))
        self.assertFalse(np.all(complete[:220] == 255))
        self.assertFalse(np.all(early[400:] == 255))

    def test_reveal_order_and_three_second_freeze(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            chosen, scores = [], []
            for index, color in enumerate(("red", "green", "orange")):
                name = f"{index}.png"
                Image.new("RGB", (640, 960), color).save(folder / name)
                chosen.append(dict(file=name, role="full_body"))
                scores.append(dict(file=name, width=640, height=960, pose="front",
                                   head_box=dict(left=.3, top=.2, right=.6, bottom=.4),
                                   dog_box=dict(left=.2, top=.2, right=.8, bottom=.9)))
            frames = AnimatedFrames(folder, {"selection": {"chosen": chosen}, "scores": {"photos": scores}},
                                    {"clips": []}, (1080, 1350), "Azra in NL")
            try:
                before, first = frames.frame_at(1.5), frames.frame_at(2.3)
                self.assertFalse(np.array_equal(before[100, 800], first[100, 800]))
                self.assertTrue(np.array_equal(first[1000, 100], first[1000, 800]))
                second = frames.frame_at(3.2)
                self.assertFalse(np.array_equal(second[1000, 100], second[1000, 800]))
                self.assertTrue(np.array_equal(frames.frame_at(10.5), frames.frame_at(13.49)))
                self.assertFalse(np.array_equal(frames.frame_at(13.49), frames.frame_at(13.5)))
            finally:
                frames.close()


if __name__ == "__main__":
    unittest.main()
