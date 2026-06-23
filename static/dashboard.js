let allCommands = [];
let allFaceGreetings = [];
let activeCommandType = 'ros_script';
let editingCommand = null;

const typeTitles = {
  ros_script: 'ROS Scripts',
  start_app: 'Applications',
  promobot_tts: 'Promobot TTS',
  set_language: 'Language Switch',
  mute: 'Mute',
  media: 'Media Dialogs',
  face_greeting: 'Face Greetings',
  shell_command: 'Shell Commands',
  multi_action: 'Multi Actions',
  none: 'None / Blocked'
};

const actionLabelByType = {
  ros_script: 'Robot Action',
  start_app: 'Robot Action',
  promobot_tts: 'Robot Response',
  set_language: 'Required Language',
  mute: 'Mute State',
  media: 'Media / Background File',
  face_greeting: 'Person Greeting',
  shell_command: 'Shell Command',
  multi_action: 'Action Steps',
  none: 'Action Value'
};

const movementScripts = [
  'hello', 'get_hand_boy', 'get_five', 'dance1', 'dance',
  'talk1', 'talk2', 'talk3', 'talk5', 'talk7', 'talk10'
];

function movementPickerHtml(selectedValue = '', inputId = 'actionValueInput') {
  const selected = String(selectedValue || '').trim();
  const chips = movementScripts.map(name => `
    <button type="button" class="motion-chip ${name === selected ? 'selected' : ''}" data-motion="${escapeHtml(name)}" onclick="selectMovement('${inputId}', '${escapeHtml(name)}')">${escapeHtml(name)}</button>
  `).join('');
  return `
    <label>Robot Action <span class="muted">Movement only</span>
      <input id="${inputId}" value="${escapeHtml(selected)}" placeholder="Select movement from the slider below">
    </label>
    <div class="motion-selector">
      <div class="motion-selector-head">
        <span>Available movements</span>
        <button type="button" class="btn small" onclick="testSelectedMovement('${inputId}')">Test Selected</button>
      </div>
      <div class="motion-scroll">${chips}</div>
    </div>
  `;
}

function selectMovement(inputId, name) {
  const input = document.getElementById(inputId);
  if (input) input.value = name;
  const container = input ? input.closest('.drawer-body') : document;
  container.querySelectorAll('.motion-chip').forEach(chip => {
    chip.classList.toggle('selected', chip.dataset.motion === name);
  });
}

async function testSelectedMovement(inputId) {
  const value = (document.getElementById(inputId)?.value || '').trim();
  if (!value) return toast('Select a movement first');
  const robot_id = document.getElementById('robotId')?.value || 'promobot_v4_0445';
  const command = {
    name: 'Test Movement: ' + value,
    enabled: true,
    trigger_mode: 'remote_only',
    language: 'all',
    phrases: [],
    match_type: 'contains',
    priority: 0,
    action_type: 'ros_script',
    action_value: value,
    reply_text: '',
    notes: 'Temporary movement test from dashboard'
  };
  const r = await fetch('/api/remote/execute', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({robot_id, command})});
  const j = await r.json();
  toast(j.message || 'Movement queued');
}


function toast(message) {
  const el = document.getElementById('toast');
  if (!el) return alert(message);
  el.textContent = message;
  el.classList.add('open');
  setTimeout(() => el.classList.remove('open'), 2200);
}

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

function setupTabs() {
  document.querySelectorAll('[data-tabs]').forEach(group => {
    group.querySelectorAll('.tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        group.querySelectorAll('.tab-btn').forEach(x => x.classList.remove('active'));
        btn.classList.add('active');
        const id = btn.dataset.tab;
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        const pane = document.getElementById('tab-' + id);
        if (pane) pane.classList.add('active');
      });
    });
  });
}

function setupSecretToggles() {
  document.querySelectorAll('[data-toggle-secret]').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = btn.parentElement.querySelector('input');
      input.type = input.type === 'password' ? 'text' : 'password';
      btn.textContent = input.type === 'password' ? 'Show' : 'Hide';
    });
  });
}

