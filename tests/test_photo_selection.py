"""Offline checks for crop safety, variety and the four-photo threshold."""

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dog_post_maker.photo_selection import Box, choose_photos, square_crop
from dog_post_maker.photo_selection import _score_collection


def photo(index, pose="front", group=None, head=None, body=None):
    return dict(id=index, file=f"{index}.jpg", width=1200, height=1600,
                sharpness=8, dog_visibility=8, face_visibility=9, light=8,
                quality=8.25, usable=True, head_complete=True, full_body_visible=True,
                reason="Clear dog and face.",
                pose=pose, similarity_group=group or pose,
                head_box=head or dict(left=.4, top=.1, right=.6, bottom=.3),
                dog_box=body or dict(left=.2, top=.1, right=.8, bottom=.9))


class PhotoSelectionTests(unittest.TestCase):
    def test_large_collection_uses_small_batches_and_unique_ids(self):
        paths=[Path(f'{i}.jpg') for i in range(19)]
        def score(chunk,env_path,**kwargs):
            self.assertTrue(kwargs['_single'])
            records=[dict(photo(i),file=path.name) for i,path in enumerate(chunk)]
            return dict(model='test',photos=records,local_rejections=[],usage={'input_tokens':len(chunk)})
        with patch('dog_post_maker.photo_selection.score_photos',side_effect=score) as calls,patch('dog_post_maker.photo_selection._refine_scores',side_effect=lambda result,*args:result):
            result=_score_collection(paths,Path('.env'),None)
        self.assertEqual([len(call.args[0]) for call in calls.call_args_list],[8,8,3])
        self.assertEqual([p['id'] for p in result['photos']],list(range(19)))
        self.assertEqual(result['usage']['input_tokens'],19)

    def test_invalid_batch_ids_retry_individual_images(self):
        paths=[Path(f'{i}.jpg') for i in range(4)]
        def score(chunk,env_path,**kwargs):
            if len(chunk)>1:raise ValueError('Vision did not return exactly one valid result per photo; no collage changed.')
            return dict(model='test',photos=[dict(photo(0),file=chunk[0].name)],local_rejections=[],usage={})
        with patch('dog_post_maker.photo_selection.score_photos',side_effect=score) as calls,patch('dog_post_maker.photo_selection._refine_scores',side_effect=lambda result,*args:result):
            result=_score_collection(paths,Path('.env'),None)
        self.assertEqual(calls.call_count,5)
        self.assertEqual([p['file'] for p in result['photos']],[p.name for p in paths])

    def test_square_crops_protect_head_at_edges(self):
        # Include asymmetric subjects and heads very close to image boundaries.
        for head in [dict(left=0, top=0, right=.2, bottom=.2),
                     dict(left=.8, top=.8, right=1, bottom=1),
                     dict(left=.7, top=.02, right=.98, bottom=.25)]:
            item = photo(0, head=head)
            for role in ("face", "full_body", "side_or_action"):
                left, top, right, bottom = square_crop(item, role)
                hl, ht, hr, hb = Box.model_validate(head).pixels(1200, 1600)
                self.assertEqual(right - left, bottom - top)
                self.assertTrue(0 <= left <= hl < hr <= right <= 1200)
                self.assertTrue(0 <= top <= ht < hb <= bottom <= 1600)

    def test_impossible_head_is_rejected(self):
        item = photo(0, head=dict(left=0, top=0, right=1, bottom=1))
        with self.assertRaises(ValueError):
            square_crop(item, "full_body")

    def test_variety_and_replacement(self):
        items = [photo(0, "seated"), photo(1, "front"), photo(2, "side"),
                 photo(3, "front"), photo(4, "front")]
        result = choose_photos({"photos": items})
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["chosen"]), 3)
        self.assertEqual(len(result["replacements"]), 2)
        chosen = [items[int(p["file"].split(".")[0])] for p in result["chosen"]]
        self.assertEqual({p["pose"] for p in chosen}, {"seated", "front", "side"})
        self.assertEqual(chosen[2]["pose"], "side")

    def test_three_usable_stops_even_with_many_rejected(self):
        items = [photo(i) for i in range(5)]
        items[3]["usable"] = items[4]["usable"] = False
        result = choose_photos({"photos": items})
        self.assertFalse(result["chosen"])
        self.assertIn("Only 3 usable", result["error"])

    def test_candidate_unsuitable_for_face_crop_does_not_abort_selection(self):
        items = [photo(0, "seated"), photo(1, "front"), photo(2, "side"), photo(3, "front")]
        items[0]["head_box"] = dict(left=.4, top=.1, right=.6, bottom=.7)
        result = choose_photos({"photos": items})
        self.assertIsNone(result["error"])
        self.assertNotEqual(result["chosen"][0]["file"], "0.jpg")

    def test_incomplete_source_not_used_as_full_body(self):
        items = [photo(0, "seated"), photo(1, "front"), photo(2, "side"), photo(3, "front")]
        items[1].update(full_body_visible=False, quality=10)
        result = choose_photos({"photos": items})
        full_body = next(p for p in result["chosen"] if p["role"] == "full_body")
        self.assertNotEqual(full_body["file"], "1.jpg")


if __name__ == "__main__":
    unittest.main()
