"""Export the prepared local drafts for the static GitHub Pages demo; no AI calls."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / 'docs'
PHOTO_TYPES = {'.jpg', '.jpeg', '.png', '.heic', '.heif'}
PUBLIC_FACTS = ('name', 'sex', 'title', 'birth_date', 'birth_date_text', 'age_text',
                'life_stage', 'country', 'location', 'size', 'energy', 'social_notes',
                'story_notes', 'good_with_dogs', 'good_with_cats', 'good_with_children')


def jpeg(source, destination, *, crop=None, maximum=800, quality=68):
    """Strip metadata and export an oriented, compressed image, at most 800px."""
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
    original_size = image.size
    if crop:
        image = image.crop(crop)
    image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, 'JPEG', quality=quality, optimize=True, progressive=True,
               exif=b'', icc_profile=None)
    return original_size, image.size


def export_dog(row):
    identifier = row['id']
    if not re.fullmatch(r'[a-z0-9_-]+', identifier):
        raise ValueError('Invalid dog identifier.')
    folder = ROOT / 'input' / identifier
    details = ROOT / 'output' / identifier / 'details'
    dog = yaml.safe_load((folder / 'dog.yaml').read_text())
    caption = (details / 'caption.txt').read_text()
    if caption.splitlines()[0] != dog['name']:
        raise ValueError(f'Caption belongs to a different dog: {identifier}')
    record = {'id': identifier, 'name': dog['name'], 'facts':
              {key: dog.get(key) for key in PUBLIC_FACTS}, 'caption': caption,
              'photos': [], 'options': [], 'status': 'no photos yet'}
    draft_path = details / 'draft.json'
    if not draft_path.exists():
        return record
    draft = json.loads(draft_path.read_text())
    if len(draft.get('options', [])) != 3:
        return record
    scores_path = details / 'photo_scores.json'
    scores = {p['file']: p for p in json.loads(scores_path.read_text())['scores']['photos']}
    media = DOCS / 'media' / identifier
    sources = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_TYPES and p.is_file())
    mapping = {}
    for index, source in enumerate(sources, 1):
        photo_id = f'p{index:03d}'
        (width, height), (small_w, small_h) = jpeg(source, media / f'{photo_id}.jpg', maximum=720, quality=56)
        mapping[source.name] = {'id': photo_id, 'original_size': (width, height)}
        score = scores.get(source.name, {})
        record['photos'].append({'id': photo_id, 'url': f'media/{identifier}/{photo_id}.jpg',
            'width': small_w, 'height': small_h,
            'head': score.get('head_box'), 'body': score.get('dog_box')})
    for number, selected in enumerate(draft['options'], 1):
        jpeg(details / f'collage_{number}.jpg', media / f'option_{number}.jpg')
        photos = []
        for item in selected:
            source = mapping[item['file']]
            width, height = source['original_size']
            left, top, right, bottom = item['crop']
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError(f'Invalid prepared crop: {identifier}')
            photos.append({'id': source['id'], 'crop':
                           [left / width, top / height, right / width, bottom / height]})
        record['options'].append({'number': number, 'url': f'media/{identifier}/option_{number}.jpg',
                                  'photos': photos})
    jpeg(details / 'collage_1.jpg', media / 'title.jpg', crop=(0, 0, 536, 536), quality=80)
    record.update(title_url=f'media/{identifier}/title.jpg', status='waiting for approval')
    return record


def main():
    catalog = json.loads((ROOT / 'input' / 'dogs_catalog.json').read_text())
    records = []
    for row in catalog:
        record = export_dog(row)
        records.append(record)
        print(f"Exported {record['name']}: {len(record['photos'])} photos, {len(record['options'])} options", flush=True)
    if len([d for d in records if d['options']]) != 15:
        raise ValueError('Expected 15 prepared dogs with three options.')
    candidates = [ROOT / 'output/azra/video.mp4', ROOT / 'output/azra/details/approved_stage/video.mp4']
    video = next((p for p in candidates if p.is_file()), None)
    if video is None:
        raise ValueError('Azra’s prepared video was not found.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('Install ffmpeg to compress the example video.')
    subprocess.run([ffmpeg, '-nostdin', '-y', '-loglevel', 'error', '-i', str(video),
        '-map_metadata', '-1', '-vf', 'scale=720:-2', '-c:v', 'libx264', '-preset', 'medium',
        '-crf', '27', '-pix_fmt', 'yuv420p', '-an', '-movflags', '+faststart',
        str(DOCS / 'media/azra-example.mp4')], check=True)
    (DOCS / 'data').mkdir(exist_ok=True)
    (DOCS / 'data/dogs.json').write_text(json.dumps(records, ensure_ascii=False, default=str, separators=(',', ':')))
    (DOCS / '.nojekyll').touch()
    size = sum(p.stat().st_size for p in DOCS.rglob('*') if p.is_file())
    if size >= 50_000_000:
        raise ValueError(f'Demo is too large: {size / 1e6:.1f} MB')
    print(f'Demo export: {size / 1e6:.1f} MB; no originals or private technical files copied.')


if __name__ == '__main__':
    main()
