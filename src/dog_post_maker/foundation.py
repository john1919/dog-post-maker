"""Read-only foundation catalog and recursive PHOTO_ROOT media staging."""
import ast
from collections import Counter
import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

PHOTOS={'.jpg','.jpeg','.png','.heic','.heif'}
VIDEOS={'.mp4','.mov'}
MONTHS={name:i for i,name in enumerate(('januari','februari','maart','april','mei','juni','juli','augustus','september','oktober','november','december'),1)}


def read_catalog(app_path):
    """Read literal dog(...) entries with AST; never execute the colleague's app."""
    tree=ast.parse(Path(app_path).read_text())
    assignment=next(node for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='DOGS' for t in node.targets))
    result=[]
    fields=('id','name','country','profileUrl','sex','age','size','image')
    for call in assignment.value.elts:
        if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name) or call.func.id!='dog':
            raise ValueError('DOGS must contain literal dog(...) entries.')
        row=dict(zip(fields,[ast.literal_eval(arg) for arg in call.args]))
        row.update({'dogs':'unknown','cats':'unknown','children':'unknown','energy':'unknown','environment':[],'note':''})
        row.update({key.arg:ast.literal_eval(key.value) for key in call.keywords})
        result.append(row)
    return result


def normalize_name(value):
    return unicodedata.normalize('NFC',value).strip().casefold()


def matching_folders(photo_root,name):
    """Match the dog before '('; permit a descriptor after a comma (Cara)."""
    if not photo_root.is_dir():return []
    return sorted(path for path in photo_root.iterdir() if path.is_dir()
                  and not normalize_name(path.name).startswith('blanco dossier')
                  and normalize_name(path.name.split('(',1)[0].split(',',1)[0])==normalize_name(name))


def media_files(photo_root,name):
    result=[]
    for folder in matching_folders(photo_root,name):
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix.lower() in PHOTOS|VIDEOS and not any(
                part.startswith('.') or normalize_name(part).startswith('blanco dossier') for part in path.relative_to(folder).parts):
                result.append(path)
    return sorted(result)


def photo_root(root):
    load_dotenv(root/'.env')
    value=os.environ.get('PHOTO_ROOT','').strip()
    return Path(value).expanduser() if value else None


def write_json(path,data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2));temporary.replace(path)


def stage_media(root,identifier,name):
    """Create local aliases, preserving originals and triggering no cloud writes."""
    external=photo_root(root)
    if external is None:return None
    folder=root/'input'/identifier;folder.mkdir(parents=True,exist_ok=True)
    manifest=folder/'media_sources.json'
    files=media_files(external,name)
    counts=Counter(normalize_name(p.name) for p in files)
    mapped={}
    for source in files:
        alias=source.name if counts[normalize_name(source.name)]==1 else hashlib.sha256(str(source.relative_to(external)).encode()).hexdigest()[:10]+'_'+source.name
        mapped[alias]=str(source.absolute())
    # Retire removed aliases and previous copied media inside this workspace only.
    archive=None
    for local in list(folder.iterdir()):
        if local.suffix.lower() not in PHOTOS|VIDEOS:continue
        desired=mapped.get(local.name)
        if desired and local.is_symlink() and os.readlink(local)==desired:continue
        if archive is None:
            archive=folder/'.previous_media'/datetime.now().strftime('%Y%m%d-%H%M%S-%f');archive.mkdir(parents=True)
        shutil.move(str(local),str(archive/local.name))
    for alias,target in mapped.items():
        local=folder/alias
        if not local.is_symlink():local.symlink_to(target)
    data={'photo_root':str(external),'files':mapped}
    if not manifest.exists() or json.loads(manifest.read_text())!=data:write_json(manifest,data)
    return {'photos':sum(Path(n).suffix.lower() in PHOTOS for n in mapped),
            'videos':sum(Path(n).suffix.lower() in VIDEOS for n in mapped),
            'folders':[str(p) for p in matching_folders(external,name)]}


def media_path(folder,filename):
    if Path(filename).name!=filename:raise ValueError('Invalid media filename.')
    path=folder/filename
    if path.is_symlink():
        manifest=folder/'media_sources.json'
        files=json.loads(manifest.read_text()).get('files',{}) if manifest.exists() else {}
        if files.get(filename)!=os.readlink(path):raise ValueError('Unregistered media link.')
    elif path.resolve().parent!=folder.resolve():raise ValueError('Invalid media path.')
    return path


def birth_date(age):
    text=age.casefold();year=re.search(r'\b(20\d{2}|19\d{2})\b',text)
    if not year:return ''
    # Range/season qualifiers are kept verbatim in birth_date_text, never invented.
    month=next((n for word,n in MONTHS.items() if re.search(r'\b'+word+r'\b',text)),None)
    if month and not re.search(r'(mei.juni|herfst|winter|zomer|lente)',text):
        day=re.search(r'geboren\s+(\d{1,2})\s+',text)
        return f'{year[0]}-{month:02d}'+(f'-{int(day[1]):02d}' if day else '')
    return year[0]


def sync_catalog(root,app_path=None):
    source=app_path or Path.home()/'Documents/RescueAI_match/app.py'
    catalog=read_catalog(source);input_dir=root/'input';input_dir.mkdir(exist_ok=True)
    write_json(input_dir/'dogs_catalog.json',catalog)
    results=[]
    for row in catalog:
        folder=input_dir/row['id'];folder.mkdir(exist_ok=True);path=folder/'dog.yaml'
        dog=yaml.safe_load(path.read_text()) if path.exists() else {}
        dog=dog or {}
        compatibility=lambda key:True if row[key]=='yes' else False if row[key]=='no' else None
        age=row['age'];born=age.split('·',1)[-1].strip()
        dog.update(name=row['name'],sex=row['sex'],country=row['country'],birth_date=birth_date(age),
                   birth_date_text=born[:1].upper()+born[1:],age_text=age,life_stage=age.split('·')[0].strip(),
                   size=row['size'],good_with_dogs=compatibility('dogs'),good_with_cats=compatibility('cats'),
                   good_with_children=compatibility('children'),energy={'high':'Hoog','medium':'Gemiddeld','low':'Laag'}.get(row['energy'],''),
                   story_notes=row['note'],profile_url=row['profileUrl'],environment=list(row['environment']))
        dog.setdefault('location','');dog.setdefault('title',f"{row['name']} in NL")
        # Structured compatibility is authoritative; do not retain contradictory legacy notes.
        dog['social_notes']=''
        path.write_text(yaml.safe_dump(dog,allow_unicode=True,sort_keys=False))
        media=stage_media(root,row['id'],row['name']) or {'photos':0,'videos':0,'folders':[]}
        results.append({'id':row['id'],'name':row['name'],**media})
    return results
