(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const providerNames = { openai: 'OpenAI', gemini: 'Gemini', isaac: 'Pokee' };
  const state = { token: '', provider: 'openai', providers: {}, configured: {}, busy: false, timer: null };
  const runningStates = new Set(['pending', 'running', 'retrying', 'queued']);

  const controls = ['model', 'prompt', 'temperature', 'max-tokens', 'generate'];
  const headers = () => ({ 'Content-Type': 'application/json', Authorization: `Bearer ${state.token}` });
  const selectedProvider = () => document.querySelector('input[name="provider"]:checked').value;
  const safeText = (value, fallback = '') => typeof value === 'string' && value.trim() ? value : fallback;

  function clearApiKey() {
    const key = $('api-key');
    key.value = '';
    key.type = 'password';
    $('toggle-key').textContent = 'Show';
    $('toggle-key').setAttribute('aria-label', 'Show API key');
  }

  function setMessage(message, kind = 'idle') {
    $('response-message').textContent = message;
    $('response-output').hidden = true;
    $('response-output').textContent = '';
    $('response-meta').hidden = true;
    $('response-meta').replaceChildren();
    setTaskStatus(kind === 'error' ? 'Needs attention' : 'Idle', kind);
  }

  function setTaskStatus(text, kind = 'idle') {
    const chip = $('task-status');
    chip.textContent = text;
    chip.className = 'status-chip';
    if (kind === 'running') chip.classList.add('is-running');
    if (kind === 'error') chip.classList.add('is-error');
    if (kind === 'success') chip.classList.add('is-connected');
  }

  function providerData(provider) {
    return state.providers[provider] || {};
  }

  function setComposerEnabled(enabled) {
    controls.forEach((id) => { $(id).disabled = !enabled; });
    $('load-models').disabled = true;
  }

  function drawProvider() {
    state.provider = selectedProvider();
    const provider = state.provider;
    const connected = Boolean(state.configured[provider]);
    const displayName = providerNames[provider];
    const metadata = providerData(provider);
    const label = metadata.label || displayName;

    $('key-label').textContent = `${displayName} API key`;
    $('api-key').placeholder = connected ? 'Paste a new key to rotate it' : 'Paste a key to connect';
    $('save-key').textContent = connected ? 'Rotate key' : 'Save key';
    $('remove-key').hidden = !connected;
    $('provider-status').textContent = connected ? `${displayName} connected` : `Connect ${displayName} to compose`;
    $('provider-status').className = `status-chip status-chip--ready${connected ? ' is-connected' : ''}`;

    const model = $('model');
    model.replaceChildren();
    const option = document.createElement('option');
    option.value = safeText(metadata.default_model);
    option.textContent = option.value || `${label} default`;
    model.append(option);
    setComposerEnabled(connected);
    if (!connected) setMessage(`${displayName} is not connected. Save a key to enable the composer.`);
  }

  function drawCredentials() {
    Object.keys(providerNames).forEach((provider) => {
      const connected = Boolean(state.configured[provider]);
      const element = $(`state-${provider}`);
      element.textContent = connected ? '•••••••• Connected' : 'Not connected';
      element.classList.toggle('is-connected', connected);
    });
    drawProvider();
  }

  function showOutput(task) {
    const result = task && typeof task.result === 'object' && task.result ? task.result : {};
    const text = safeText(result.text, safeText(result.output, 'No text output was returned.'));
    const meta = $('response-meta');
    meta.replaceChildren();
    [['Provider', result.provider], ['Model', result.model], ['Status', task.status]].forEach(([name, value]) => {
      if (!value) return;
      const item = document.createElement('span');
      item.textContent = `${name}: ${value}`;
      meta.append(item);
    });
    meta.hidden = !meta.childElementCount;
    $('response-message').textContent = 'Completed.';
    $('response-output').textContent = text;
    $('response-output').hidden = false;
  }

  function knownError(response, body, fallback) {
    if (response.status === 401) return 'Your session has expired. Connect again to continue.';
    if (response.status === 400 && body && safeText(body.detail) === 'Unable to validate this API key.') return 'We could not validate that API key. Check it and try again.';
    if (response.status === 409) return 'No saved key is available for this provider. Save a key to continue.';
    if (response.status === 429) return 'Too many requests. Please wait a moment and try again.';
    const detail = body && safeText(body.detail);
    const permitted = ['AI credential expired or is unavailable; submit a new task.'];
    return permitted.includes(detail) ? detail : fallback;
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { ...options, headers: headers() });
    let body = null;
    try { body = await response.json(); } catch (_) { /* Responses are intentionally not surfaced raw. */ }
    if (!response.ok) throw new Error(knownError(response, body, 'The request could not be completed.'));
    return body || {};
  }

  async function connect() {
    const submittedToken = $('token').value.trim();
    if (!submittedToken) { setMessage('Enter an access token to connect.', 'error'); return; }
    state.token = submittedToken;
    $('connect').disabled = true;
    try {
      const metadata = await request('/api/v1/ai/providers/');
      const values = Array.isArray(metadata.providers) ? metadata.providers : [];
      state.providers = values.reduce((all, value) => {
        if (value && providerNames[value.id]) all[value.id] = value;
        return all;
      }, {});
      await Promise.all(Object.keys(providerNames).map(async (provider) => {
        const credential = await request(`/api/v1/ai/credentials/${provider}/`);
        state.configured[provider] = Boolean(credential.configured);
      }));
      $('token').value = '';
      $('token').disabled = true;
      $('connect').hidden = true;
      $('signout').hidden = false;
      $('auth-status').textContent = 'Connected';
      $('auth-status').classList.add('is-connected');
      $('workspace').hidden = false;
      drawCredentials();
    } catch (error) {
      state.token = '';
      setMessage(error.message, 'error');
    } finally {
      $('connect').disabled = false;
    }
  }

  async function saveKey() {
    const apiKey = $('api-key').value;
    if (!apiKey) { setMessage('Paste an API key before saving it.', 'error'); return; }
    const provider = state.provider;
    $('save-key').disabled = true;
    try {
      await request(`/api/v1/ai/credentials/${provider}/`, { method: 'PUT', body: JSON.stringify({ api_key: apiKey }) });
      state.configured[provider] = true;
      drawCredentials();
      setMessage(`${providerNames[provider]} key saved securely. You can now compose a request.`);
    } catch (error) {
      setMessage(error.message, 'error');
    } finally {
      clearApiKey();
      $('save-key').disabled = false;
    }
  }

  async function removeKey() {
    const provider = state.provider;
    $('remove-key').disabled = true;
    try {
      await request(`/api/v1/ai/credentials/${provider}/`, { method: 'DELETE' });
      state.configured[provider] = false;
      drawCredentials();
      setMessage(`${providerNames[provider]} key removed.`);
    } catch (error) {
      setMessage(error.message, 'error');
    } finally {
      $('remove-key').disabled = false;
    }
  }

  function setBusy(busy) {
    state.busy = busy;
    $('generate').disabled = busy || !state.configured[state.provider];
    $('generate').classList.toggle('is-busy', busy);
    $('generate-label').textContent = busy ? 'Generating' : 'Generate';
  }

  async function generate() {
    if (state.busy) return;
    const prompt = $('prompt').value.trim();
    if (!prompt) { setMessage('Write a prompt before generating.', 'error'); return; }
    clearTimeout(state.timer);
    setBusy(true);
    setTaskStatus('Queued', 'running');
    $('response-message').textContent = 'Your request is entering the secure queue…';
    try {
      const body = { provider: state.provider, prompt };
      if ($('model').value) body.model = $('model').value;
      if ($('temperature').value) body.temperature = Number($('temperature').value);
      if ($('max-tokens').value) body.max_tokens = Number($('max-tokens').value);
      const task = await request('/api/v1/ai/generate/saved/', { method: 'POST', body: JSON.stringify(body) });
      if (!task.id) throw new Error('The task could not be started.');
      poll(task.id);
    } catch (error) {
      setBusy(false);
      setMessage(error.message, 'error');
    }
  }

  async function poll(id) {
    try {
      const task = await request(`/api/v1/tasks/${encodeURIComponent(id)}/`);
      const status = safeText(task.status, 'pending').toLowerCase();
      if (runningStates.has(status)) {
        setTaskStatus(status.charAt(0).toUpperCase() + status.slice(1), 'running');
        $('response-message').textContent = 'Your response is being generated…';
        state.timer = window.setTimeout(() => poll(id), 1200);
        return;
      }
      setBusy(false);
      if (status === 'completed' || status === 'success') {
        setTaskStatus('Complete', 'success');
        showOutput(task);
      } else {
        const safeFailure = safeText(task.error, 'The task could not be completed.');
        setMessage(safeFailure, 'error');
      }
    } catch (error) {
      setBusy(false);
      setMessage(error.message || 'Unable to retrieve task status.', 'error');
    }
  }

  function signOut() {
    clearTimeout(state.timer);
    state.timer = null;
    state.token = '';
    state.providers = {};
    state.configured = {};
    state.busy = false;
    $('token').value = '';
    $('token').disabled = false;
    $('connect').hidden = false;
    $('signout').hidden = true;
    $('auth-status').textContent = 'Not connected';
    $('auth-status').classList.remove('is-connected');
    $('workspace').hidden = true;
    $('prompt').value = '';
    $('temperature').value = '';
    $('max-tokens').value = '';
    clearApiKey();
    setMessage('Connect a provider, then write a prompt to begin.');
  }

  $('connect').addEventListener('click', connect);
  $('signout').addEventListener('click', signOut);
  $('save-key').addEventListener('click', saveKey);
  $('remove-key').addEventListener('click', removeKey);
  $('generate').addEventListener('click', generate);
  $('toggle-key').addEventListener('click', () => {
    const key = $('api-key');
    const visible = key.type === 'text';
    key.type = visible ? 'password' : 'text';
    $('toggle-key').textContent = visible ? 'Show' : 'Hide';
    $('toggle-key').setAttribute('aria-label', visible ? 'Show API key' : 'Hide API key');
  });
  document.querySelectorAll('input[name="provider"]').forEach((radio) => radio.addEventListener('change', () => {
    clearApiKey();
    drawProvider();
  }));
  $('prompt').addEventListener('input', () => { $('prompt-count').textContent = `${$('prompt').value.length.toLocaleString()} / 20,000`; });
})();
