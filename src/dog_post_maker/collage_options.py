"""Choose a collage first; render one animated video only after human approval."""
import copy
from collections import Counter
import itertools
import json
from typing import Literal

import yaml
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel

from .agent import PostAgent, normalize_change, validate_selection
from .photo_selection import square_crop

POSITIONS = {'top-right':0, 'bottom-left':1, 'bottom-right':2}
ROLES = ('face','full_body','side_or_action')


def label_for(index):
    label=''
    index+=1
    while index:
        index,remainder=divmod(index-1,26)
        label=chr(65+remainder)+label
    return label


def build_options(scores, must_use):
    usable=[p for p in scores['photos'] if p['usable']]
    if len(usable)<4: raise ValueError('Need at least four usable photos; please add more.')
    if len(set(must_use))>3: raise ValueError('At most three must_use_photos can fit.')
    candidates=[]
    for triple in itertools.permutations(usable,3):
        names={p['file'] for p in triple}
        if not set(must_use)<=names: continue
        if not triple[1]['full_body_visible'] or triple[2]['pose'] not in ('side','action'):continue
        try:
            chosen=[{'file':p['file'],'role':role,'crop':list(square_crop(p,role)),
                     'reason':f'Option role: {role}; quality {p["quality"]}/10; pose {p["pose"]}.'}
                    for p,role in zip(triple,ROLES)]
        except ValueError:continue
        quality=sum(p['quality'] for p in triple)
        if triple[1]['pose']==triple[2]['pose']:quality-=8
        if triple[1]['similarity_group']==triple[2]['similarity_group']:quality-=12
        candidates.append((quality,chosen,names))
    if must_use:
        raise ValueError('Requiring a photo in all three options conflicts with the at-most-two reuse rule. Update must_use_photos before generating options.')
    def search(options,used,seen):
        if len(options)==3:return options
        counts=Counter(p['file'] for option in options for p in option)
        remaining=sorted((c for c in candidates if frozenset(c[2]) not in seen and all(counts[n]<2 for n in c[2])),
                         key=lambda c:c[0]+4*len(c[2]-used),reverse=True)
        for _,chosen,names in remaining:
            result=search(options+[chosen],used|names,seen|{frozenset(names)})
            if result is not None:return result
        return None
    options=search([],set(),set())
    if options is None:
        raise ValueError('Not enough varied photos to create three options with at most two uses per photo. Add more full-body or side/action photos.')
    validate_option_reuse(options)
    return options


def validate_option_reuse(options):
    counts=Counter(p['file'] for option in options for p in option)
    if any(count>2 for count in counts.values()):
        raise ValueError('A photo may appear in at most two options.')
    if len(options)==3 and any(len({option[i]['file'] for option in options})==1 for i in range(3)):
        raise ValueError('The same photo cannot occupy a tile position in all three options.')
    if len({frozenset(p['file'] for p in option) for option in options})!=len(options):
        raise ValueError('Every option must use a different set of photos.')


def validate_variety(chosen,scores,must):
    validate_selection(chosen,scores,must)
    photos={p['file']:p for p in scores['photos']}
    if tuple(p['role'] for p in chosen)!=ROLES:raise ValueError('Tile roles must remain face, full body, side/action.')
    if not photos[chosen[1]['file']]['full_body_visible']:raise ValueError('The bottom-left tile needs a full-body source photo.')
    if photos[chosen[2]['file']]['pose'] not in ('side','action'):raise ValueError('The bottom-right tile needs a side/action source photo.')




class OptionFix(BaseModel):
    option: int
    tile: Literal['top-right','bottom-left','bottom-right']
    issue: str
    replacement: str | None
    crop: list[int] | None


class OptionsReview(BaseModel):
    findings: list[OptionFix]
    summary: str


