"""Shared photo scoring, crop validation and cost accounting for the draft agent."""
import copy
import hashlib
import json
from pathlib import Path

import yaml

from .caption import FIELDS
from .photo_selection import choose_photos, score_photos, square_crop, write_selection_report


def usage_cost(usage, model):
    if not usage or model not in ('gpt-4.1-mini', 'gpt-4.1-mini-2025-04-14'):
        return 0 if not usage else None
    cached = (usage.get('input_tokens_details') or {}).get('cached_tokens', 0)
    return ((usage.get('input_tokens', 0)-cached)*.4 + cached*.1 + usage.get('output_tokens', 0)*1.6)/1e6


def source_fingerprint(paths):
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_selection(chosen, scores, must_use):
    usable = {p['file']: p for p in scores['photos'] if p['usable']}
    names = [p['file'] for p in chosen]
    if len(usable) < 4:
        raise ValueError('Fewer than 4 usable photos. Please add more photos and run again.')
    if len(names) != 3 or len(set(names)) != 3 or any(n not in usable for n in names):
        raise ValueError('Choose exactly three different usable photos.')
    if not set(must_use).issubset(names):
        raise ValueError('Selection must include every must_use_photos filename.')
    if not any(p['role']=='face' for p in chosen):
        raise ValueError('A close-up face selection is required by the standing rule.')
    for item in chosen:
        l,t,r,b = item['crop']; p = usable[item['file']]
        if not (0 <= l < r <= p['width'] and 0 <= t < b <= p['height'] and r-l == b-t):
            raise ValueError('Crops must be square, inside the EXIF-oriented source image.')


def normalize_change(item, photo):
    """Turn a proposed region into a square that preserves its estimated head."""
    from .video import viewport
    from .photo_selection import Box
    result=copy.deepcopy(item)
    head=result.pop('head_box',None)
    box=dict(zip(('left','top','right','bottom'),head)) if head is not None else photo['head_box']
    Box.model_validate(box).pixels(photo['width'],photo['height'])
    l,t,r,b=result['crop']
    if r<=l or b<=t: raise ValueError('Crop region must have positive width and height.')
    side=min(min(photo['width'],photo['height']),max(r-l,b-t))
    bounds=viewport(photo['width'],photo['height'],box,(1,1),((l+r)/2,(t+b)/2),
                    min(photo['width'],photo['height'])/side)
    side=min(min(photo['width'],photo['height']),int(round(bounds[2]-bounds[0])))
    left=max(0,min(photo['width']-side,int(round(bounds[0]))))
    top=max(0,min(photo['height']-side,int(round(bounds[1]))))
    result['crop']=[left,top,left+side,top+side]
    return result,box


def collect_missing(dog, path, ask, log):
    for field in ('name', *FIELDS):
        if dog.get(field) is None or not str(dog[field]).strip():
            answer = ask(f'Missing {field}. Enter value, or unknown to leave empty: ').strip()
            if not answer or answer.lower() == 'unknown':
                if field == 'name':
                    raise ValueError('A name is required; no value was guessed.')
                dog[field] = ''
                log('missing_information', {'field': field, 'answer': 'unknown; left empty'})
            else:
                dog[field] = answer
                log('missing_information', {'field': field, 'answer': answer})
            path.write_text(yaml.safe_dump(dog, allow_unicode=True, sort_keys=False), encoding='utf-8')
    return dog


