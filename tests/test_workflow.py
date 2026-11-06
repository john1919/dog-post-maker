"""Drafts stay private; public approval API publishes only after authorization."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dog_post_maker.collage_options import build_options,label_for,validate_option_reuse
from dog_post_maker.workflow import approve,make_options,make_video,save_caption,caption_bullets,swap_photo,_caption_draft


def scores():
    photos=[]
    for i,pose in enumerate(('seated','front','side','side','seated','front','action')):
        photos.append(dict(file=f'{i}.jpg',width=1200,height=1600,usable=True,quality=9-i/10,
            full_body_visible=pose=='front',pose=pose,similarity_group=pose,
            head_box=dict(left=.4,top=.2,right=.6,bottom=.4),
            dog_box=dict(left=.2,top=.2,right=.8,bottom=.9)))
    return {'photos':photos}


class WorkflowTests(unittest.TestCase):
    def test_checked_caption_is_not_rewritten_or_given_another_dogs_traits(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder);agent.root=Path(folder);agent.preferences={'rules':[]}
            agent.dog={'name':'Belka','sex':'Teef','story_notes':'Sociaal met honden en katten.'}
            agent.client=Mock();agent.charge=Mock()
            checked={'source_grounding':[{'text':'Belka is sociaal met honden en katten.'}],
                     'facts_sha256':'belka-only','validation_attempts':[{'accepted':True}]}
            with patch('dog_post_maker.workflow.write_caption',return_value=checked) as writer:
                text=_caption_draft(agent)
            self.assertNotIn('voergemotiveerd',text)
            self.assertIn('Belka is sociaal met honden en katten.',text)
            self.assertEqual(writer.call_args.args[0]['name'],'Belka')
            self.assertNotIn('Omgang met kinderen: Nog niet bekend',writer.call_args.args[0]['story_notes'])
            self.assertEqual(agent.dog['story_notes'],'Sociaal met honden en katten.')
            agent.client.responses.parse.assert_not_called()

    def test_explicit_unknown_children_are_visible_without_guessing(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder);agent.root=Path(folder);agent.preferences={'rules':[]}
            agent.dog={'name':'Belka','sex':'Teef','story_notes':'Sociaal met honden.','good_with_children':None}
            checked={'source_grounding':[{'text':'Belka is sociaal met honden.'}],
                     'facts_sha256':'belka-only','validation_attempts':[{'accepted':True}]}
            with patch('dog_post_maker.workflow.write_caption',return_value=checked):text=_caption_draft(agent)
            self.assertIn('Hoe Belka met kinderen omgaat, is nog niet bekend.',text)

    def test_make_options_is_driven_by_agent_tool_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder)
            agent.preferences={'rules':[]};agent.score=Mock(return_value={'usable_count':7,'chosen':[]})
            agent.check_and_repair=Mock()
            names=['score_photos','make_collage_options','check_and_repair','write_caption']
            agent.model='test-model';agent.client=Mock()
            agent.client.responses.create.side_effect=[SimpleNamespace(output=[SimpleNamespace(
                type='function_call',name=name,arguments='{}',call_id=name)],usage=None) for name in names]
            def caption(current):
                (current.details/'caption.txt').write_text('Azra\n')
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent), \
                 patch('dog_post_maker.workflow._caption_draft',side_effect=caption) as write:
                result=make_options('azra')
            self.assertFalse(result['approved'])
            self.assertEqual(agent.client.responses.create.call_count,4)
            agent.score.assert_called_once_with()
            agent.check_and_repair.assert_called_once_with()
            write.assert_called_once_with(agent)

    def test_three_options_are_distinct_and_varied(self):
        data=scores();options=build_options(data,[])
        self.assertEqual(len({frozenset(p['file'] for p in option) for option in options}),3)
        validate_option_reuse(options)
        from collections import Counter
        self.assertLessEqual(max(Counter(p['file'] for option in options for p in option).values()),2)
        by_name={p['file']:p for p in data['photos']}
        for option in options:
            self.assertEqual([p['role'] for p in option],['face','full_body','side_or_action'])
            self.assertTrue(by_name[option[1]['file']]['full_body_visible'])
            self.assertIn(by_name[option[2]['file']]['pose'],('side','action'))

    def test_required_photos_preserved_in_all_options(self):
        with self.assertRaisesRegex(ValueError,'conflicts'):build_options(scores(),['0.jpg'])
        with self.assertRaises(ValueError):build_options(scores(),['0.jpg','1.jpg','2.jpg'])

    def test_spare_photo_labels(self):
        self.assertEqual([label_for(i) for i in (0,25,26,27)],['A','Z','AA','AB'])
        self.assertEqual(label_for(701),'ZZ')
        self.assertEqual(label_for(702),'AAA')

    def context(self,folder):
        output=Path(folder)/'output';details=output/'details';details.mkdir(parents=True)
        options=build_options(scores(),[])
        agent=SimpleNamespace(output=output,details=details,options=options,labels={},dog={},
            photo_data={'scores':scores()},log=Mock())
        (details/'draft.json').write_text(json.dumps({'options':options,'labels':{},'approved':False,'swaps':[{'tile':'bottom-right','from':'2.jpg','to':'6.jpg'}]}))
        (details/'caption.txt').write_text('Azra\n')
        return agent

    def test_short_bullets_use_country_fallback(self):
        dog=dict(sex='Teef',birth_date='2026-05',country='Noord-Macedonië',location='',
                 size='Middelgroot · verwacht max. 17 kg',good_with_dogs=True,good_with_cats=True,
                 energy='Hoog',good_with_children=None)
        self.assertEqual(caption_bullets(dog),['Teef','Geboren rond mei 2026','Verblijft in Noord-Macedonië',
            'Middelgroot, verwacht max. 17 kg','Sociaal met honden en katten'])
        dog['location']='Utrecht'
        self.assertEqual(caption_bullets(dog)[2],'Verblijft in Utrecht')

    def test_reuse_validator_rejects_three_appearances(self):
        options=build_options(scores(),[])
        for option in options:option[0]['file']='same.jpg'
        with self.assertRaises(ValueError):validate_option_reuse(options)

    def test_manual_swaps_ignore_ai_suitability_and_advice_cannot_veto(self):
        from PIL import Image
        for mode in ('safe','head_cut','offline'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                agent=self.context(folder)
                agent.folder=Path(folder);agent.model='gpt-4.1-mini'
                Image.new('RGB',(1200,1600),'white').save(agent.folder/'0.jpg')
                agent.photo_data['scores']['photos'][0].update(usable=False,full_body_visible=False)
                agent.render_option=Mock();agent.overview=Mock();agent.charge=Mock();agent.client=Mock()
                if mode=='offline':agent.client.responses.parse.side_effect=RuntimeError('unavailable')
                else:agent.client.responses.parse.return_value=SimpleNamespace(
                    output_parsed=SimpleNamespace(head_or_face_cut_off=mode=='head_cut'),
                    usage=SimpleNamespace(model_dump=lambda:{}))
                with patch('dog_post_maker.workflow.DraftAgent',return_value=agent):
                    result=swap_photo('azra',1,'bottom-left','0.jpg')
                self.assertTrue(result['applied'])
                state=json.loads((agent.details/'draft.json').read_text())
                self.assertEqual(state['options'][0][1]['file'],'0.jpg')
                self.assertTrue(state['options'][0][1]['manual'])
                self.assertEqual(state['quality_status'],'advisory')
                self.assertTrue(agent.render_option.call_args.kwargs['manual'])
                self.assertEqual(any('head or face' in note for note in result['notes']),mode=='head_cut')

    def test_manual_choice_is_not_rejected_during_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder);agent.options[0][1]['manual']=True
            stage=agent.details/'approved_stage';stage.mkdir()
            (stage/'collage.jpg').write_bytes(b'jpeg');(stage/'video.mp4').write_bytes(b'mp4')
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent), \
                 patch('dog_post_maker.workflow.validate_variety',side_effect=ValueError('AI veto')) as validate, \
                 patch('dog_post_maker.workflow.make_video',return_value={'video':str(stage/'video.mp4')}):
                approve('azra',1,'Volunteer')
            validate.assert_not_called()
            self.assertTrue((agent.output/'approval.txt').exists())

    def test_video_cannot_run_before_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder)
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent),patch('dog_post_maker.animated_collage.make_animated_collage') as render:
                with self.assertRaisesRegex(ValueError,'requires approval'):make_video('azra',1)
                render.assert_not_called()
            self.assertEqual([p.name for p in agent.output.iterdir()],['details'])

    def test_approval_receipt_and_exact_final_file_set(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder)
            def staged_video(dog,option):
                self.assertTrue(json.loads((agent.details/'draft.json').read_text())['approved'])
                self.assertEqual([p.name for p in agent.output.iterdir()],['details'])
                stage=agent.details/'approved_stage';stage.mkdir()
                (stage/'collage.jpg').write_bytes(b'jpeg');(stage/'video.mp4').write_bytes(b'mp4')
                return {'video':str(stage/'video.mp4')}
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent),patch('dog_post_maker.workflow.make_video',side_effect=staged_video):
                result=approve('azra',1,'Rescue volunteer')
            self.assertEqual({p.name for p in agent.output.iterdir() if p.is_file()},{'collage.jpg','video.mp4','caption.txt','approval.txt'})
            receipt=json.loads((agent.output/'approval.txt').read_text())
            self.assertEqual(receipt['approver_name'],'Rescue volunteer')
            self.assertEqual(receipt['chosen_option'],1)
            self.assertIn('+',receipt['approved_at'])
            self.assertTrue(receipt['swapped_photos'])
            for name,digest in receipt['sha256'].items():self.assertEqual(hashlib.sha256((agent.output/name).read_bytes()).hexdigest(),digest)

    def test_failed_video_does_not_publish_anything(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder)
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent),patch('dog_post_maker.workflow.make_video',side_effect=ValueError('QA failed')):
                with self.assertRaises(ValueError):approve('azra',1,'Volunteer')
            self.assertFalse(json.loads((agent.details/'draft.json').read_text())['approved'])
            self.assertEqual([p.name for p in agent.output.iterdir()],['details'])

    def video_context(self,folder):
        agent=self.context(folder)
        state=json.loads((agent.details/'draft.json').read_text())
        state.update(approved=True,chosen_option=1,approved_selection=copy.deepcopy(agent.options[0]))
        (agent.details/'draft.json').write_text(json.dumps(state))
        (agent.details/'video_selection.json').write_text(json.dumps({'clips':[]}))
        agent.photo_data['selection']={'chosen':agent.options[0]}
        (agent.details/'collage_1.jpg').write_bytes(b'jpeg')
        agent.dog={'name':'Azra'}
        agent.folder=Path(folder);agent.root=Path(folder);agent.generation=0;agent.charge=Mock()
        return agent

    def test_source_quality_observations_do_not_block_approved_video(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.video_context(folder)
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent), \
                 patch('dog_post_maker.video.select_video_segments',return_value={'clips':[]}), \
                 patch('dog_post_maker.animated_collage.make_animated_collage',return_value={'duration':16.5}) as render, \
                 patch('dog_post_maker.workflow.review_video',return_value={'blocking_findings':[],'observations':['Source motion blur'],'summary':'No render defects'}):
                result=make_video('azra',1)
            self.assertEqual(render.call_count,1)
            self.assertEqual(result['checks']['observations'],['Source motion blur'])
            self.assertEqual([p.name for p in agent.output.iterdir()],['details'])

    def test_blocking_video_defects_stop_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.video_context(folder)
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent), \
                 patch('dog_post_maker.video.select_video_segments',return_value={'clips':[]}), \
                 patch('dog_post_maker.animated_collage.make_animated_collage',return_value={'duration':16.5}) as render, \
                 patch('dog_post_maker.workflow.review_video',return_value={'blocking_findings':['Head cut'],'observations':[],'summary':'Fix crop'}):
                with self.assertRaisesRegex(ValueError,'three attempts'):make_video('azra',1)
            self.assertEqual(render.call_count,3)
            self.assertEqual([p.name for p in agent.output.iterdir()],['details'])

    def test_editing_caption_revokes_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            agent=self.context(folder);state=json.loads((agent.details/'draft.json').read_text());state['approved']=True
            (agent.details/'draft.json').write_text(json.dumps(state));(agent.output/'collage.jpg').write_bytes(b'old')
            with patch('dog_post_maker.workflow.DraftAgent',return_value=agent):save_caption('azra','Updated caption')
            self.assertEqual((agent.details/'caption.txt').read_text(),'Updated caption\n')
            self.assertFalse(json.loads((agent.details/'draft.json').read_text())['approved'])
            self.assertEqual([p.name for p in agent.output.iterdir()],['details'])


if __name__=='__main__':unittest.main()