class CollageChoiceAgent(PostAgent):
    def render_option(self,chosen,path, *, manual=False):
        from . import crop_photo,make_collage
        if not manual:
            validate_variety(chosen,self.photo_data['scores'],self.dog.get('must_use_photos') or [])
        make_collage(self.dog['name'],[crop_photo(self.folder/p['file'],p['crop']) for p in chosen],path,
                     self.dog.get('title') or f"{self.dog['name']} in NL")

    def overview(self):
        from . import _font
        used={p['file'] for option in self.options for p in option}
        for photo in self.photo_data['scores']['photos']:
            if photo['usable'] and photo['file'] not in used and photo['file'] not in self.labels.values():
                self.labels[label_for(len(self.labels))]=photo['file']
        visible={key:name for key,name in self.labels.items() if name not in used}
        width=1800;panel=580
        rows=(len(visible)+7)//8
        sheet=Image.new('RGB',(width,710+rows*215),'#eef2f4');draw=ImageDraw.Draw(sheet)
        for index in range(3):
            x=index*600+10
            draw.text((x+panel/2,8),str(index+1),font=_font('Baloo2.ttf',70,True),fill='black',anchor='mt')
            with Image.open(self.details/f'collage_{index+1}.jpg') as image:
                sheet.paste(image.resize((panel,panel),Image.Resampling.LANCZOS),(x,90))
        draw.text((15,678),'Other usable photos — use these labels for replacements',font=_font('Nunito.ttf',24),fill='black')
        for index,(label,name) in enumerate(visible.items()):
            x=(index%8)*225;y=715+(index//8)*215
            with Image.open(self.folder/name) as source:
                image=ImageOps.exif_transpose(source).convert('RGB');image.thumbnail((215,170))
                sheet.paste(image,(x+(215-image.width)//2,y+35))
            draw.text((x+8,y),label,font=_font('Baloo2.ttf',32,True),fill='black')
        sheet.save(self.details/'options.jpg',quality=95)
        (self.details/'collage_options.json').write_text(json.dumps({'options':self.options,'labels':self.labels},indent=2))

    def review_options(self,feedback=''):
        import base64,io
        content=[]
        for i in range(3):
            with Image.open(self.details/f'collage_{i+1}.jpg') as source:
                image=source.copy();image.thumbnail((1080,1080));buffer=io.BytesIO();image.save(buffer,'JPEG')
            content.extend([{'type':'input_text','text':f'Option {i+1}'},{'type':'input_image','detail':'high','image_url':'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()}])
        response=self.client.responses.parse(model=self.model,store=False,max_output_tokens=2200,text_format=OptionsReview,
            input=[{'role':'system','content':'Review three actual collage options. Findings must describe ONLY visible defects needing changes, never praise or acceptable cropping. Check intact head/face, full body in bottom-left, distinct framing/poses, distracting backgrounds and readable text. A face portrait omits lower body intentionally; tail tips may be cropped. The title is in its own tile; do not invent text overlap. Return concrete per-option per-tile findings. Replacement must be a usable filename below; crop is source-image pixel region, or null. Keep role variety and must-use photos. No new dog facts. Preferences: '+json.dumps(self.preferences)+'. State: '+json.dumps({'options':self.options,'candidates':self.photo_data['scores']['photos'],'must_use':self.dog.get('must_use_photos') or [],'human_feedback':feedback})},
                   {'role':'user','content':content}])
        self.charge('collage options vision check',response.usage.model_dump())
        result=response.output_parsed
        if result is None:raise ValueError('No visual review returned.')
        self.log('options_quality_check',result.model_dump())
        return result

    def check_and_repair(self,feedback=''):
        for attempt in range(3):
            for index,chosen in enumerate(self.options):self.render_option(chosen,self.details/f'collage_{index+1}.jpg')
            review=self.review_options(feedback)
            if not review.findings or attempt==2:break
            for fix in review.findings:
                try:
                    if fix.option not in (1,2,3) or fix.tile not in POSITIONS:raise ValueError('Invalid option/tile in repair.')
                    proposed=copy.deepcopy(self.options[fix.option-1]);index=POSITIONS[fix.tile]
                    if fix.replacement:
                        photo=next(p for p in self.photo_data['scores']['photos'] if p['file']==fix.replacement and p['usable'])
                        proposed[index].update(file=photo['file'],crop=list(square_crop(photo,ROLES[index])))
                    if fix.crop:
                        photo=next(p for p in self.photo_data['scores']['photos'] if p['file']==proposed[index]['file'])
                        proposed[index],_=normalize_change({**proposed[index],'crop':fix.crop,'head_box':None},photo)
                    validate_variety(proposed,self.photo_data['scores'],self.dog.get('must_use_photos') or [])
                    other_sets=[{p['file'] for p in option} for j,option in enumerate(self.options) if j!=fix.option-1]
                    if {p['file'] for p in proposed} in other_sets:raise ValueError('Repair would duplicate another option.')
                    group=copy.deepcopy(self.options);group[fix.option-1]=proposed
                    validate_option_reuse(group)
                    self.options=group;self.log('option_repair',fix.model_dump())
                except (ValueError,StopIteration) as error:self.log('rejected_option_repair',str(error))
        self.overview()
        self.log('options_ready',{'overview':str(self.details/'options.jpg'),'remaining_findings':[f.model_dump() for f in review.findings]})

    def remember_feedback(self,feedback):
        answer=self.ask('Should I remember this for all dogs? [yes/no] ').strip().lower()
        self.log('memory_answer',answer)
        if answer in ('yes','y'):
            self.preferences['rules'].append(feedback)
            self.preferences_path.write_text(yaml.safe_dump(self.preferences,allow_unicode=True,sort_keys=False))
            self.log('preference_saved',feedback)
