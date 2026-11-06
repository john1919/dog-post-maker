'use strict';

// Static assets only. All choices, edits and demo approvals live in memory.
const $ = id => document.getElementById(id);
const positions = ['top-right', 'bottom-left', 'bottom-right'];
const imageCache = new Map();
let dogs = [], current = null, activeTile = null, busy = false;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').classList.toggle('hidden', !text);
  $('notice').classList.toggle('error', error);
}
function status(dog) {
  return !dog.options.length ? 'no photos yet' : dog.approved ? 'approved (demo)' : 'waiting for approval';
}
function controls() {
  $('dog').disabled = busy;
  $('caption').disabled = busy;
  $('approver').disabled = busy;
  $('approve').disabled = busy || !current?.chosen || !$('approver').value.trim() || !$('caption').value.trim() || current?.approved;
  $('approve').textContent = current?.approved ? 'Approved (demo) ✓' : 'Approve →';
  document.querySelectorAll('.option-card button, .tile-targets button').forEach(button => button.disabled = busy);
  document.querySelectorAll('.photo-choice').forEach(button => button.disabled = busy || !current?.chosen || !activeTile);
}
function refreshDropdown() {
  for (const option of $('dog').options) {
    const dog = dogs.find(item => item.id === option.value);
    if (dog) option.textContent = `${dog.name} — ${status(dog)}`;
  }
}
function displayFact(value) {
  return value === true ? 'Ja' : value === false ? 'Nee' : value === null || value === undefined || value === '' ? 'Nog niet bekend' : String(value);
}
function render() {
  const selected = current.options.find(option => option.number === current.chosen);
  $('dog-status').textContent = status(current);
  $('dog-status').classList.toggle('approved', !!current.approved);
  $('dog-facts').textContent = [current.facts.sex, current.facts.age_text, current.facts.size, current.facts.country].filter(Boolean).join(' · ');
  $('profile-facts').replaceChildren();
  const labels = {sex: 'Geslacht', birth_date_text: 'Geboren', country: 'Land', location: 'Verblijfplaats', size: 'Grootte',
    energy: 'Energie', good_with_dogs: 'Met honden', good_with_cats: 'Met katten', good_with_children: 'Met kinderen', story_notes: 'Verhaal'};
  for (const [key, label] of Object.entries(labels)) {
    $('profile-facts').append(element('dt', '', label), element('dd', '', displayFact(current.facts[key])));
  }
  $('options').replaceChildren();
  for (const option of current.options) {
    const card = element('article', 'option-card' + (option.number === current.chosen ? ' chosen' : ''));
    const top = element('div', 'option-top');
    top.append(element('span', 'option-number', String(option.number)),
      element('span', 'selection-label', option.number === current.chosen ? 'YOUR CHOICE ✓' : ''));
    const image = element('img');image.src = option.preview || option.url;
    image.alt = `${current.name}, collage option ${option.number}`;
    const button = element('button', 'secondary', option.number === current.chosen ? 'Chosen' : 'Choose this one');
    button.type = 'button';button.setAttribute('aria-pressed', String(option.number === current.chosen));
    button.onclick = () => {
      if (current.chosen !== option.number) current.approved = false;
      current.chosen = option.number;activeTile = null;render();
      notice('Choose a photo tile below to try a replacement. This is a demo; nothing is saved.');
    };
    card.append(top, image, button);$('options').append(card);
  }
  if (!current.options.length) $('options').append(element('p', 'helper', `${current.name} has no photos yet. The full tool needs at least four usable photos.`));
  $('chosen-wrap').classList.toggle('hidden', !selected);
  $('chosen-empty').classList.toggle('hidden', !!selected);
  if (selected) $('chosen-image').src = selected.preview || selected.url;
  $('tile-help').textContent = activeTile ? `Selected tile: ${activeTile}. Click any photo below to replace it.` :
    selected ? 'Click a photo tile, then choose any replacement below.' : 'Choose an option above, then click a photo tile to replace it.';
  $('swap-notes').classList.toggle('hidden', !current.tip);
  $('swap-notes').textContent = current.tip || '';
  $('photos-title').textContent = `All of ${current.name}'s photos`;
  $('photos').replaceChildren();
  for (const [index, photo] of current.photos.entries()) {
    const used = selected ? selected.photos.map((item, tile) => item.id === photo.id ? positions[tile] : null).filter(Boolean) : [];
    const button = element('button', 'photo-choice' + (used.length ? ' in-collage' : ''));
    button.type = 'button';
    const image = element('img');image.src = photo.url;image.loading = 'lazy';
    image.alt = `${current.name}, photo ${index + 1}`;
    button.append(image, element('span', '', used.length ? `In collage · ${used.join(', ')}` : 'Use this photo'),
      element('small', '', String(index + 1)));
    button.onclick = () => swapPhoto(photo);
    $('photos').append(button);
  }
  const unknown = Object.entries(labels).filter(([key]) => current.facts[key] === null || current.facts[key] === undefined || current.facts[key] === '').map(([, label]) => label);
  $('unknown').textContent = `Nog niet bekend: ${unknown.join(', ')}. The full tool asks for missing information; this demo uses the prepared facts.`;
  $('unknown').classList.toggle('hidden', !unknown.length);
  document.querySelectorAll('.tile-targets button').forEach(button => {
    button.classList.toggle('active', button.dataset.tile === activeTile);
    button.setAttribute('aria-pressed', String(button.dataset.tile === activeTile));
  });
  $('approval-help').textContent = current.approved ? `Approved in this demo only. No name, caption or approval is saved.` :
    selected ? `Option ${selected.number} is selected. Approve to see Azra's example video.` : 'Choose a collage before trying approval.';
  $('result').classList.toggle('hidden', !current.approved);
  if (current.approved) {
    if (!$('video').getAttribute('src')) $('video').src = 'media/azra-example.mp4';
  } else {
    $('video').pause();
    if ($('video').getAttribute('src')) {$('video').removeAttribute('src');$('video').load();}
  }
  refreshDropdown();controls();
}
function loadImage(url) {
  if (!imageCache.has(url)) imageCache.set(url, new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => {imageCache.delete(url);reject(new Error('This demo photo could not be loaded. Please try again.'));};
    image.src = url;
  }));
  return imageCache.get(url);
}
function squareCrop(photo, tile) {
  const w = photo.width, h = photo.height, maximum = Math.min(w, h);
  const valid = box => box && [box.left, box.top, box.right, box.bottom].every(Number.isFinite) && box.left < box.right && box.top < box.bottom;
  const head = valid(photo.head) ? {left: photo.head.left * w, right: photo.head.right * w, top: photo.head.top * h, bottom: photo.head.bottom * h} : null;
  let side = maximum;
  if (head && tile === 'top-right') side = Math.min(maximum, Math.max((head.right - head.left) * 2.5, (head.bottom - head.top) * 2.5));
  let cx = w / 2, cy = h / 2;
  const body = valid(photo.body) ? photo.body : null;
  if (head && tile === 'top-right') {cx = (head.left + head.right) / 2;cy = (head.top + head.bottom) / 2;}
  else if (body) {cx = (body.left + body.right) * w / 2;cy = (body.top + body.bottom) * h / 2;}
  let left = Math.max(0, Math.min(w - side, cx - side / 2));
  let top = Math.max(0, Math.min(h - side, cy - side / 2));
  let warning = false;
  if (head) {
    const margin = Math.max(head.right - head.left, head.bottom - head.top) * .12;
    const hl = Math.max(0, head.left - margin), hr = Math.min(w, head.right + margin);
    const ht = Math.max(0, head.top - margin), hb = Math.min(h, head.bottom + margin);
    side = Math.min(maximum, Math.max(side, hr - hl, hb - ht));
    const minX = Math.max(0, hr - side), maxX = Math.min(w - side, hl);
    const minY = Math.max(0, hb - side), maxY = Math.min(h - side, ht);
    if (minX <= maxX && minY <= maxY) {
      left = Math.max(minX, Math.min(maxX, cx - side / 2));top = Math.max(minY, Math.min(maxY, cy - side / 2));
    } else {left = Math.max(0, Math.min(w - side, cx - side / 2));top = Math.max(0, Math.min(h - side, cy - side / 2));warning = true;}
  }
  return {crop: [left / w, top / h, (left + side) / w, (top + side) / h], warning};
}
async function redraw(option) {
  const canvas = document.createElement('canvas');canvas.width = canvas.height = 1080;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Your browser cannot redraw this demo collage.');
  const urls = [current.title_url, ...option.photos.map(item => current.photos.find(photo => photo.id === item.id).url)];
  const images = await Promise.all(urls.map(loadImage));
  context.fillStyle = '#7AA3B8';context.fillRect(0, 0, 1080, 1080);
  context.drawImage(images[0], 0, 0, 536, 536);
  const destinations = [[544, 0], [0, 544], [544, 544]];
  option.photos.forEach((item, index) => {
    const image = images[index + 1], [left, top, right, bottom] = item.crop;
    context.drawImage(image, left * image.width, top * image.height, (right - left) * image.width,
      (bottom - top) * image.height, ...destinations[index], 536, 536);
  });
  return canvas.toDataURL('image/jpeg', .9);
}
async function swapPhoto(photo) {
  if (busy || !current?.chosen || !activeTile) return;
  busy = true;controls();notice('Redrawing the collage in your browser…');
  try {
    const option = current.options.find(item => item.number === current.chosen);
    const candidate = {...option, photos: option.photos.map(item => ({...item, crop: [...item.crop]}))};
    const framed = squareCrop(photo, activeTile);
    candidate.photos[positions.indexOf(activeTile)] = {id: photo.id, crop: framed.crop};
    const preview = await redraw(candidate);
    option.photos = candidate.photos;option.preview = preview;current.approved = false;
    current.tip = framed.warning ? 'The head may not fit this square crop. Your choice is kept; you can try another photo.' :
      'Photo replaced. The demo crops locally using the saved framing hints; the full tool also checks the result with AI.';
    notice('Photo updated. Nothing was uploaded or saved.');
  } catch (error) {notice(error.message, true);}
  finally {busy = false;render();}
}
$('dog').onchange = () => {
  current = dogs.find(dog => dog.id === $('dog').value);activeTile = null;
  $('caption').value = current.caption;notice('');render();
};
document.querySelectorAll('.tile-targets button').forEach(button => button.onclick = () => {activeTile = button.dataset.tile;render();});
$('caption').oninput = () => {
  current.caption = $('caption').value;
  if (current.approved) {current.approved = false;render();}
  else controls();
};
$('approver').oninput = controls;
$('approval-form').onsubmit = event => {
  event.preventDefault();
  if (busy || !current?.chosen || !$('approver').value.trim() || !$('caption').value.trim()) return;
  current.approved = true;render();
  notice('Demo approval complete. Nothing is saved or posted. The video below is Azra’s example.');
  $('result').scrollIntoView({behavior: 'smooth', block: 'start'});
};
(async () => {
  try {
    const response = await fetch('data/dogs.json');
    if (!response.ok) throw new Error('The demo data could not be loaded. Please refresh.');
    dogs = await response.json();
    $('dog').replaceChildren(...dogs.map(dog => {const option = element('option', '', `${dog.name} — ${status(dog)}`);option.value = dog.id;return option;}));
    current = dogs.find(dog => dog.id === 'azra') || dogs[0];
    if (!current) throw new Error('No demo dogs found.');
    $('dog').value = current.id;$('caption').value = current.caption;render();
  } catch (error) {notice(error.message, true);}
})();
