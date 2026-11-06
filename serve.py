"""Local volunteer UI. Run: uv run serve.py --port 8787."""
import argparse
import io
import json
import mimetypes
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, unquote, urlparse, quote

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))

import yaml
from PIL import Image, ImageOps
from dog_post_maker.foundation import stage_media, media_path
from dog_post_maker.workflow import (
    make_options, swap_photo, save_caption, approve, _save, _archive_outputs,
)

PHOTO_TYPES = {'.jpg', '.jpeg', '.png', '.heic', '.heif'}
FINAL_FILES = {'collage.jpg', 'video.mp4', 'caption.txt', 'approval.txt'}
DRAFT_IMAGES = {'collage_1.jpg', 'collage_2.jpg', 'collage_3.jpg', 'options.jpg'}


def dog_folder(slug, *, refresh=True):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', slug):
        raise ValueError('Invalid dog name.')
    path = ROOT / 'input' / slug
    if not (path / 'dog.yaml').is_file():
        raise ValueError('Dog not found.')
    catalog=ROOT/'input'/'dogs_catalog.json'
    if refresh and catalog.exists():
        row=next((r for r in json.loads(catalog.read_text()) if r['id']==slug),None)
        if row:stage_media(ROOT,slug,row['name'])
    return path


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def dog_state(slug):
    folder = dog_folder(slug)
    dog = yaml.safe_load((folder / 'dog.yaml').read_text())
    out = ROOT / 'output' / slug
    details = out / 'details'
    draft = read_json(details / 'draft.json', {})
    approved = bool(draft.get('approved') and all((out / name).is_file() for name in FINAL_FILES))
    chosen = draft.get('chosen_option')
    options = draft.get('options', [])
    selected = options[chosen - 1] if chosen in (1, 2, 3) and len(options) == 3 else []
    selected_names = {item['file'] for item in selected}
    scores = read_json(details / 'photo_scores.json', {}).get('scores', {}).get('photos', [])
    usability = {photo['file']: photo['usable'] for photo in scores}
    caption_path = details / 'caption.txt'
    caption = caption_path.read_text() if caption_path.exists() else ''
    unknown_names = {'birth_date': 'Birth date', 'location': 'Current location',
                     'country': 'Country', 'size': 'Size', 'energy': 'Energy',
                     'good_with_dogs': 'Dogs', 'good_with_cats': 'Cats',
                     'good_with_children': 'Children', 'story_notes': 'Story'}
    unknown = [label for key, label in unknown_names.items()
               if dog.get(key) is None or (isinstance(dog.get(key), str) and not dog[key].strip())]
    version = str((details / 'options.jpg').stat().st_mtime_ns) if (details / 'options.jpg').exists() else '0'
    photos=[p for p in sorted(folder.iterdir()) if p.suffix.lower() in PHOTO_TYPES and p.is_file()]
    status='no photos yet' if len(photos)<4 else 'approved' if approved else 'waiting'
    facts={key:dog.get(key) for key in ('sex','country','age_text','birth_date_text','size','energy','good_with_dogs','good_with_cats','good_with_children','story_notes','profile_url')}
    return {'id': slug, 'name': dog.get('name', slug), 'status': status, 'facts':facts, 'photo_count':len(photos),
            'chosen': chosen, 'options': [{'number': i + 1,
                'url': f'/drafts/{slug}/collage_{i+1}.jpg?v={version}', 'photos': option}
                for i, option in enumerate(options) if (details / f'collage_{i+1}.jpg').exists()],
            'photos': [{'file': p.name, 'url': f'/photos/{slug}/{quote(p.name,safe="")}',
                        'selected': p.name in selected_names, 'usable': usability.get(p.name, True)}
                       for p in photos],
            'caption': caption, 'unknown': unknown, 'notes': draft.get('manual_notes', {}).get(str(chosen), []),
            'downloads': {name: f'/files/{slug}/{name}?v={(out / "approval.txt").stat().st_mtime_ns}' for name in FINAL_FILES} if approved else {}}


def select_option(slug, number):
    dog_folder(slug)
    if type(number) is not int or number not in (1, 2, 3):
        raise ValueError('Choose option 1, 2 or 3.')
    out = ROOT / 'output' / slug
    path = out / 'details' / 'draft.json'
    state = read_json(path, {})
    if len(state.get('options', [])) != 3:
        raise ValueError('Generate the collage options first.')
    if state.get('approved') and state.get('chosen_option') != number:
        _archive_outputs(out, out / 'details')
        state.update(approved=False, approver_name=None)
    state['chosen_option'] = number
    _save(path, state)
    events_path=out / 'details' / 'options_events.json'
    events=read_json(events_path, [])
    events.append({'time':datetime.now(ZoneInfo('Europe/Berlin')).isoformat(), 'event':'web_option_selected', 'data':{'option':number}})
    _save(events_path, events)
    notes=out / 'details' / 'review_notes.txt'
    with notes.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(events[-1], ensure_ascii=False)+'\n')
    return dog_state(slug)


