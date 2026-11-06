"""Guardrails are enforced locally, independent of model compliance."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
import json
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dog_post_maker.agent import PostAgent,collect_missing,normalize_change,validate_selection,usage_cost,run_draft_tools


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.scores={'photos':[{'file':f'{i}.jpg','usable':True,'width':1000,'height':1000} for i in range(4)]}
        self.chosen=[{'file':f'{i}.jpg','role':role,'crop':[0,0,1000,1000]} for i,role in enumerate(('face','full_body','side_or_action'))]

    def test_required_photo_cannot_be_dropped(self):
        validate_selection(self.chosen,self.scores,['0.jpg'])
        with self.assertRaises(ValueError):validate_selection(self.chosen,self.scores,['3.jpg'])

    def test_three_usable_stops(self):
        self.scores['photos'][3]['usable']=False
        with self.assertRaisesRegex(ValueError,'Fewer than 4'):validate_selection(self.chosen,self.scores,[])

    def test_invalid_crop_and_no_face_rejected(self):
        self.chosen[0]['crop']=[0,0,1001,1001]
        with self.assertRaises(ValueError):validate_selection(self.chosen,self.scores,[])
        self.chosen[0].update(crop=[0,0,1000,1000],role='full_body')
        with self.assertRaisesRegex(ValueError,'close-up'):validate_selection(self.chosen,self.scores,[])

    def test_answers_persist_and_unknown_stays_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'dog.yaml';dog={'name':'Azra','sex':'Teef'}
            answers=iter(['unknown','unknown','North Macedonia','unknown','unknown'])
            result=collect_missing(dog,path,lambda _:next(answers),lambda *_:None)
            self.assertEqual(yaml.safe_load(path.read_text()),result)
            self.assertEqual(result['location'],'North Macedonia')
            self.assertEqual(result['story_notes'],'')

    def test_source_threshold_stops_before_api_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent=object.__new__(PostAgent);agent.folder=Path(temporary)
            for i in range(3):(agent.folder/f'{i}.jpg').write_bytes(b'photo')
            with patch('dog_post_maker.agent.score_photos') as score:
                with self.assertRaisesRegex(ValueError,'Fewer than 4'):agent.score()
                score.assert_not_called()

    def test_crop_repair_normalizes_region_without_mutating_request(self):
        item={'file':'a.jpg','role':'face','crop':[161,0,1361,1361],'head_box':None}
        photo={'width':1536,'height':2048,'head_box':{'left':.3,'top':.1,'right':.75,'bottom':.43}}
        fixed,head=normalize_change(item,photo)
        l,t,r,b=fixed['crop']
        self.assertEqual(r-l,b-t)
        self.assertLessEqual(r,1536)
        self.assertEqual(item['crop'],[161,0,1361,1361])
        self.assertIn('head_box',item)

    def test_pixel_head_coordinates_rejected(self):
        item={'file':'a.jpg','role':'face','crop':[0,0,1000,1000],'head_box':[400,200,900,500]}
        with self.assertRaises(ValueError):
            normalize_change(item,{'width':1536,'height':2048})

    def test_cost_includes_cached_discount(self):
        self.assertAlmostEqual(usage_cost({'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':500}},'gpt-4.1-mini'),.00041)
        self.assertIsNone(usage_cost({'input_tokens':10},'custom'))

    def test_terminal_approval_never_invented_and_feedback_memory_explicit(self):
        from dog_post_maker.workflow import run_terminal
        from dog_post_maker.collage_options import CollageChoiceAgent
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);details=folder/'details';details.mkdir()
            (details/'caption.txt').write_text('Azra')
            (details/'options.jpg').write_bytes(b'preview')
            (details/'draft.json').write_text(json.dumps({'approved':False,'options':[[],[],[]]}))
            agent=SimpleNamespace(details=details,options=[[],[],[]],labels={},preferences={'rules':[]},
                preferences_path=folder/'preferences.yaml',ask=lambda _:'yes',log=Mock())
            agent.dog={'name':'Azra',**{key:'known' for key in ('sex','birth_date','country','location','social_notes','story_notes')}}
            agent.remember_feedback=lambda feedback:CollageChoiceAgent.remember_feedback(agent,feedback)
            feedback='1: replace bottom-right with C'
            with patch('dog_post_maker.workflow._paths',return_value=(folder,folder,details)), \
                 patch('dog_post_maker.workflow.DraftAgent',return_value=agent), \
                 patch('dog_post_maker.workflow.swap_photo',return_value={'collage':'preview.jpg'}) as swap, \
                 patch('dog_post_maker.workflow.approve') as approve, \
                 patch('builtins.input',side_effect=[feedback,'approve','Volunteer']):
                run_terminal('azra')
            swap.assert_called_once_with('azra',1,'bottom-right','C')
            approve.assert_called_once_with('azra',1,'Volunteer')
            self.assertEqual(yaml.safe_load(agent.preferences_path.read_text())['rules'],[feedback])

    def tool_agent(self,names):
        agent=SimpleNamespace(client=Mock(),model='test-model',preferences={'rules':['Include a close-up']},log=Mock(),charge=Mock())
        agent.client.responses.create.side_effect=[SimpleNamespace(output=[SimpleNamespace(
            type='function_call',name=name,arguments='{}',call_id=str(i))],usage=None) for i,name in enumerate(names)]
        return agent

    def test_tool_loop_runs_current_tools_and_never_exposes_approval(self):
        names=['score_photos','make_collage_options','check_and_repair','write_caption']
        agent=self.tool_agent(names);actions={name:Mock(return_value={'ok':True}) for name in names}
        run_draft_tools(agent,actions)
        for action in actions.values():action.assert_called_once_with()
        self.assertEqual(agent.client.responses.create.call_count,4)
        request=agent.client.responses.create.call_args.kwargs
        self.assertEqual([tool['name'] for tool in request['tools']],names)
        self.assertFalse(request['parallel_tool_calls'])
        self.assertEqual(len([m for m in request['input'] if isinstance(m,dict) and m.get('type')=='function_call_output']),4)

    def test_tool_loop_prevents_skipping_checks_and_manual_approval(self):
        names=['score_photos','make_collage_options','check_and_repair','write_caption']
        agent=self.tool_agent(['approve','write_caption']+names)
        actions={name:Mock(return_value={'ok':True}) for name in names}
        run_draft_tools(agent,actions)
        self.assertEqual(agent.client.responses.create.call_count,6)
        self.assertEqual(agent.log.call_args_list[1].args[0],'tool_error')
        for action in actions.values():action.assert_called_once_with()

    def test_tool_loop_stops_on_too_few_photos(self):
        agent=self.tool_agent(['score_photos'])
        actions={'score_photos':Mock(side_effect=ValueError('Fewer than 4 usable photos')),
                 'make_collage_options':Mock(),'check_and_repair':Mock(),'write_caption':Mock()}
        with self.assertRaisesRegex(ValueError,'Fewer than 4'):run_draft_tools(agent,actions)
        actions['make_collage_options'].assert_not_called()
        self.assertEqual(agent.client.responses.create.call_count,1)

    def test_tool_loop_has_a_finite_turn_limit(self):
        agent=self.tool_agent(['write_caption']*12)
        score=Mock()
        with self.assertRaisesRegex(ValueError,'tool-turn limit'):
            run_draft_tools(agent,{'score_photos':score})
        score.assert_not_called()
        self.assertEqual(agent.client.responses.create.call_count,12)

    def test_terminal_collects_and_saves_missing_information(self):
        from dog_post_maker.workflow import run_terminal
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);details=folder/'details';details.mkdir()
            agent=SimpleNamespace(dog={'name':'Azra','sex':'Teef'},details=details,options=[[],[],[]],labels={},log=Mock())
            replies=['unknown','Noord-Macedonië','unknown','unknown','unknown','1','approve','Volunteer']
            with patch('dog_post_maker.workflow._paths',return_value=(folder,folder,details)), \
                 patch('dog_post_maker.workflow.DraftAgent',return_value=agent), \
                 patch('dog_post_maker.workflow.make_options',return_value={'overview':'preview','caption':'Azra'}) as options, \
                 patch('dog_post_maker.workflow.approve') as approve, \
                 patch('builtins.input',side_effect=replies):
                run_terminal('azra')
            saved=yaml.safe_load((folder/'dog.yaml').read_text())
            self.assertEqual(saved['country'],'Noord-Macedonië')
            self.assertEqual(saved['story_notes'],'')
            options.assert_called_once_with('azra')
            approve.assert_called_once_with('azra',1,'Volunteer')


if __name__=='__main__':unittest.main()
