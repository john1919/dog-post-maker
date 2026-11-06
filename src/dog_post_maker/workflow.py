"""Interface-callable draft/approval workflow. No terminal input in public functions."""
import copy
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from .collage_options import CollageChoiceAgent, build_options, label_for, validate_variety
from .caption import born_around, write_caption
from .photo_selection import square_crop


def _paths(dog):
    from . import ROOT
    folder=Path(dog)
    if not folder.is_dir(): folder=ROOT/'input'/str(dog)
    folder=folder.resolve()
    if not (folder/'dog.yaml').exists():raise ValueError(f'Missing dog.yaml in {folder}')
    from .foundation import stage_media
    catalog_path=ROOT/'input'/'dogs_catalog.json'
    if catalog_path.exists():
        row=next((r for r in json.loads(catalog_path.read_text()) if r['id']==folder.name),None)
        if row:stage_media(ROOT,row['id'],row['name'])
    output=ROOT/'output'/folder.name;details=output/'details';details.mkdir(parents=True,exist_ok=True)
    return folder,output,details


def _read(path,default=None):
    return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)


def _save(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


class DraftAgent(CollageChoiceAgent):
    """Reuse scoring, visual checks, crop repairs and preferences without UI side effects."""
    def __init__(self,dog):
        from . import ROOT
        self.folder,self.output,self.details=_paths(dog);self.root=ROOT
        self.dog=yaml.safe_load((self.folder/'dog.yaml').read_text())
        self.preferences_path=ROOT/'preferences.yaml'
        self.preferences=yaml.safe_load(self.preferences_path.read_text()) if self.preferences_path.exists() else {'rules':[]}
        self.events=_read(self.details/'options_events.json',[])
        self.costs=_read(self.details/'options_cost.json',{'requests':[]})['requests']
        self.base='';self.notes=self.details/'review_notes.txt'
        self.photo_data=_read(self.details/'photo_scores.json')
        self.ask=input
        self.attempts=0;self.generation=0;self.checked=-1;self.dirty=True;self.inspection=None
        load_dotenv(ROOT/'.env');self.model=os.environ.get('OPENAI_MODEL','').strip()
        if not self.model:raise ValueError('Set OPENAI_MODEL in .env.')
        self.client=OpenAI(timeout=120,max_retries=0)
        state=_read(self.details/'draft.json',{})
        self.options=state.get('options',[]);self.labels=state.get('labels',{})

    def log(self,event,data):
        # Keep earlier review records and rebuild only this workflow's section.
        previous=self.notes.read_text() if self.notes.exists() else ''
        self.events.append({'time':datetime.now(ZoneInfo('Europe/Berlin')).isoformat(),'event':event,'data':copy.deepcopy(data)})
        _save(self.details/'options_events.json',self.events)
        prefix=previous.split('\nOPTIONS WORKFLOW\n')[0]
        self.notes.write_text(prefix+'\nOPTIONS WORKFLOW\n'+'\n'.join(json.dumps(e,ensure_ascii=False) for e in self.events)+'\n')

    def charge(self,label,usage,model=None):
        from .agent import usage_cost
        model=model or self.model
        self.costs.append({'label':label,'model':model,'usage':usage,'cost_usd':usage_cost(usage,model)})
        total=sum(item['cost_usd'] or 0 for item in self.costs)
        _save(self.details/'options_cost.json',{'requests':self.costs,'known_cost_usd':total})
        return total


def _archive_outputs(output,details):
    files=[p for p in output.iterdir() if p.is_file()]
    if files:
        archive=details/'previous_outputs'/datetime.now().strftime('%Y%m%d-%H%M%S-%f');archive.mkdir(parents=True)
        for path in files:shutil.move(str(path),str(archive/path.name))


def _state(agent):
    return _read(agent.details/'draft.json',{})


def _save_draft(agent,state):
    state.update(options=agent.options,labels=agent.labels)
    _save(agent.details/'draft.json',state)


def _invalidate(agent,state):
    if state.get('approved'):
        _archive_outputs(agent.output,agent.details)
    state.update(approved=False,approver_name=None)


def caption_bullets(dog):
    """At most five short, unlabelled facts; location falls back to country."""
    bullets=[]
    if dog.get('sex'):bullets.append(dog['sex'].strip())
    if dog.get('birth_date_text'):bullets.append(dog['birth_date_text'])
    elif dog.get('birth_date'):bullets.append('Geboren rond '+born_around(dog['birth_date']))
    location=dog.get('location') or dog.get('country')
    if location:bullets.append('Verblijft in '+location.strip())
    if dog.get('size'):bullets.append(re.sub(r'\s*·\s*',', ',dog['size'].strip()))
    dogs,cats=dog.get('good_with_dogs'),dog.get('good_with_cats')
    if dogs is True and cats is True:bullets.append('Sociaal met honden en katten')
    elif dogs is True:bullets.append('Sociaal met andere honden')
    elif cats is True:bullets.append('Sociaal met katten')
    elif dog.get('social_notes'):bullets.append(dog['social_notes'].strip().split('\n')[0])
    return [bullet.rstrip().rstrip('.').rstrip() for bullet in bullets[:5]]


def assemble_caption(dog,paragraphs):
    """Format checked prose with deterministic profile bullets and unknowns."""
    paragraphs=list(paragraphs)
    unknown_lines=[]
    for field,label in [('good_with_dogs','honden'),('good_with_cats','katten'),('good_with_children','kinderen')]:
        if field in dog and dog[field] is None and not re.search(r'\b'+label+r'\b',' '.join(paragraphs),re.I):
            unknown_lines.append(f'Hoe {dog["name"]} met {label} omgaat, is nog niet bekend.')
    if unknown_lines:paragraphs.append(' '.join(unknown_lines))
    lines=[dog['name']]+[f'▪ {bullet}' for bullet in caption_bullets(dog)]
    return '\n'.join(lines)+'\n\n'+'\n\n'.join(paragraphs)+f"\n\nBen jij het baasje dat {dog['name']} zoekt?\nMeer informatie op dogsonajourney.com\n"


def _caption_draft(agent):
    data=copy.deepcopy(agent.dog)
    facts=[data.get('story_notes') or '']
    for field,label in [('size','Grootte'),('energy','Energie'),('life_stage','Levensfase')]:
        if data.get(field):facts.append(f'{label}: {data[field]}.')
    for field,label in [('good_with_dogs','honden'),('good_with_cats','katten'),('good_with_children','kinderen')]:
        if field not in data:continue  # Older profiles may describe this only in story_notes.
        value=data.get(field)
        facts.append(f'Omgang met {label}: '+('ja.' if value is True else 'nee.' if value is False else 'Nog niet bekend.'))
    data['story_notes']='\n'.join(facts)
    data['location']=data.get('location') or 'Nog niet bekend'
    requests=agent.details/'caption_requests.json';before=len(_read(requests,[]))
    try:result=write_caption(data,agent.details/'caption_generated.txt',agent.root/'.env',style_preferences=agent.preferences['rules'])
    finally:
        for request in _read(requests,[])[before:]:agent.charge('caption',request['usage'],request['model'])
    paragraphs=[p['text'] for p in result['source_grounding']]
    agent.log('caption_fact_check',{'accepted':True,'dog':agent.dog['name'],
        'facts_sha256':result['facts_sha256'],'attempts':result['validation_attempts']})
    text=assemble_caption(agent.dog,paragraphs)
    (agent.details/'caption.txt').write_text(text,encoding='utf-8')
    return text


def make_options(dog):
    """Return three reviewed collage paths, overview, labelled spares and draft caption."""
    agent=DraftAgent(dog);_archive_outputs(agent.output,agent.details)
    agent.log('preferences_loaded',agent.preferences)
    from .agent import run_draft_tools
    def score():
        result=agent.score()
        return {'usable_count':result['usable_count'],'chosen':result['chosen']}
    def options():
        agent.options=build_options(agent.photo_data['scores'],agent.dog.get('must_use_photos') or [])
        used={p['file'] for option in agent.options for p in option}
        remaining=[p['file'] for p in agent.photo_data['scores']['photos'] if p['usable'] and p['file'] not in used]
        agent.labels={label_for(i):name for i,name in enumerate(remaining)}
        return {'options':agent.options}
    def check():
        agent.check_and_repair()
        return {'overview':str(agent.details/'options.jpg'),'checked':True,'human_review_required':True}
    def caption():
        _caption_draft(agent)
        return {'caption':str(agent.details/'caption.txt')}
    run_draft_tools(agent,{'score_photos':score,'make_collage_options':options,
                           'check_and_repair':check,'write_caption':caption})
    text=(agent.details/'caption.txt').read_text()
    state={'approved':False,'swaps':[],'chosen_option':None,'caption':str(agent.details/'caption.txt')}
    _save_draft(agent,state)
    agent.log('draft_ready',{'overview':str(agent.details/'options.jpg'),'approved':False})
    return {'options':[str(agent.details/f'collage_{i}.jpg') for i in (1,2,3)],
            'overview':str(agent.details/'options.jpg'),'spares':agent.labels,'caption':text,'approved':False}


class ManualPhotoReview(BaseModel):
    head_or_face_cut_off: bool


def swap_photo(dog,option,tile,spare_photo):
    """Apply the volunteer's choice. AI quality checks are advisory, never a veto."""
    from PIL import Image, ImageOps
    from .collage_options import POSITIONS, ROLES
    from . import PHOTO_SUFFIXES
    agent=DraftAgent(dog);state=_state(agent)
    if type(option) is not int or option not in (1,2,3) or len(agent.options)!=3:
        raise ValueError('Choose an existing option 1–3.')
    if tile not in POSITIONS:raise ValueError('Choose top-right, bottom-left or bottom-right.')
    filename=agent.labels.get(spare_photo,spare_photo)
    if not isinstance(filename,str) or Path(filename).name!=filename:
        raise ValueError('Choose a photo from this dog folder.')
    from .foundation import media_path
    path=media_path(agent.folder,filename)
    if path.suffix.lower() not in PHOTO_SUFFIXES:
        raise ValueError('Choose a photo from this dog folder.')
    with Image.open(path) as source:
        image=ImageOps.exif_transpose(source).convert('RGB')
    scores=agent.photo_data['scores']['photos']
    photo=next((p for p in scores if p['file']==filename),None)
    if photo is None:
        # Geometry defaults are framing hints, not claims that the head was detected.
        photo={'file':filename,'width':image.width,'height':image.height,'usable':True,
               'head_box':{'left':0,'top':0,'right':1,'bottom':1},
               'dog_box':{'left':0,'top':0,'right':1,'bottom':1},'pose':'other',
               'quality':0,'full_body_visible':False,'similarity_group':'manual',
               'reason':'Volunteer-selected photo; not AI-scored.'}
        scores.append(photo)
    photo['width'],photo['height']=image.size
    index=POSITIONS[tile];role=ROLES[index]
    try:crop=list(square_crop(photo,role))
    except ValueError:
        side=min(image.size);left=(image.width-side)//2;top=(image.height-side)//2
        crop=[left,top,left+side,top+side]
    old=agent.options[option-1][index]['file']
    _invalidate(agent,state)
    agent.options[option-1][index]={'file':filename,'role':role,'crop':crop,'manual':True,
                                   'reason':'Volunteer choice; variety and reuse rules not applied.'}
    agent.render_option(agent.options[option-1],agent.details/f'collage_{option}.jpg',manual=True)
    agent.overview()
    _save(agent.details/'photo_scores.json',agent.photo_data)
    state.setdefault('swaps',[]).append({'option':option,'tile':tile,'from':old,'to':filename})
    state['chosen_option']=option;state['quality_status']='advisory'
    tip={'top-right':'Tip: this tile usually shows the face. Your choice is kept.',
         'bottom-left':'Tip: this tile usually shows the full body. Your choice is kept.',
         'bottom-right':'Tip: this tile usually shows a side view or action. Your choice is kept.'}[tile]
    state.setdefault('manual_notes',{})[str(option)]=[tip]
    _save_draft(agent,state);agent.log('human_swap',state['swaps'][-1])
    # The swap is already saved. A model outage or an adverse judgment cannot undo it.
    try:
        import base64,io
        preview=image.crop(crop);preview.thumbnail((768,768));buffer=io.BytesIO();preview.save(buffer,'JPEG')
        response=agent.client.responses.parse(model=agent.model,store=False,max_output_tokens=200,
            text_format=ManualPhotoReview,input=[{'role':'system','content':
                'Inspect this volunteer-chosen cropped dog photo. Return true ONLY if the head, ears or face '
                'is visibly cut off by a frame edge. Tail cuts and missing lower body are fine. Do not evaluate '
                'pose variety, backgrounds, sharpness or suitability for a role. This is advisory only.'},
                {'role':'user','content':[{'type':'input_image','detail':'high','image_url':
                'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()}]}])
        agent.charge('manual photo advisory',response.usage.model_dump())
        if response.output_parsed and response.output_parsed.head_or_face_cut_off:
            state['manual_notes'][str(option)].append('The head or face may be cut off. Your photo is saved; you can keep it or choose another.')
        agent.log('manual_photo_advisory',state['manual_notes'][str(option)])
    except Exception as error:
        agent.log('manual_photo_advisory_unavailable',type(error).__name__)
    _save_draft(agent,state)
    return {'collage':str(agent.details/f'collage_{option}.jpg'),'overview':str(agent.details/'options.jpg'),
            'notes':state['manual_notes'][str(option)],'applied':True}


def save_caption(dog,text):
    """Save human-edited draft text; revoke prior approval, never publish implicitly."""
    if not isinstance(text,str) or not text.strip():raise ValueError('Caption must be nonempty text.')
    agent=DraftAgent(dog);state=_state(agent);_invalidate(agent,state)
    path=agent.details/'caption.txt';path.write_text(text.rstrip()+'\n',encoding='utf-8')
    _save_draft(agent,state);agent.log('caption_saved',{'text':text})
    return {'caption':str(path),'approved':False}


class VideoReview(BaseModel):
    blocking_findings: list[str]
    observations: list[str]
    summary: str


def review_video(agent,stage):
    import base64,io
    from PIL import Image
    from moviepy import VideoFileClip
    content=[]
    with VideoFileClip(str(stage/'video.mp4'),audio=False) as clip:
        for i,t in enumerate((2.3,4.3,6,8.5,11.5,15)):
            image=Image.fromarray(clip.get_frame(t))
            image.save(agent.details/f'final_video_check_{i}.jpg')
            image.thumbnail((1080,1080));buffer=io.BytesIO();image.save(buffer,'JPEG',quality=90)
            content.extend([{'type':'input_text','text':f'Video frame at {t}s'},
                            {'type':'input_image','detail':'high','image_url':'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()}])
    response=agent.client.responses.parse(model=agent.model,store=False,max_output_tokens=1800,text_format=VideoReview,
        input=[{'role':'system','content':
            'Check the rendered animated collage VIDEO for actual visible rendering defects. The human already approved '
            'the collage/photo choices: do not reopen pose variety or composition preferences. Blocking findings are '
            'a visibly cut head/face due to the video crop, unreadable final text, stretching, or unintended black bars. '
            'Put ordinary source-footage motion blur, backgrounds and subjective preferences in observations, not blockers. '
            'Face portrait tiles intentionally omit lower body. Title/blue background and sequential empty tiles before '
            '4.1 seconds are intentional. Still tiles gently zoom; two lower tiles briefly play clips. From 10.5–13.5s '
            'the collage freezes, then the end card appears. Return only defects actually visible in supplied frames; '
            'do not infer unseen frames or invent missing close-ups.'}, {'role':'user','content':content}])
    agent.charge('final video quality check',response.usage.model_dump())
    if response.output_parsed is None:raise ValueError('Video review returned no result.')
    result=response.output_parsed.model_dump();agent.log('final_video_review',result)
    _save(agent.details/'final_video_review.json',result)
    return result


def make_video(dog,option):
    """Render one staged 1080×1350 animated collage only with matching approval intent."""
    from .animated_collage import make_animated_collage
    from .video import select_video_segments,photo_head_box
    agent=DraftAgent(dog);state=_state(agent)
    if not state.get('approved') or state.get('chosen_option')!=option:
        raise ValueError('Video rendering requires approval of this exact option.')
    if state.get('approved_selection')!=agent.options[option-1]:raise ValueError('Selection changed; approve it again.')
    data=copy.deepcopy(agent.photo_data);data['selection']['chosen']=agent.options[option-1];data['approved_collage']=True
    paths=sorted(p for p in agent.folder.iterdir() if p.suffix.lower() in ('.mp4','.mov'))
    before=(agent.details/'video_selection.json').read_text() if (agent.details/'video_selection.json').exists() else None
    clips=select_video_segments(paths,agent.details,agent.root/'.env')
    if before!=(agent.details/'video_selection.json').read_text():agent.charge('video selection',clips['usage'],clips['model'])
    stage=agent.details/'approved_stage';stage.mkdir(exist_ok=True)
    shutil.copy2(agent.details/f'collage_{option}.jpg',stage/'collage.jpg')
    original_output=agent.output;agent.output=stage
    try:
        for attempt in range(3):
            metadata=make_animated_collage(agent.folder,data,clips,stage/'video.mp4',(1080,1350),agent.dog.get('title') or f"{agent.dog['name']} in NL")
            agent.generation+=1;agent.dirty=False
            review=review_video(agent,stage)
            if not review['blocking_findings']:break
            for p in data['scores']['photos']:
                if p['file'] in [c['file'] for c in data['selection']['chosen']]:
                    box=photo_head_box(p);margin=.03*(attempt+1)
                    p['head_box']={'left':max(0,box['left']-margin),'top':max(0,box['top']-margin),
                                   'right':min(1,box['right']+margin),'bottom':min(1,box['bottom']+margin)}
            agent.log('video_repair',{'attempt':attempt+1,'findings':review['blocking_findings']})
        _save(agent.details/'video_render.json',metadata)
        if review['blocking_findings']:raise ValueError('Video has unresolved render defects after three attempts; nothing published. See review_notes.txt.')
    finally:agent.output=original_output
    return {'video':str(stage/'video.mp4'),'checks':review}


def approve(dog,option,approver_name):
    """Explicit approval authorizes rendering; publish four files and a checksum receipt last."""
    if not isinstance(approver_name,str) or not approver_name.strip():raise ValueError('Approver name is required.')
    agent=DraftAgent(dog);state=_state(agent)
    if type(option) is not int or option not in (1,2,3) or len(agent.options)!=3:raise ValueError('Choose an existing option 1–3.')
    if not any(photo.get('manual') for photo in agent.options[option-1]):
        validate_variety(agent.options[option-1],agent.photo_data['scores'],agent.dog.get('must_use_photos') or [])
    if not (agent.details/'caption.txt').exists():raise ValueError('Save the draft caption before approval.')
    if state.get('quality_status') in ('pending','failed'):raise ValueError('The changed collage needs a successful quality check before approval.')
    caption_hash=hashlib.sha256((agent.details/'caption.txt').read_bytes()).hexdigest()
    state.update(approved=True,chosen_option=option,approved_selection=copy.deepcopy(agent.options[option-1]),approver_name=approver_name.strip(),caption_hash=caption_hash)
    _save_draft(agent,state);agent.log('approval_authorized',{'option':option,'approver_name':approver_name.strip()})
    try:video=make_video(dog,option)
    except Exception:
        state['approved']=False;_save_draft(agent,state);raise
    latest=_state(agent)
    if not latest.get('approved') or latest.get('approved_selection')!=state['approved_selection'] or hashlib.sha256((agent.details/'caption.txt').read_bytes()).hexdigest()!=caption_hash:
        raise ValueError('Draft changed during rendering; nothing published. Approve the updated draft again.')
    stage=Path(video['video']).parent
    shutil.copy2(agent.details/'caption.txt',stage/'caption.txt')
    hashes={name:hashlib.sha256((stage/name).read_bytes()).hexdigest() for name in ('collage.jpg','video.mp4','caption.txt')}
    receipt={'approver_name':approver_name.strip(),'approved_at':datetime.now(ZoneInfo('Europe/Berlin')).isoformat(),
             'chosen_option':option,'selected_photos':state['approved_selection'],'swapped_photos':[swap for swap in state.get('swaps',[]) if swap.get('option',option)==option],'sha256':hashes}
    (stage/'approval.txt').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for name in ('collage.jpg','video.mp4','caption.txt','approval.txt'):shutil.copy2(stage/name,agent.output/name)
    agent.log('published_locally',receipt)
    return {'files':[str(agent.output/name) for name in ('collage.jpg','video.mp4','caption.txt','approval.txt')],'approval':receipt}


def run_terminal(dog):
    folder,output,details=_paths(dog)
    from .agent import collect_missing
    agent=DraftAgent(dog)
    previous=copy.deepcopy(agent.dog)
    agent.dog=collect_missing(agent.dog,folder/'dog.yaml',input,agent.log)
    facts_changed=previous!=agent.dog
    state=_read(details/'draft.json',{})
    if not facts_changed and state and not state.get('approved') and (details/'options.jpg').exists() and (details/'caption.txt').exists():
        result={'overview':str(details/'options.jpg'),'caption':(details/'caption.txt').read_text()}
        DraftAgent(dog).log('draft_resumed','Existing unapproved draft retained.')
    else:
        result=make_options(dog);state=_read(details/'draft.json',{})
    print('Overview:',result['overview']);print(result['caption'])
    selected=state.get('chosen_option')
    while True:
        answer=input("Which option? You can also replace a photo, e.g. '2' or '2: replace bottom-right with C'. Type approve when satisfied: ").strip()
        agent=DraftAgent(dog);agent.log('human_feedback',answer)
        if answer.lower()=='caption':
            print('Paste caption; finish with a line containing .end')
            lines=[]
            while True:
                line=input()
                if line=='.end':break
                lines.append(line)
            try:print(save_caption(dog,'\n'.join(lines)))
            except ValueError as error:print(error)
            continue
        if answer.lower()=='approve':
            if selected is None:print('Choose an option first.');continue
            name=input('Approver name: ').strip()
            try:print(approve(dog,selected,name))
            except ValueError as error:print(error);continue
            return
        match=re.fullmatch(r'([123])(?:\s*:\s*replace\s+(top-right|bottom-left|bottom-right)\s+with\s+([A-Za-z]+))?',answer,re.I)
        if not match:print('Use 1, 2, 3 or the replacement example.');continue
        try:
            if match[2]:
                result=swap_photo(dog,int(match[1]),match[2].lower(),match[3].upper());print('Updated collage:',result['collage'])
                agent.remember_feedback(answer)
            else:print('Selected collage:',agent.details/f'collage_{match[1]}.jpg')
            selected=int(match[1])
            state=_state(agent);state['chosen_option']=selected;_save_draft(agent,state)
        except ValueError as error:print(error)
