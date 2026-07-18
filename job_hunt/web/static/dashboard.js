'use strict';
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let currentJob = null;
function setText(id, value) { document.getElementById(id).textContent = value ?? '-'; }
function listCounts(id, data) {
  const root = document.getElementById(id); root.replaceChildren();
  Object.entries(data).slice(0, 20).forEach(([key, value]) => {
    const item = document.createElement('li'); item.textContent = `${key}: ${value}`; root.append(item);
  });
}
async function api(url, options = {}) {
  options.headers = { ...(options.headers || {}), Accept: 'application/json' };
  if (options.method && options.method !== 'GET') options.headers['X-CSRF-Token'] = csrf;
  const response = await fetch(url, options);
  if (response.status === 401) { location.href = '/login'; throw new Error('Sessão encerrada'); }
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || 'Falha na operação');
  return body;
}
async function loadDashboard() {
  const data = await api('/api/dashboard');
  setText('new-jobs', data.new_jobs); setText('high-jobs', data.high_score_jobs); setText('total-jobs', data.total_jobs);
  listCounts('companies', data.by_company); listCounts('sources', data.by_source); listCounts('pipeline', data.applications_by_status);
}
async function loadJobs() {
  const params = new URLSearchParams();
  const values = { search: 'search', modality: 'modality', user_status: 'disposition', minimum_score: 'min-score' };
  Object.entries(values).forEach(([key, id]) => { const value = document.getElementById(id).value; if (value) params.set(key, value); });
  const data = await api(`/api/jobs?${params}`); const body = document.getElementById('jobs'); body.replaceChildren();
  data.items.forEach(job => {
    const row = document.createElement('tr'); row.dataset.id = job.id;
    [job.score ?? '-', job.title, job.company, job.location ?? '-', job.user_status].forEach(value => { const cell = document.createElement('td'); cell.textContent = value; row.append(cell); });
    row.addEventListener('click', () => loadDetail(job.id)); body.append(row);
  });
  setText('pagination', `${data.total} vagas · página ${data.page} de ${data.pages || 1}`);
}
async function loadDetail(id) {
  const data = await api(`/api/jobs/${id}`); currentJob = id;
  document.getElementById('empty-detail').hidden = true; document.getElementById('detail').hidden = false;
  setText('detail-title', data.job.title); setText('detail-company', `${data.company.name} · ${data.job.location ?? 'Local não informado'}`);
  setText('detail-score', data.latest_analysis ? `Score ${data.latest_analysis.total_score}/100` : 'Sem análise');
  const link = document.getElementById('official-link'); const url = data.sources[0]?.apply_url || data.sources[0]?.url || '';
  if (url.startsWith('https://') || url.startsWith('http://')) { link.href = url; link.hidden = false; } else link.hidden = true;
  document.getElementById('application-status').value = data.application?.status || 'discovered';
  setText('analysis', JSON.stringify(data.latest_analysis, null, 2)); setText('description', data.latest_snapshot?.description || 'Descrição não armazenada');
}
async function mutate(path, payload) {
  setText('action-status', 'Processando...');
  try {
    const result = await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    setText('action-status', 'Concluído'); await loadJobs(); return result;
  } catch (error) { setText('action-status', error.message); return null; }
}
document.getElementById('filter').addEventListener('click', loadJobs);
document.querySelectorAll('[data-disposition]').forEach(button => button.addEventListener('click', () => currentJob && mutate(`/api/jobs/${currentJob}/disposition`, { status: button.dataset.disposition })));
document.getElementById('save-application').addEventListener('click', () => currentJob && mutate(`/api/jobs/${currentJob}/application`, { status: document.getElementById('application-status').value, notes: null, allow_reopen: false }));
document.getElementById('generate-docs').addEventListener('click', () => currentJob && mutate(`/api/jobs/${currentJob}/documents`, { language: 'en', create_docx: true, create_pdf: false }));
Promise.all([loadDashboard(), loadJobs()]).catch(error => setText('pagination', error.message));