class Jobs:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()
        self.items = {}
        self.active = False

    def start(self, action):
        with self.lock:
            if self.active:
                raise ValueError('Another change is still running. Please wait for it to finish.')
            self.active = True
            job_id = uuid.uuid4().hex
            self.items[job_id] = {'status': 'running'}
        def work():
            try:
                action()
                result = {'status': 'done'}
            except Exception as error:
                result = {'status': 'error', 'error': str(error)}
            with self.lock:
                self.items[job_id] = result
                self.active = False
        self.pool.submit(work)
        return job_id


JOBS = Jobs()


def perform(slug, action, payload):
    folder = dog_folder(slug)
    if action == 'generate':
        return make_options(folder)
    if action == 'choose':
        return select_option(slug, payload.get('option'))
    if action == 'swap':
        return swap_photo(folder, payload.get('option'), payload.get('tile'), payload.get('photo'))
    if action == 'caption':
        return save_caption(folder, payload.get('text'))
    if action == 'approve':
        name = payload.get('name', '').strip()
        if not name:
            raise ValueError('Enter your name before approving.')
        text = payload.get('caption')
        if text is not None and text != dog_state(slug)['caption']:
            save_caption(folder, text)
        # approve() calls make_video() once, then publishes the receipt last.
        return approve(folder, payload.get('option'), name)
    raise ValueError('Unknown action.')


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, data, content_type, status=200, download=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(data)

    def json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == '/api/dogs':
                catalog=read_json(ROOT/'input'/'dogs_catalog.json',None)
                ids=[row['id'] for row in catalog] if catalog is not None else [p.name for p in sorted((ROOT/'input').iterdir()) if p.is_dir() and (p/'dog.yaml').exists()]
                dogs=[dog_state(identifier) for identifier in ids]
                return self.json([{'id': dog['id'], 'name': dog['name'], 'status': dog['status'], 'facts':dog['facts'], 'photo_count':dog['photo_count']} for dog in dogs])
            match = re.fullmatch(r'/api/dogs/([A-Za-z0-9_-]+)', path)
            if match:
                return self.json(dog_state(match[1]))
            match = re.fullmatch(r'/api/jobs/([a-f0-9]+)', path)
            if match:
                with JOBS.lock:
                    job = JOBS.items.get(match[1])
                return self.json(job or {'error': 'Job not found'}, 200 if job else 404)
            match = re.fullmatch(r'/(drafts|photos|files)/([A-Za-z0-9_-]+)/([^/]+)', path)
            if match:
                kind, slug, filename = match.groups()
                # State/actions refresh the index. Thumbnail requests reuse it,
                # avoiding a recursive cloud-folder scan for every image.
                folder = dog_folder(slug,refresh=False)
                if kind == 'photos':
                    source = media_path(folder,filename)
                    if source.suffix.lower() not in PHOTO_TYPES:
                        raise ValueError('Photo not allowed.')
                    with Image.open(source) as raw:
                        image = ImageOps.exif_transpose(raw).convert('RGB')
                        image.thumbnail((1100, 1100))
                        buffer = io.BytesIO();image.save(buffer, 'JPEG', quality=88)
                    return self.send_bytes(buffer.getvalue(), 'image/jpeg')
                allowed = DRAFT_IMAGES if kind == 'drafts' else FINAL_FILES
                if filename not in allowed:
                    raise ValueError('File not allowed.')
                base = ROOT / 'output' / slug
                source = (base / 'details' if kind == 'drafts' else base) / filename
                if kind == 'files' and dog_state(slug)['status'] != 'approved':
                    raise ValueError('This post has not been approved yet.')
                return self.send_bytes(source.read_bytes(), mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                                       download=filename if 'download' in parse_qs(parsed.query) else None)
            static = {'/': 'index.html', '/index.html': 'index.html', '/styles.css': 'styles.css', '/app.js': 'app.js'}
            if path in static:
                file = ROOT / 'web' / static[path]
                return self.send_bytes(file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'text/plain')
            self.json({'error': 'Not found'}, 404)
        except (ValueError, OSError) as error:
            self.json({'error': str(error)}, 400)

    def do_POST(self):
        # Only the local page may initiate mutations; no permissive CORS.
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if host not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}') or (origin and origin not in (f'http://{host}',)):
            return self.json({'error': 'Local origin required'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 131072:
                raise ValueError('Invalid request size.')
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):raise ValueError('Expected an object.')
            match = re.fullmatch(r'/api/dogs/([A-Za-z0-9_-]+)/(generate|choose|swap|caption|approve)', self.path)
            if not match:raise ValueError('Unknown action.')
            slug, action = match.groups();dog_folder(slug)
            job = JOBS.start(lambda: perform(slug, action, payload))
            self.json({'job': job}, 202)
        except (ValueError, TypeError) as error:
            self.json({'error': str(error)}, 400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Local volunteer page: http://127.0.0.1:{args.port}', flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();JOBS.pool.shutdown(wait=False)


if __name__ == '__main__':
    main()
