"""Local UI adapter checks; no live AI calls or real approvals."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import serve


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.folder=self.root/'input'/'azra';self.folder.mkdir(parents=True)
        (self.folder/'dog.yaml').write_text('name: Azra\nsex: Teef\ngood_with_children: null\n')
        self.out=self.root/'output'/'azra';self.details=self.out/'details';self.details.mkdir(parents=True)
        self.options=[[{'file':f'{i}.jpg','role':'face'}] for i in range(3)]
        for i in range(3):
            (self.folder/f'{i}.jpg').write_bytes(b'photo')
            (self.details/f'collage_{i+1}.jpg').write_bytes(b'collage')
        (self.folder/'extra.jpg').write_bytes(b'photo')
        (self.details/'draft.json').write_text(json.dumps({'approved':False,'chosen_option':None,'options':self.options}))
        (self.details/'caption.txt').write_text('Azra\n')
        (self.details/'photo_scores.json').write_text(json.dumps({'scores':{'photos':[{'file':'2.jpg','usable':False}]}}))
        self.patch=patch.object(serve,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)

    def test_waiting_dog_exposes_options_all_photos_and_unknowns(self):
        state=serve.dog_state('azra')
        self.assertEqual(state['status'],'waiting')
        self.assertEqual(len(state['options']),3)
        self.assertEqual(len(state['photos']),4)
        self.assertFalse(state['photos'][2]['usable'])
        self.assertIn('Children',state['unknown'])
        self.assertEqual(state['downloads'],{})

    def test_choice_persists_without_approval_or_render(self):
        state=serve.select_option('azra',2)
        self.assertEqual(state['chosen'],2)
        self.assertEqual(state['status'],'waiting')
        self.assertTrue(state['photos'][1]['selected'])
        self.assertEqual(list(self.out.glob('*.mp4')),[])

    def test_fewer_than_four_photos_has_no_photos_status_and_catalog_facts(self):
        (self.folder/'extra.jpg').unlink()
        (self.folder/'dog.yaml').write_text('name: Azra\nsex: Teef\ncountry: Noord-Macedonië\nage_text: Pup · geboren rond mei 2026\n')
        state=serve.dog_state('azra')
        self.assertEqual(state['status'],'no photos yet')
        self.assertEqual(state['photo_count'],3)
        self.assertEqual(state['facts']['country'],'Noord-Macedonië')
        self.assertEqual(state['facts']['age_text'],'Pup · geboren rond mei 2026')

    def test_dropdown_uses_catalog_instead_of_test_input_folders(self):
        (self.root/'input'/'dogs_catalog.json').write_text(json.dumps([{'id':'azra'},{'id':'chap'}]))
        handler=serve.Handler.__new__(serve.Handler)
        handler.path='/api/dogs';handler.json=Mock()
        def summary(slug):
            return dict(id=slug,name=slug.title(),status='waiting' if slug=='azra' else 'no photos yet',facts={'sex':'Teef'},photo_count=4 if slug=='azra' else 0)
        with patch.object(serve,'dog_state',side_effect=summary):handler.do_GET()
        rows=handler.json.call_args.args[0]
        self.assertEqual([row['id'] for row in rows],['azra','chap'])
        self.assertEqual(rows[1]['status'],'no photos yet')
        self.assertEqual(rows[0]['facts'],{'sex':'Teef'})

    def test_approved_requires_receipt_and_all_files(self):
        state=json.loads((self.details/'draft.json').read_text());state['approved']=True
        (self.details/'draft.json').write_text(json.dumps(state))
        self.assertEqual(serve.dog_state('azra')['status'],'waiting')
        for name in serve.FINAL_FILES:(self.out/name).write_bytes(b'approved')
        self.assertEqual(serve.dog_state('azra')['status'],'approved')

    def test_backend_delegates_to_existing_workflow_functions(self):
        with patch.object(serve,'make_options') as generate,patch.object(serve,'swap_photo') as swap,patch.object(serve,'save_caption') as caption,patch.object(serve,'approve') as approve:
            serve.perform('azra','generate',{})
            generate.assert_called_once_with(self.folder)
            serve.perform('azra','swap',{'option':2,'tile':'bottom-right','photo':'0.jpg'})
            swap.assert_called_once_with(self.folder,2,'bottom-right','0.jpg')
            serve.perform('azra','caption',{'text':'New caption'})
            caption.assert_called_once_with(self.folder,'New caption')
            caption.reset_mock()
            serve.perform('azra','approve',{'name':'Volunteer','option':2,'caption':'Edited text'})
            caption.assert_called_once_with(self.folder,'Edited text')
            approve.assert_called_once_with(self.folder,2,'Volunteer')

    def test_name_required_before_approval(self):
        with patch.object(serve,'approve') as approve:
            with self.assertRaises(ValueError):serve.perform('azra','approve',{'name':'','option':1})
            approve.assert_not_called()

    def test_paths_cannot_escape_input(self):
        for value in ('../azra','azra/../../.env','azra%2f..',''):
            with self.assertRaises(ValueError):serve.dog_folder(value)
        with self.assertRaises(ValueError):serve.select_option('azra',True)

    def test_long_jobs_serialize_and_errors_are_reported(self):
        jobs=serve.Jobs();release=threading.Event()
        try:
            job=jobs.start(lambda:release.wait(2))
            with self.assertRaises(ValueError):jobs.start(lambda:None)
            release.set();jobs.pool.shutdown(wait=True)
            self.assertEqual(jobs.items[job]['status'],'done')
        finally:release.set()
        failures=serve.Jobs()
        try:
            def fail():raise ValueError('Needs more usable photos')
            job=failures.start(fail);failures.pool.shutdown(wait=True)
            self.assertEqual(failures.items[job],{'status':'error','error':'Needs more usable photos'})
        finally:failures.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