def run_draft_tools(agent, actions):
    """Run the current draft tools through OpenAI; approval is never a model tool.

    Python enforces dependencies and a finite turn limit. The vision tools make
    the photo/crop decisions and perform their own bounded repair loops.
    """
    descriptions={
        'score_photos':'Score source photos and stop if fewer than four are usable.',
        'make_collage_options':'Build three varied collage options from scored photos.',
        'check_and_repair':'Look at all collages and fix visible defects, up to three attempts.',
        'write_caption':'Write and fact-check a Dutch caption using only supplied dog facts.',
    }
    tools=[{'type':'function','name':name,'description':descriptions[name],'strict':True,
            'parameters':{'type':'object','properties':{},'required':[],'additionalProperties':False}}
           for name in actions]
    messages=[{'role':'system','content':
        'Prepare draft rescue-dog posts by calling the provided tools in their listed order. '
        'Do not invent facts, skip visual checks, approve, publish, or make a video. '
        'Photo/crop decisions and repairs belong to the vision tools. Unknown facts stay unknown. '
        'Standing preferences: '+json.dumps(agent.preferences,ensure_ascii=False)},
        {'role':'user','content':'Prepare three collage options and a caption for volunteer review.'}]
    completed=[]
    for _ in range(12):
        response=agent.client.responses.create(model=agent.model,store=False,input=messages,
            tools=tools,tool_choice='required',parallel_tool_calls=False,max_output_tokens=500)
        if response.usage:agent.charge('draft tool calling',response.usage.model_dump())
        messages.extend(response.output)
        calls=[item for item in response.output if item.type=='function_call']
        for call in calls:
            expected=list(actions)[len(completed)]
            agent.log('tool_call',{'tool':call.name,'arguments':call.arguments})
            try:arguments=json.loads(call.arguments)
            except (TypeError,ValueError):arguments=None
            if call.name!=expected or arguments!={}:
                result={'error':f'Call {expected} next with no arguments.'}
                agent.log('tool_error',result)
            else:
                try:result=actions[call.name]()
                except Exception as error:
                    agent.log('tool_error',{'tool':call.name,'error':str(error)})
                    raise
                completed.append(call.name)
            messages.append({'type':'function_call_output','call_id':call.call_id,
                             'output':json.dumps(result,ensure_ascii=False)})
            if len(completed)==len(actions):return
    raise ValueError('Agent tool-turn limit reached; drafts were not approved. See review_notes.txt.')


class PostAgent:
    """Photo-scoring methods shared by the current option/approval workflow."""

    def score(self):
        from . import PHOTO_SUFFIXES
        paths=sorted(p for p in self.folder.iterdir() if p.suffix.lower() in PHOTO_SUFFIXES)
        if len(paths)<4: raise ValueError('Fewer than 4 photos. Please add more photos and run again.')
        fingerprint=source_fingerprint(paths); cache=self.details/'photo_scores.json'
        stamp=self.details/'agent_photo_fingerprint.txt'
        if cache.exists() and stamp.exists() and stamp.read_text()==fingerprint:
            self.photo_data=json.loads(cache.read_text());self.log('photo_cache','Verified source hashes; no photo API charge.')
        else:
            scores=score_photos(paths,self.root/'.env',usage_callback=lambda usage,model:self.charge('photo scoring',usage,model))
            self.photo_data={'scores':scores,'selection':choose_photos(scores)}
            cache.write_text(json.dumps(self.photo_data,indent=2));stamp.write_text(fingerprint)
        scores=self.photo_data['scores'];selection=self.photo_data['selection']
        if selection.get('error'): raise ValueError(selection['error'])
        must=self.dog.get('must_use_photos') or []
        if not isinstance(must,list) or any(not isinstance(n,str) or Path(n).name!=n for n in must) or len(set(must))>3:
            raise ValueError('must_use_photos must contain up to three distinct local filenames.')
        chosen=selection['chosen'];usable={p['file']:p for p in scores['photos'] if p['usable']}
        for name in must:
            if name not in usable: raise ValueError(f'Required photo {name} is missing or unusable; please replace it or update must_use_photos.')
            if name not in [p['file'] for p in chosen]:
                index=next(i for i in reversed(range(3)) if chosen[i]['file'] not in must)
                role=chosen[index]['role'];chosen[index]={'file':name,'role':role,'crop':list(square_crop(usable[name],role)),'reason':'Required by must_use_photos.'}
        validate_selection(chosen,scores,must)
        selection['replacements']=[n for n in usable if n not in [c['file'] for c in chosen]]
        self.save_selection()
        self.log('photo_selection',{'chosen':chosen,'replacements':selection['replacements']})
        self.dirty=True
        return {'chosen':chosen,'usable_count':len(usable),'candidates':scores['photos'],'preferences':self.preferences,'must_use_photos':must}

    def save_selection(self):
        (self.details/'photo_scores.json').write_text(json.dumps(self.photo_data,indent=2))
        write_selection_report(self.photo_data['scores'],self.photo_data['selection'],self.details/'photo_selection.txt')
