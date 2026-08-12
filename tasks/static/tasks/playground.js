const $ = id => document.getElementById(id);
const output = $('output');
let token = '';
let timer;
const headers = () => ({'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token});
const report = value => { output.innerHTML = '<pre>' + String(value).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) + '</pre>'; };
const clearKey = () => { $('key').value = ''; $('key').type = 'password'; $('toggle').textContent = 'Show'; };
$('toggle').onclick = () => { const key = $('key'); key.type = key.type === 'password' ? 'text' : 'password'; $('toggle').textContent = key.type === 'password' ? 'Show' : 'Hide'; };
$('load').onclick = async () => {
  token = $('token').value;
  try {
    const response = await fetch('/api/v1/ai/providers/', {headers: headers()});
    if (!response.ok) throw new Error('Unable to load providers.');
    const data = await response.json();
    $('provider').innerHTML = data.providers.map(p => `<option value="${p.id}">${p.label}</option>`).join('');
    $('app').hidden = false;
    output.textContent = 'Enter a key and load models.';
  } catch (_) { report('Unable to load providers.'); }
};
$('models').onclick = async () => {
  const apiKey = $('key').value;
  if (!apiKey) return report('An API key is required to load models.');
  try {
    const response = await fetch(`/api/v1/ai/providers/${$('provider').value}/models/`, {method: 'POST', headers: headers(), body: JSON.stringify({api_key: apiKey})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Model discovery failed.');
    $('model').innerHTML = data.models.map(model => `<option>${model}</option>`).join('');
  } catch (_) { report('Model discovery failed.'); } finally { clearKey(); }
};
$('run').onclick = async () => {
  clearInterval(timer);
  const apiKey = $('key').value;
  const prompt = $('prompt').value;
  if (!apiKey || !prompt) return report('API key and prompt are required.');
  try {
    const body = {provider: $('provider').value, api_key: apiKey, prompt};
    if ($('model').value) body.model = $('model').value;
    if ($('temperature').value) body.temperature = Number($('temperature').value);
    if ($('max_tokens').value) body.max_tokens = Number($('max_tokens').value);
    const response = await fetch('/api/v1/ai/generate/', {method: 'POST', headers: headers(), body: JSON.stringify(body)});
    const task = await response.json();
    if (!response.ok) throw new Error(task.detail || 'Task submission failed.');
    poll(task.id);
  } catch (_) { report('Task submission failed.'); } finally { clearKey(); }
};
async function poll(id) {
  try {
    const response = await fetch('/api/v1/tasks/' + id + '/', {headers: headers()});
    if (!response.ok) throw new Error();
    const task = await response.json();
    report(JSON.stringify({status: task.status, provider: task.result?.provider, model: task.result?.model, duration: task.updated_at && task.created_at ? Math.round((new Date(task.updated_at) - new Date(task.created_at)) / 1000) + 's' : null, result: task.result?.text, error: task.error}, null, 2));
    if (['pending', 'running', 'retrying'].includes(task.status)) timer = setTimeout(() => poll(id), 1200);
  } catch (_) { report('Unable to retrieve task status.'); }
}
$('clear').onclick = () => { clearKey(); $('prompt').value = ''; $('temperature').value = ''; $('max_tokens').value = ''; output.textContent = 'Cleared.'; };
