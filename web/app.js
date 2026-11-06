'use strict';
const $ = id => document.getElementById(id);
let current = null, busy = false, activeTile = null, dirty = false;

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
async function api(path, options) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'That change could not be saved.');
  return result;
}
function controls() {
  $('dog').disabled = busy;
  $('approver').disabled = busy;
  $('caption').disabled = busy || !current?.options.length;
  $('save-caption').disabled = busy || !dirty || !$('caption').value.trim();
  $('approve').disabled = busy || !current?.chosen || !$('approver').value.trim() || !$('caption').value.trim() || (current.status === 'approved' && !dirty);
  $('approve').textContent = current?.status === 'approved' && !dirty ? 'Approved ✓' : 'Approve & make video →';
  $('generate').disabled = busy || !current || current.photos.length < 4;
  document.querySelectorAll('.option-card button, .tile-targets button').forEach(button => button.disabled = busy);
  document.querySelectorAll('.photo-choice').forEach(button => {
    button.disabled = busy || !current?.chosen || !activeTile;
  });
  $('saved').textContent = dirty ? 'You have unsaved changes' : current?.caption ? 'Caption saved' : 'No caption loaded';
}
async function dogs(selected) {
  const list = await api('/api/dogs');
  $('dog').replaceChildren();
  list.forEach(dog => {
    const option = element('option', '', `${dog.name} — ${dog.status === 'waiting' ? 'waiting for approval' : dog.status}`);
    option.value = dog.id;
    $('dog').append(option);
  });
  const id = selected || list.find(dog => dog.id === 'azra')?.id || list[0]?.id;
  if (id) $('dog').value = id;
  return id;
}
function render() {
  $('dog-status').textContent = current.status === 'approved' ? 'Approved' : current.status === 'no photos yet' ? 'No photos yet' : 'Waiting for approval';
  $('dog-facts').textContent = [current.facts?.sex, current.facts?.age_text, current.facts?.size, current.facts?.country].filter(Boolean).join(' · ');
  $('dog-status').classList.toggle('approved', current.status === 'approved');
  $('options').replaceChildren();
  current.options.forEach(option => {
    const card = element('article', 'option-card' + (current.chosen === option.number ? ' chosen' : ''));
    const top = element('div', 'option-top');
    top.append(element('span', 'option-number', String(option.number)), element('span', 'selection-label', current.chosen === option.number ? 'YOUR CHOICE ✓' : ''));
    const image = element('img');
    image.src = option.url;
    image.alt = `${current.name}, collage option ${option.number}`;
    const button = element('button', 'secondary', current.chosen === option.number ? 'Chosen' : 'Choose this one');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(current.chosen === option.number));
    button.onclick = () => run('choose', {option: option.number}, `Choosing option ${option.number}…`);
    card.append(top, image, button);$('options').append(card);
  });
  if (!current.options.length) $('options').append(element('p', 'helper', current.photos.length < 4 ? `At least four photos are needed. ${current.name} has ${current.photos.length} so far.` : 'No options yet. Make three collages to get started.'));
  $('generate').classList.toggle('hidden', current.options.length > 0);
  const chosen = current.options.find(option => option.number === current.chosen);
  $('chosen-wrap').classList.toggle('hidden', !chosen);
  $('chosen-empty').classList.toggle('hidden', !!chosen);
  if (chosen) $('chosen-image').src = chosen.url;
  $('swap-notes').classList.toggle('hidden', !current.notes?.length);
  $('swap-notes').textContent = (current.notes || []).join(' ');
  $('photos-title').textContent = `All of ${current.name}'s photos`;
  $('photos').replaceChildren();
  const names = chosen ? chosen.photos.map(photo => photo.file) : [];
  const positions = ['top-right', 'bottom-left', 'bottom-right'];
  current.photos.forEach((photo, index) => {
    const button = element('button', 'photo-choice' + (names.includes(photo.file) ? ' in-collage' : ''));
    button.type = 'button';button.dataset.usable = String(photo.usable);
    const image = element('img');image.src = photo.url;image.alt = `${current.name}, photo ${index + 1}`;image.loading = 'lazy';
    const position = names.indexOf(photo.file);
    const description = position >= 0 ? `In collage · ${positions[position]}` : 'Use this photo';
    button.append(image, element('span', '', description), element('small', '', String(index + 1)));
    button.title = photo.file;
    button.onclick = () => {
      if (chosen?.photos[positions.indexOf(activeTile)]?.file === photo.file) { notice('That photo is already in this tile.');return; }
      run('swap', {option: current.chosen, tile: activeTile, photo: photo.file}, 'Checking and updating your photo…');
    };
    $('photos').append(button);
  });
  $('unknown').classList.toggle('hidden', !current.unknown.length);
  $('unknown').textContent = `Nog niet bekend: ${current.unknown.join(', ')}. You can leave these unknown; no facts will be guessed.`;
  document.querySelectorAll('.tile-targets button').forEach(button => {
    button.classList.toggle('active', button.dataset.tile === activeTile);
    button.setAttribute('aria-pressed', String(button.dataset.tile === activeTile));
  });
  $('tile-help').textContent = activeTile ? `Selected tile: ${activeTile}. Now choose a photo below.` : chosen ? 'Click a photo tile, then choose its replacement below.' : 'Choose an option above, then click a photo tile to replace it.';
  $('approval-help').textContent = current.status === 'approved' ? 'Approved files are ready below. Nothing has been posted.' : current.chosen ? `Option ${current.chosen} will be approved with the caption above. Making and checking the video can take a few minutes.` : 'Choose a collage before approving.';
  const approved = current.status === 'approved';
  $('result').classList.toggle('hidden', !approved);
  $('downloads').replaceChildren();
  if (approved) {
    const src = current.downloads['video.mp4'];
    if ($('video').getAttribute('src') !== src) $('video').src = src;
    [['collage.jpg', 'Download collage'], ['video.mp4', 'Download video'], ['caption.txt', 'Download caption']].forEach(([name, label]) => {
      const link = element('a', '', label);link.href = current.downloads[name] + (current.downloads[name].includes('?') ? '&' : '?') + 'download=1';link.download = name;$('downloads').append(link);
    });
  } else {
    $('video').pause();$('video').removeAttribute('src');$('video').load();
  }
  controls();
}
async function loadDog(id, preserveCaption = false) {
  current = await api(`/api/dogs/${encodeURIComponent(id)}`);
  if (!preserveCaption || !dirty) { $('caption').value = current.caption;dirty = false; }
  render();
}
async function run(action, payload, message) {
  if (busy || !current) return;
  const id = current.id;
  busy = true;controls();notice(message);
  try {
    const started = await api(`/api/dogs/${encodeURIComponent(id)}/${action}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    let job;
    do {
      await new Promise(resolve => setTimeout(resolve, 900));
      job = await api(`/api/jobs/${started.job}`);
    } while (job.status === 'running');
    if (job.status === 'error') throw new Error(job.error);
    if (action === 'caption' || action === 'approve' || action === 'generate') dirty = false;
    await dogs(id);await loadDog(id, true);
    notice(action === 'approve' ? 'Approved. Your video and downloads are ready — nothing has been posted.' : action === 'swap' ? 'Photo updated. Take another look at your collage.' : action === 'caption' ? 'Caption saved.' : action === 'generate' ? 'Three options are ready. Choose your favourite.' : 'Choice saved. You can fine-tune the photos below.');
    if (action === 'approve') $('result').scrollIntoView({behavior: 'smooth', block: 'start'});
  } catch (error) {
    notice(error.message, true);
    try { await loadDog(id, true); } catch (_) { /* Preserve the useful original error. */ }
  } finally { busy = false;controls(); }
}
$('dog').onchange = async () => {
  if (dirty && !window.confirm('Leave this unsaved caption? Choose Cancel to save it first.')) { $('dog').value = current.id;return; }
  dirty = false;activeTile = null;notice('');
  try { await loadDog($('dog').value); } catch (error) { notice(error.message, true); }
};
document.querySelectorAll('.tile-targets button').forEach(button => button.onclick = () => {
  activeTile = button.dataset.tile;render();
});
$('caption').oninput = () => { dirty = $('caption').value !== current?.caption;controls(); };
$('approver').oninput = controls;
$('save-caption').onclick = () => run('caption', {text: $('caption').value}, 'Saving your caption…');
$('generate').onclick = () => run('generate', {}, 'Choosing photos, checking the crops and writing a caption…');
$('approval-form').onsubmit = event => {
  event.preventDefault();
  run('approve', {option: current.chosen, name: $('approver').value.trim(), caption: $('caption').value}, 'Approved for rendering. Making and checking your animated video…');
};
(async () => {
  try { const id = await dogs();if (id) await loadDog(id);else notice('No dog folders found. Add a dog.yaml and photos to input/.'); }
  catch (error) { notice(error.message, true); }
})();
