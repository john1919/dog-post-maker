"""Check missing facts, Dutch date precision and source-grounded story handling."""

import sys
import json
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dog_post_maker.caption import Paragraph, Story, CaptionValidation, ClaimCheck, Evidence, CaptionRejected, born_around, write_caption, validate_caption
from dog_post_maker.output_files import tidy_output


class CaptionTests(unittest.TestCase):
    def test_missing_story_makes_no_api_call_or_invented_paragraphs(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"OPENAI_MODEL": "gpt-4.1-mini"}), patch("dog_post_maker.caption.OpenAI") as api:
            folder = Path(temporary)
            result = write_caption(dict(name="Azra", sex="Teef"), folder / "caption.txt", folder / ".env")
            text = (folder / "caption.txt").read_text()
            self.assertTrue(text.startswith("ONTBREEKT:"))
            self.assertIn("Azra\n▪ Teef\n", text)
            self.assertTrue(text.endswith("Ben jij het baasje dat Azra zoekt?\nMeer informatie op dogsonajourney.com\n"))
            self.assertNotIn("Verblijft in", text)
            self.assertEqual(result["cost_usd"], 0)
            self.assertEqual(result["paragraph_count"], 0)
            api.assert_not_called()

    def test_dutch_date_preserves_known_precision(self):
        self.assertEqual(born_around("2025-02"), "februari 2025")
        self.assertEqual(born_around("2025"), "2025")
        self.assertEqual(born_around("2025-02-10"), "februari 2025")
        with self.assertRaises(ValueError):
            born_around("2025-13")

    def test_invalid_grounding_preserves_existing_caption(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"OPENAI_MODEL": "custom-model", "OPENAI_API_KEY": "test"}), patch("dog_post_maker.caption.OpenAI") as api:
            folder = Path(temporary)
            output = folder / "caption.txt"
            output.write_text("previous")
            api.return_value.responses.parse.return_value = SimpleNamespace(
                output_parsed=Story(paragraphs=[Paragraph(text="Ze is lief.", source_quotes=["invented quote"])]))
            with self.assertRaises(ValueError):
                write_caption(dict(name="Azra", story_notes="Gentle."), output, folder / ".env")
            self.assertEqual(output.read_text(), "previous")
            self.assertEqual(api.return_value.responses.parse.call_args.kwargs["model"], "custom-model")

    def test_tidy_preserves_post_files_and_moves_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for name in ("collage.jpg", "caption.txt", "unused_clip.mp4", "photo_scores.json", "preview.jpg", "review_notes.txt"):
                (folder / name).write_text(name)
            details = tidy_output(folder)
            self.assertEqual({p.name for p in folder.iterdir()}, {"collage.jpg", "caption.txt", "details"})
            self.assertEqual((details / "photo_scores.json").read_text(), "photo_scores.json")
            tidy_output(folder)  # Safe to run again after migration.

    def test_supported_story_uses_configured_model_and_records_usage(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"OPENAI_MODEL": "gpt-4.1-mini", "OPENAI_API_KEY": "test"}), patch("dog_post_maker.caption.OpenAI") as api:
            folder = Path(temporary)
            source = "Gentle. Medium energy. Needs a sporty owner."
            paragraphs = [Paragraph(text=text, source_quotes=[quote]) for text, quote in
                          [("Ze is zachtaardig.", "Gentle."), ("Ze heeft een gemiddeld energieniveau.", "Medium energy."),
                           ("Een sportief baasje past bij haar.", "Needs a sporty owner.")]]
            generated = SimpleNamespace(
                output_parsed=Story(paragraphs=paragraphs),
                usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 100, "output_tokens": 50}))
            verified=SimpleNamespace(output_parsed=CaptionValidation(checks=[ClaimCheck(id=i,supported=True,
                evidence=[Evidence(field='story_notes',quote=p.source_quotes[0])],reason='Supported') for i,p in enumerate(paragraphs)]),
                usage=SimpleNamespace(model_dump=lambda:{}))
            api.return_value.responses.parse.side_effect=[generated,verified,verified]
            result = write_caption(dict(name="Azra", sex="Teef", story_notes=source), folder / "caption.txt", folder / ".env")
            self.assertEqual(result["paragraph_count"], 3)
            self.assertEqual(result["api_calls"], 3)
            self.assertAlmostEqual(result["cost_usd"], .00012)
            self.assertIn("Ze is zachtaardig.", (folder / "caption.txt").read_text())

    def test_two_dogs_never_share_traits_or_request_context(self):
        dogs=[dict(name='Azra',story_notes='Speels en voergemotiveerd.'),
              dict(name='Belka',story_notes='Sociaal met honden en katten.',social_notes='')]
        originals=copy.deepcopy(dogs);requests=[];counts={}
        def response(**kwargs):
            self.assertNotIn('voergemotiveerd',kwargs['input'][0]['content'])
            self.assertNotIn('hunting instinct',kwargs['input'][0]['content'])
            payload=json.loads(kwargs['input'][-1]['content']);requests.append((kwargs['text_format'],payload))
            if kwargs['text_format'] is Story:
                name=payload['name'];counts[name]=counts.get(name,0)+1
                text=('Azra is speels en voergemotiveerd.' if name=='Azra' else
                      'Ze is voergemotiveerd.' if counts[name]==1 else 'Belka is sociaal met honden en katten.')
                parsed=Story(paragraphs=[Paragraph(text=text,source_quotes=[payload['story_notes']])])
            else:
                supported=all('voergemotiveerd' not in s['text'] or 'voergemotiveerd' in payload['facts']['story_notes'] for s in payload['sentences'])
                parsed=CaptionValidation(checks=[ClaimCheck(id=s['id'],supported=supported,
                    evidence=[Evidence(field='story_notes',quote=payload['facts']['story_notes'])],
                    reason='Supported' if supported else 'Trait is absent from this dog’s facts') for s in payload['sentences']])
            return SimpleNamespace(output_parsed=parsed,usage=SimpleNamespace(model_dump=lambda:{'input_tokens':10,'output_tokens':10}))
        with tempfile.TemporaryDirectory() as temporary,patch.dict('os.environ',{'OPENAI_MODEL':'gpt-4.1-mini','OPENAI_API_KEY':'test'}),patch('dog_post_maker.caption.OpenAI') as api:
            api.return_value.responses.parse.side_effect=response
            results=[]
            for dog in dogs:
                folder=Path(temporary)/dog['name'];folder.mkdir()
                results.append(write_caption(dog,folder/'caption.txt',folder/'.env'))
            self.assertIn('voergemotiveerd',(Path(temporary)/'Azra/caption.txt').read_text())
            self.assertNotIn('voergemotiveerd',(Path(temporary)/'Belka/caption.txt').read_text())
            self.assertEqual([a['accepted'] for a in results[1]['validation_attempts']],[False,True])
            self.assertNotEqual(results[0]['facts_sha256'],results[1]['facts_sha256'])
        self.assertEqual(dogs,originals)
        self.assertEqual(counts,{'Azra':1,'Belka':2})
        for schema,payload in requests:
            if schema is Story and payload['name']=='Belka':self.assertNotIn('Speels',payload['story_notes'])
            if schema is CaptionValidation and payload['dog']=='Belka':
                self.assertNotIn('voergemotiveerd',payload['facts']['story_notes'])
                self.assertNotIn('social_notes',payload['facts'])

    def test_false_evidence_is_rejected_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as temporary,patch.dict('os.environ',{'OPENAI_MODEL':'gpt-4.1-mini','OPENAI_API_KEY':'test'}),patch('dog_post_maker.caption.OpenAI') as api:
            folder=Path(temporary);output=folder/'caption.txt';output.write_text('Previous approved draft')
            generated=SimpleNamespace(output_parsed=Story(paragraphs=[Paragraph(text='Ze is voergemotiveerd.',source_quotes=['Sociaal.'])]),usage=None)
            verified=SimpleNamespace(output_parsed=CaptionValidation(checks=[ClaimCheck(id=0,supported=True,
                evidence=[Evidence(field='story_notes',quote='Voergemotiveerd.')],reason='Claimed support')]),usage=None)
            api.return_value.responses.parse.side_effect=[generated,verified]*3
            with self.assertRaisesRegex(CaptionRejected,'three attempts'):
                write_caption({'name':'Belka','story_notes':'Sociaal.'},output,folder/'.env')
            self.assertEqual(output.read_text(),'Previous approved draft')
            self.assertEqual(api.return_value.responses.parse.call_count,6)

    def test_checker_cannot_skip_a_sentence_with_an_unsupported_trait(self):
        with patch('dog_post_maker.caption.OpenAI') as api:
            api.return_value.responses.parse.return_value=SimpleNamespace(output_parsed=CaptionValidation(checks=[
                ClaimCheck(id=0,supported=True,evidence=[Evidence(field='story_notes',quote='Sociaal.')],reason='Supported')]),usage=None)
            with self.assertRaisesRegex(CaptionRejected,'every sentence'):
                validate_caption(['Ze is sociaal. Ze is voergemotiveerd.'],{'name':'Belka','story_notes':'Sociaal.'},'test',lambda _:None)

    def test_reviewer_one_based_numbering_is_unambiguous_but_all_checks_required(self):
        with patch('dog_post_maker.caption.OpenAI') as api:
            def response():return SimpleNamespace(output_parsed=CaptionValidation(checks=[ClaimCheck(id=1,
                supported=True,evidence=[Evidence(field='story_notes',quote='Sociaal.')],reason='Supported')]),usage=None)
            api.return_value.responses.parse.side_effect=[response(),response()]
            checks=validate_caption(['Ze is sociaal.'],{'name':'Belka','story_notes':'Sociaal.'},'test',lambda _:None)
            self.assertEqual([check['id'] for check in checks],[0,0])
            self.assertEqual([check['reviewer'] for check in checks],['primary','critic'])

    def test_second_reviewer_can_reject_a_first_reviewer_false_positive(self):
        with tempfile.TemporaryDirectory() as temporary,patch.dict('os.environ',{'OPENAI_MODEL':'gpt-4.1-mini','OPENAI_API_KEY':'test'}),patch('dog_post_maker.caption.OpenAI') as api:
            def response(parsed):return SimpleNamespace(output_parsed=parsed,usage=None)
            bad=response(Story(paragraphs=[Paragraph(text='Ze is sociaal en gezond.',source_quotes=['Sociaal.'])]))
            good=response(Story(paragraphs=[Paragraph(text='Ze is sociaal.',source_quotes=['Sociaal.'])]))
            approve=response(CaptionValidation(checks=[ClaimCheck(id=0,supported=True,
                evidence=[Evidence(field='story_notes',quote='Sociaal.')],reason='Supported')]))
            reject=response(CaptionValidation(checks=[ClaimCheck(id=0,supported=False,evidence=[],reason='Health is not in the facts')]))
            api.return_value.responses.parse.side_effect=[bad,approve,reject,good,approve,approve]
            folder=Path(temporary)
            result=write_caption({'name':'Belka','story_notes':'Sociaal.'},folder/'caption.txt',folder/'.env')
            self.assertNotIn('gezond',(folder/'caption.txt').read_text())
            self.assertEqual([a['accepted'] for a in result['validation_attempts']],[False,True])
            self.assertEqual(result['api_calls'],6)

    def test_unsupported_cats_claim_rejected_even_with_valid_quote(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict("os.environ", {"OPENAI_MODEL": "gpt-4.1-mini", "OPENAI_API_KEY": "test"}), patch("dog_post_maker.caption.OpenAI") as api:
            folder = Path(temporary)
            api.return_value.responses.parse.return_value = SimpleNamespace(
                output_parsed=Story(paragraphs=[Paragraph(text="Ze kan goed met katten.", source_quotes=["Gentle."])]))
            with self.assertRaises(ValueError):
                write_caption(dict(name="Azra", story_notes="Gentle."), folder / "caption.txt", folder / ".env")
            self.assertFalse((folder / "caption.txt").exists())


if __name__ == "__main__":
    unittest.main()