async function saveSettings() {
  const form = document.getElementById('settingsForm');
  const fd = new FormData(form);
  const payload = Object.fromEntries(fd.entries());
  if (form.querySelector('[name="use_elevenlabs_for_arabic"]')) {
    payload.use_elevenlabs_for_arabic = form.querySelector('[name="use_elevenlabs_for_arabic"]').checked;
  }
  const r = await fetch('/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const j = await r.json();
  toast(j.message || 'Saved');
}

async function savePrompt() {
  const form = document.getElementById('promptForm');
  const payload = Object.fromEntries(new FormData(form).entries());
  const r = await fetch('/prompt', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const j = await r.json();
  toast(j.message || 'Saved');
}

function resetPromptForm() {
  window.location.reload();
}

async function testHealth() {
  const r = await fetch('/api/health');
  const j = await r.json();
  toast(j.ok ? 'Connection OK' : 'Connection failed');
}

async function loadCommands() {
  const r = await fetch('/api/commands');
  const j = await r.json();
  allCommands = j.commands || [];
  try {
    const fr = await fetch('/api/face-greetings');
    const fj = await fr.json();
    allFaceGreetings = fj.face_greetings || [];
  } catch(e) { allFaceGreetings = []; }
  renderCommands();
}

function setActiveCommandType(type) {
  activeCommandType = type;
  document.querySelectorAll('.cmd-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.type === type));
  const title = document.getElementById('activeTypeTitle');
  if (title) title.textContent = typeTitles[type] || type;
  history.replaceState(null, '', '#'+type);
  renderCommands();
}

function commandSearchHaystack(c) {
  return [
    c.name,
    c.trigger_mode,
    c.language,
    c.action_value,
    c.reply_text,
    c.extra_action_type,
    c.extra_action_value,
    c.required_language,
    c.mute_state,
    c.person_name,
    c.recognition_type,
    c.response_text,
    c.cooldown_sec,
    c.media_duration_sec,
    ...(c.phrases || [])
  ].join(' ').toLowerCase();
}

function renderCommands() {
  const tbody = document.querySelector('#commandsTable tbody');
  if (!tbody) return;
  const search = (document.getElementById('commandSearch')?.value || '').toLowerCase();
  const sourceItems = activeCommandType === 'face_greeting' ? allFaceGreetings : allCommands;
  const items = sourceItems.filter(c => {
    if (activeCommandType === 'face_greeting') return true;
    if (activeCommandType === 'media') return ['show_photo','show_video','set_background_image','set_background_video'].includes(c.action_type);
    return c.action_type === activeCommandType;
  }).filter(c => !search || commandSearchHaystack(c).includes(search));
  const count = document.getElementById('activeTypeCount');
  if (count) count.textContent = `${items.length} commands`;
  tbody.innerHTML = '';
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">No commands in this tab.</td></tr>`;
    return;
  }
  for (const c of items) {
    const phrases = (c.phrases || []).length ? (c.phrases || []).map(p => `<span class="pill">${escapeHtml(p)}</span>`).join('') : '<span class="muted">none</span>';
    const actionValue = displayActionValue(c);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${c.id}</td>
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.trigger_mode)}</td>
      <td>${escapeHtml(c.language)}</td>
      <td>${phrases}</td>
      <td>${escapeHtml(actionValue)}</td>
      <td>${c.priority}</td>
      <td><span class="${c.enabled ? 'status-enabled' : 'status-disabled'}">${c.enabled ? 'Enabled' : 'Disabled'}</span></td>
      <td><div class="row-actions"><button class="btn small" onclick='editCommand(${c.id})'>Edit</button>${activeCommandType === 'face_greeting' ? '' : `<button class="btn small" onclick='runCommand(${c.id})'>Run</button>`}<button class="btn small danger" onclick='deleteCommand(${c.id})'>Delete</button></div></td>
    `;
    tbody.appendChild(tr);
  }
}

function displayActionValue(c) {
  if (activeCommandType === 'face_greeting' || c.person_name !== undefined || c.recognition_type !== undefined) {
    const kind = (c.recognition_type || 'known') === 'unknown' ? 'Unknown face' : (c.person_name || 'Known face');
    return `${kind} → ${c.response_text || ''} | ${c.action_value || 'none'}`;
  }
  if (c.action_type === 'promobot_tts') {
    let text = c.action_value || '';
    if (c.extra_action_type && c.extra_action_value) text += ` | ${c.extra_action_type}: ${c.extra_action_value}`;
    return text;
  }
  if (c.action_type === 'show_photo') return `Photo Dialog → ${c.action_value || ''} | ${c.media_duration_sec || 5}s`;
  if (c.action_type === 'show_video') return `Video Dialog → ${c.action_value || ''}`;
  if (c.action_type === 'set_background_image') return `Background Image → ${c.action_value || ''}`;
  if (c.action_type === 'set_background_video') return `Background Video → ${c.action_value || ''}`;
  if (c.action_type === 'set_language') return c.required_language || c.action_value || '';
  if (c.action_type === 'mute') return c.mute_state || c.action_value || '';
  return c.action_value || '';
}

function commonFieldsHtml(type, command) {
  const phrasesLabel = type === 'ros_script' || type === 'start_app' ? 'Phrase robot should hear' : 'Phrases optional';
  let triggerOptions = `<option value="voice_remote">Voice + Remote</option><option value="remote_only">Remote Only</option>`;
  if (type === 'promobot_tts' || type === 'set_language' || type === 'mute') triggerOptions = `<option value="remote_only">Remote Only</option>`;

  const triggerValue = command?.trigger_mode || ((type === 'promobot_tts' || type === 'set_language' || type === 'mute') ? 'remote_only' : 'voice_remote');
  return `
    <label>Name<input name="name" id="cmdName" value="${escapeHtml(command?.name || '')}" required></label>
    <div class="form-grid two compact-grid">
      <label>Enable or Disable<select name="enabled" id="cmdEnabled"><option value="true">Enabled</option><option value="false">Disabled</option></select></label>
      <label>Trigger Mode<select name="trigger_mode" id="cmdTrigger">${triggerOptions}</select></label>
      <label>Languages<select name="language" id="cmdLanguage"><option value="all">All</option><option value="ar">Arabic</option><option value="en">English</option><option value="ku">Kurdish</option></select></label>
      <label>Match Type<select name="match_type" id="cmdMatch"><option value="contains">Contains</option><option value="exact">Exact</option><option value="regex">Regex</option></select></label>
      <label>Priority<input type="number" name="priority" id="cmdPriority" value="${escapeHtml(command?.priority ?? 50)}"></label>
    </div>
    ${type !== 'mute' && type !== 'set_language' && type !== 'promobot_tts' ? `<label>${phrasesLabel}<textarea name="phrases" id="cmdPhrases" class="short-textarea" placeholder="One phrase per line">${escapeHtml((command?.phrases || []).join('\n'))}</textarea></label>` : ''}
  `;
}

function actionFields(type, command = {}) {
  const value = command.action_value || '';
  if (type === 'ros_script') return `
    ${commonFieldsHtml(type, command)}
    <label>Robot Response<input name="reply_text" id="cmdReply" value="${escapeHtml(command.reply_text || '')}"></label>
    ${movementPickerHtml(value, 'actionValueInput')}
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  if (type === 'start_app') return `
    ${commonFieldsHtml(type, command)}
    <label>Robot Response<input name="reply_text" id="cmdReply" value="${escapeHtml(command.reply_text || '')}"></label>
    <label>Robot Action <span class="muted">Apps only</span><input id="actionValueInput" value="${escapeHtml(value)}" placeholder="promobot_example_app_camerae999999"></label>
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  if (type === 'promobot_tts') return `
    ${commonFieldsHtml(type, command)}
    <label>Robot Response<textarea id="actionValueInput" class="short-textarea" placeholder="Text robot should speak">${escapeHtml(value)}</textarea></label>
    <div class="form-grid two compact-grid">
      <label>Optional Robot Action<select id="extraActionType"><option value="">None</option><option value="ros_script">Movement</option><option value="start_app">App</option></select></label>
      <label>Optional Action Value<input id="extraActionValue" value="${escapeHtml(command.extra_action_value || '')}" placeholder="script or app folder"></label>
    </div>
    <input type="hidden" id="cmdPhrases" value="">
    <input type="hidden" id="cmdReply" value="">
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  if (type === 'set_language') return `
    ${commonFieldsHtml(type, command)}
    <label>Robot Action <span class="muted">Change language</span><select id="actionValueInput"><option value="ar_AE">Arabic ar_AE</option><option value="en_US">English en_US</option><option value="ku_IQ">Kurdish ku_IQ</option></select></label>
    <input type="hidden" id="cmdPhrases" value="">
    <input type="hidden" id="cmdReply" value="">
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  if (type === 'mute') return `
    ${commonFieldsHtml(type, command)}
    <label>Active or Deactive<select id="actionValueInput"><option value="active">Active mute</option><option value="deactive">Deactive mute</option></select></label>
    <input type="hidden" id="cmdPhrases" value="">
    <input type="hidden" id="cmdReply" value="">
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  if (type === 'media') {
    const mediaKind = ['show_photo','show_video','set_background_image','set_background_video'].includes(command.action_type) ? command.action_type : 'show_photo';
    const isVideo = mediaKind === 'show_video' || mediaKind === 'set_background_video';
    const pathHint = isVideo ? '/home/promobot/config/video' : '/home/promobot/config/photo';
    const placeholder = isVideo ? 'intro.mp4 or background.mp4' : 'welcome.jpg, offer.png, or background.jpg';
    return `
    ${commonFieldsHtml(type, command)}
    <label>Media Action<select id="mediaKind" onchange="updateMediaKindUi()">
      <option value="show_photo">Show Photo Dialog</option>
      <option value="show_video">Show Video Dialog</option>
      <option value="set_background_image">Set App Background Image</option>
      <option value="set_background_video">Set App Background Video</option>
    </select></label>
    <label>Robot Response optional<input name="reply_text" id="cmdReply" value="${escapeHtml(command.reply_text || '')}" placeholder="Optional text before action"></label>
    <label id="mediaFileLabel">File name <span class="muted">${pathHint}</span><input id="actionValueInput" value="${escapeHtml(value)}" placeholder="${placeholder}"></label>
    <label id="mediaDurationLabel">Photo dialog display duration seconds<input type="number" id="mediaDurationSec" min="1" max="600" value="${escapeHtml(command.media_duration_sec ?? 5)}"></label>
    <div class="form-grid two compact-grid">
      <label>Optional Movement<select id="extraActionType"><option value="">None</option><option value="ros_script">Movement</option></select></label>
    </div>
    ${movementPickerHtml(command.extra_action_value || '', 'extraActionValue')}
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;
  }

  if (type === 'face_greeting') return `
    <label>Face Type<select id="faceRecognitionType" onchange="toggleFaceNameField()"><option value="known">Known Person</option><option value="unknown">Unknown Person</option></select></label>
    <div id="knownFaceNameField"><label>Person Name<input name="person_name" id="facePersonName" value="${escapeHtml(command.person_name || '')}" placeholder="Name from Promobot face database"></label></div>
    <div class="form-grid two compact-grid">
      <label>Enable or Disable<select id="faceEnabled"><option value="true">Enabled</option><option value="false">Disabled</option></select></label>
      <label>Languages<select id="faceLanguage"><option value="all">All</option><option value="ar">Arabic</option><option value="en">English</option><option value="ku">Kurdish</option></select></label>
      <label>Match Type<select id="faceMatch"><option value="exact">Exact</option><option value="contains">Contains</option><option value="regex">Regex</option></select></label>
      <label>Priority<input type="number" id="facePriority" value="${escapeHtml(command.priority ?? 50)}"></label>
      <label>Cooldown seconds<input type="number" id="faceCooldown" value="${escapeHtml(command.cooldown_sec ?? 60)}"></label>
    </div>
    <label>Robot Response<textarea id="faceResponseText" class="short-textarea" placeholder="أهلاً وسهلاً، نورتنا.">${escapeHtml(command.response_text || '')}</textarea></label>
    ${movementPickerHtml(command.action_value || '', 'faceActionValue')}
    <label>Notes optional<textarea id="faceNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  if (type === 'shell_command') return `
    ${commonFieldsHtml(type, command)}
    <label>Shell Command<textarea id="actionValueInput" class="short-textarea" placeholder="/bin/bash command">${escapeHtml(value)}</textarea></label>
    <label>Robot Response optional<input name="reply_text" id="cmdReply" value="${escapeHtml(command.reply_text || '')}"></label>
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  if (type === 'multi_action') return `
    ${commonFieldsHtml(type, command)}
    <div><label>Action Steps</label><div id="multiSteps"></div><button type="button" class="btn" onclick="addMultiStep()">Add Step</button><input type="hidden" id="actionValueInput" value="${escapeHtml(value)}"></div>
    <label>Robot Response optional<input name="reply_text" id="cmdReply" value="${escapeHtml(command.reply_text || '')}"></label>
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;

  return `
    ${commonFieldsHtml(type, command)}
    <label>Action Value<input id="actionValueInput" value="${escapeHtml(value)}" placeholder="optional"></label>
    <label>Robot Response optional<input name="reply_text" id="cmdReply" value="${escapeHtml(command.reply_text || '')}"></label>
    <label>Notes optional<textarea name="notes" id="cmdNotes" class="short-textarea">${escapeHtml(command.notes || '')}</textarea></label>`;
}

function updateMediaKindUi() {
  const kind = document.getElementById('mediaKind')?.value || 'show_photo';
  const fileLabel = document.getElementById('mediaFileLabel');
  const durationLabel = document.getElementById('mediaDurationLabel');
  const input = document.getElementById('actionValueInput');
  const isVideo = kind === 'show_video' || kind === 'set_background_video';
  const isBackground = kind === 'set_background_image' || kind === 'set_background_video';
  if (fileLabel) {
    const hint = isVideo ? '/home/promobot/config/video' : '/home/promobot/config/photo';
    fileLabel.childNodes[0].textContent = isBackground ? 'Background file name ' : 'Dialog file name ';
    const muted = fileLabel.querySelector('.muted');
    if (muted) muted.textContent = hint;
  }
  if (input) input.placeholder = isVideo ? 'intro.mp4 or background.mp4' : 'welcome.jpg, offer.png, or background.jpg';
  if (durationLabel) durationLabel.style.display = kind === 'show_photo' ? '' : 'none';
}

function toggleFaceNameField() {
  const type = document.getElementById('faceRecognitionType')?.value || 'known';
  const field = document.getElementById('knownFaceNameField');
  const input = document.getElementById('facePersonName');
  if (!field) return;
  if (type === 'unknown') {
    field.style.display = 'none';
    if (input) input.value = '';
  } else {
    field.style.display = '';
  }
}

function applyCommonValues(command, type) {
  if (type === 'face_greeting') {
    document.getElementById('faceRecognitionType').value = command?.recognition_type || (command?.person_name ? 'known' : 'known');
    document.getElementById('faceEnabled').value = String(command?.enabled ?? true);
    document.getElementById('faceLanguage').value = command?.language || 'all';
    document.getElementById('faceMatch').value = command?.match_type || 'exact';
    toggleFaceNameField();
    return;
  }
  document.getElementById('cmdEnabled').value = String(command?.enabled ?? true);
  document.getElementById('cmdTrigger').value = command?.trigger_mode || ((type === 'promobot_tts' || type === 'set_language' || type === 'mute') ? 'remote_only' : 'voice_remote');
  document.getElementById('cmdLanguage').value = command?.language || (type === 'mute' ? 'all' : 'all');
  document.getElementById('cmdMatch').value = command?.match_type || 'contains';
  if (type === 'set_language' && command?.required_language) document.getElementById('actionValueInput').value = command.required_language;
  else if (type === 'set_language' && command?.action_value) document.getElementById('actionValueInput').value = command.action_value;
  if (type === 'mute') document.getElementById('actionValueInput').value = command?.mute_state || command?.action_value || 'active';
  if (type === 'media' && document.getElementById('mediaKind')) {
    document.getElementById('mediaKind').value = ['show_photo','show_video','set_background_image','set_background_video'].includes(command?.action_type) ? command.action_type : 'show_photo';
    updateMediaKindUi();
  }
  if (type === 'promobot_tts' || type === 'media') {
    if (document.getElementById('extraActionType')) document.getElementById('extraActionType').value = command?.extra_action_type || '';
    if (document.getElementById('extraActionValue')) document.getElementById('extraActionValue').value = command?.extra_action_value || '';
  }
  if (type === 'media' && document.getElementById('mediaDurationSec')) {
    document.getElementById('mediaDurationSec').value = command?.media_duration_sec || 5;
  }
}

function openCommandDrawer(command = null) {
  editingCommand = command;
  let type = (command && (activeCommandType === 'face_greeting' || command.person_name !== undefined)) ? 'face_greeting' : (command?.action_type || activeCommandType);
  if (['show_photo','show_video','set_background_image','set_background_video'].includes(type)) type = 'media';
  document.getElementById('cmdActionType').value = type;
  document.getElementById('drawerTitle').textContent = command ? 'Edit Command' : 'Add Command';
  document.getElementById('drawerSubtitle').textContent = typeTitles[type] || type;
  document.getElementById('cmdId').value = command?.id || '';
  document.getElementById('drawerDynamicFields').innerHTML = actionFields(type, command || {});
  applyCommonValues(command || {}, type);
  if (type === 'multi_action') renderMultiSteps(command?.action_value || '');
  document.getElementById('drawerBackdrop').classList.add('open');
  document.getElementById('commandDrawer').classList.add('open');
}

function closeCommandDrawer() {
  document.getElementById('drawerBackdrop')?.classList.remove('open');
  document.getElementById('commandDrawer')?.classList.remove('open');
  editingCommand = null;
}

function editCommand(id) {
  const cmd = activeCommandType === 'face_greeting' ? allFaceGreetings.find(c => c.id === id) : allCommands.find(c => c.id === id);
  if (cmd) openCommandDrawer(cmd);
}

function getActionValue() {
  const type = document.getElementById('cmdActionType').value;
  if (type === 'multi_action') return serializeMultiSteps();
  return document.getElementById('actionValueInput')?.value || '';
}

function getPhrases() {
  const el = document.getElementById('cmdPhrases');
  if (!el) return [];
  return el.value.split('\n').map(x => x.trim()).filter(Boolean);
}

async function saveCommand() {
  const id = document.getElementById('cmdId').value;
  const type = document.getElementById('cmdActionType').value;
  if (type === 'face_greeting') {
    const recognitionType = document.getElementById('faceRecognitionType').value;
    const payload = {
      recognition_type: recognitionType,
      person_name: recognitionType === 'known' ? document.getElementById('facePersonName').value : '',
      enabled: document.getElementById('faceEnabled').value === 'true',
      language: document.getElementById('faceLanguage').value,
      match_type: document.getElementById('faceMatch').value,
      priority: parseInt(document.getElementById('facePriority').value || '0'),
      cooldown_sec: parseInt(document.getElementById('faceCooldown').value || '60'),
      response_text: document.getElementById('faceResponseText').value,
      action_type: 'ros_script',
      action_value: document.getElementById('faceActionValue').value,
      notes: document.getElementById('faceNotes').value
    };
    const url = id ? `/api/face-greetings/${id}` : '/api/face-greetings';
    const method = id ? 'PUT' : 'POST';
    const r = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const j = await r.json();
    if (j.ok) { toast(j.message || 'Saved'); closeCommandDrawer(); await loadCommands(); } else toast(j.message || 'Save failed');
    return;
  }
  const payload = {
    name: document.getElementById('cmdName').value,
    enabled: document.getElementById('cmdEnabled').value === 'true',
    trigger_mode: document.getElementById('cmdTrigger').value,
    language: document.getElementById('cmdLanguage').value,
    match_type: document.getElementById('cmdMatch').value,
    priority: parseInt(document.getElementById('cmdPriority').value || '0'),
    phrases: getPhrases(),
    action_type: type === 'media' ? (document.getElementById('mediaKind')?.value || 'show_photo') : type,
    action_value: getActionValue(),
    reply_text: document.getElementById('cmdReply')?.value || '',
    notes: document.getElementById('cmdNotes')?.value || '',
    extra_action_type: '',
    extra_action_value: '',
    required_language: '',
    mute_state: '',
    media_duration_sec: parseInt(document.getElementById('mediaDurationSec')?.value || '5')
  };

  if (type === 'promobot_tts') {
    payload.trigger_mode = 'remote_only';
    payload.extra_action_type = document.getElementById('extraActionType')?.value || '';
    payload.extra_action_value = document.getElementById('extraActionValue')?.value || '';
  }
  if (type === 'media') {
    payload.extra_action_type = document.getElementById('extraActionType')?.value || '';
    payload.extra_action_value = document.getElementById('extraActionValue')?.value || '';
    payload.media_duration_sec = parseInt(document.getElementById('mediaDurationSec')?.value || '5');
  }
  if (type === 'set_language') {
    payload.trigger_mode = 'remote_only';
    payload.required_language = payload.action_value;
  }
  if (type === 'mute') {
    payload.trigger_mode = 'remote_only';
    payload.language = 'all';
    payload.mute_state = payload.action_value;
  }

  const url = id ? `/api/commands/${id}` : '/api/commands';
  const method = id ? 'PUT' : 'POST';
  const r = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const j = await r.json();
  if (j.ok) { toast(j.message || 'Saved'); closeCommandDrawer(); await loadCommands(); }
  else toast(j.message || 'Save failed');
}

async function deleteCommand(id) {
  if (!confirm('Delete command '+id+'?')) return;
  if (activeCommandType === 'face_greeting') await fetch(`/api/face-greetings/${id}`, {method:'DELETE'});
  else await fetch(`/api/commands/${id}`, {method:'DELETE'});
  await loadCommands();
}

async function runCommand(id) {
  const robot_id = document.getElementById('robotId')?.value || 'promobot_v4_0445';
  const r = await fetch('/api/remote/execute', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({robot_id, command_id:id})});
  const j = await r.json();
  toast(j.message || 'Queued');
}

function renderMultiSteps(value) {
  const box = document.getElementById('multiSteps');
  if (!box) return;
  box.innerHTML = '';
  const lines = String(value || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  if (!lines.length) addMultiStep();
  for (const line of lines) {
    const [type, ...rest] = line.includes('|') ? line.split('|') : line.split(':');
    addMultiStep((type || '').trim(), rest.join('|').trim(), type === 'pause' ? rest.join('|').trim() : '');
  }
}

function addMultiStep(type = 'ros_script', value = '', delay = '') {
  const box = document.getElementById('multiSteps');
  if (!box) return;
  const row = document.createElement('div');
  row.className = 'multi-step';
  row.innerHTML = `
    <select class="step-type">
      <option value="ros_script">Movement</option>
      <option value="start_app">App</option>
      <option value="promobot_tts">Promobot TTS</option>
      <option value="show_photo">Photo Dialog</option>
      <option value="show_video">Video Dialog</option>
      <option value="set_background_image">Background Image</option>
      <option value="set_background_video">Background Video</option>
      <option value="set_language">Language</option>
      <option value="shell_command">Shell</option>
      <option value="pause">Delay</option>
    </select>
    <input class="step-value" placeholder="value" value="${escapeHtml(value)}">
    <input class="step-delay" type="number" placeholder="Delay ms" value="${escapeHtml(delay)}">
    <button type="button" class="btn small danger" onclick="this.parentElement.remove()">×</button>`;
  box.appendChild(row);
  row.querySelector('.step-type').value = type || 'ros_script';
}

function serializeMultiSteps() {
  const rows = document.querySelectorAll('#multiSteps .multi-step');
  const lines = [];
  rows.forEach(row => {
    const type = row.querySelector('.step-type').value;
    const value = row.querySelector('.step-value').value.trim();
    const delay = parseInt(row.querySelector('.step-delay').value || '0');
    if (type === 'pause') {
      const d = parseInt(value || delay || '0');
      if (d > 0) lines.push(`pause|${d}`);
      return;
    }
    if (type && value) lines.push(`${type}|${value}`);
    if (delay > 0) lines.push(`pause|${delay}`);
  });
  return lines.join('\n');
}

async function loadUsage() {
  const r = await fetch('/api/overview'); const j = await r.json();
  const body = document.getElementById('usageBody'); if (!body) return;
  body.innerHTML = Object.entries(j).filter(([k]) => k !== 'ok').map(([k,v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v)}</td></tr>`).join('');
}

async function loadLogs() {
  const r = await fetch('/api/logs'); const j = await r.json();
  const body = document.getElementById('logsBody'); if (!body) return;
  body.innerHTML = (j.logs || []).map(l => `<tr><td>${escapeHtml(l.ts)}</td><td>${escapeHtml(l.kind)}</td><td>${escapeHtml(l.message)}</td></tr>`).join('') || `<tr><td colspan="3" class="empty">No logs yet.</td></tr>`;
}

function initCommandPage() {
  document.querySelectorAll('.cmd-tab').forEach(btn => btn.addEventListener('click', () => setActiveCommandType(btn.dataset.type)));
  document.getElementById('commandSearch')?.addEventListener('input', renderCommands);
  const hashType = location.hash.replace('#','');
  if (hashType && typeTitles[hashType]) activeCommandType = hashType;
  setActiveCommandType(activeCommandType);
  loadCommands();
}

document.addEventListener('DOMContentLoaded', () => {
  setupTabs();
  setupSecretToggles();
  if (document.getElementById('commandsTable')) initCommandPage();
  if (document.getElementById('usageBody')) loadUsage();
  if (document.getElementById('logsBody')) loadLogs();
});
