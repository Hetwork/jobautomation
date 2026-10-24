const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {applied:'Applied', interview:'Interview', rejected:'Rejected', offer:'Offer', scam:'Potential scam'};
let state = {applications:[], counts:{}, email_count:0, followup_count:0};
let view = 'all', filter = 'all', query = '', selected = null, toastTimer, busy = false;
let gmailState = {}, gmailBusy = false;
const date = value => new Date(value).toLocaleDateString(undefined, {month:'short',day:'numeric'});
const badge = label => `<span class="badge ${label}">${labels[label]}</span>`;

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}
function toast(message, error = false) {
  clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').className = error ? 'error' : ''; $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 9000 : 5000);
}
async function refresh() {
  try {
    state = await api('/api/state'); render();
    $('#sync-status').textContent = `Saved locally · ${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`;
    if (!gmailBusy) refreshGmail().catch(() => {});
  } catch (error) { $('#sync-status').textContent = 'Connection unavailable'; throw error; }
}
function setView(next) { view = next; filter = 'all'; render(); }
function setFilter(next) { view = 'all'; filter = next; render(); }

function render() {
  const apps = state.applications;
  for (const [id, value] of Object.entries({'nav-all':apps.length,'nav-followups':state.followup_count,'nav-scams':state.counts.scam || 0,'nav-inbox':state.email_count,'stat-total':apps.length,'stat-interviews':state.counts.interview || 0,'stat-offers':state.counts.offer || 0,'stat-followups':state.followup_count})) $(`#${id}`).textContent = value;
  $('#stat-emails').textContent = `From ${state.email_count} imported emails`;
  $('#risk-banner').hidden = !state.counts.scam;
  $('#risk-title').textContent = `${state.counts.scam} application${state.counts.scam === 1 ? '' : 's'} flagged for potential scams`;
  const headings = {all:['Applications','Your next chapter, organized.','Every application in sight. Every next step within reach.','Application tracker','A little clarity for your next big move.'],followups:['Follow-ups','A small nudge. A new possibility.','Active applications with seven days of silence.','Your next steps','Open an application to prepare and log a follow-up.'],scams:['Flagged offers','Pause before you proceed.','Suspicious requests, with the evidence to understand why.','Flagged applications','Potential scams need review; a flag is not proof of fraud.'],inbox:['Imported inbox','The whole conversation.','Every imported message, classified once and kept in context.','Imported inbox','Open a message to see its application history.']};
  const h = headings[view];
  $('#breadcrumb').textContent = h[0]; $('#page-title').textContent = h[1]; $('#page-subtitle').textContent = h[2];
  $('#tracker-title').firstChild.textContent = h[3] + ' '; $('#tracker-subtitle').textContent = h[4];
  document.querySelectorAll('.nav-item').forEach(el => { el.classList.toggle('active', el.dataset.view === view); el.setAttribute('aria-current', el.dataset.view === view ? 'page' : 'false'); });
  document.querySelectorAll('.tab').forEach(el => {el.classList.toggle('selected', el.dataset.filter === filter);el.setAttribute('aria-pressed', String(el.dataset.filter === filter));});
  $('.tabs').hidden = view !== 'all';
  const matches = text => text.toLowerCase().includes(query.toLowerCase());
  let rows = apps.filter(a => (view !== 'followups' || a.followup_due) && (view !== 'scams' || a.status === 'scam') && (filter === 'all' || a.status === filter) && (view === 'inbox' || matches(`${a.company} ${a.role}`)));
  if (view === 'inbox') rows = apps.flatMap(a => a.history.map(e => ({...e, app:a}))).filter(e => matches(`${e.app.company} ${e.app.role} ${e.subject} ${e.sender}`)).sort((a,b) => b.date.localeCompare(a.date));
  $('#tracker-count').textContent = rows.length;
  $('#table-caption').textContent = `Showing ${rows.length} ${view === 'inbox' ? 'emails' : 'applications'}`;
  if (!rows.length) {
    const empty = !apps.length;
    $('#table-area').innerHTML = `<div class="empty"><span>${empty ? '▤' : '✓'}</span><h3>${empty ? 'Your next chapter starts here.' : 'You’re all caught up here.'}</h3><p>${empty ? 'Load the 20-email demo or import your own inbox. We’ll organize the applications and show what needs attention.' : 'No results match this view. Try another stage or clear your search.'}</p>${empty ? '<button class="primary" id="empty-demo">Try the demo ↗</button>' : '<button class="secondary" id="reset-filters">Show all applications</button>'}</div>`;
    $('#empty-demo')?.addEventListener('click', loadDemo);
    $('#reset-filters')?.addEventListener('click', () => {query='';$('#search').value='';setView('all');});
    return;
  }
  const inbox = view === 'inbox';
  $('#table-area').innerHTML = `<table><thead><tr>${(inbox ? ['COMPANY','EMAIL','LABEL','RECEIVED',''] : ['COMPANY','ROLE','STAGE','LAST UPDATE','NEXT STEP','']).map(t => `<th scope="col">${t}</th>`).join('')}</tr></thead><tbody>${rows.map(row => {
    const a = inbox ? row.app : row;
    const key = escapeHTML(a.id);
    const company = `<div class="company-cell"><span class="company-logo" aria-hidden="true">${escapeHTML(a.company.slice(0,1).toUpperCase())}</span><button class="row-link" data-detail="${key}"><span class="company-name">${escapeHTML(a.company)}</span><span class="company-meta">${a.email_count} email${a.email_count === 1 ? '' : 's'}</span></button></div>`;
    return `<tr class="${a.status === 'scam' ? 'flag-row' : ''}"><td>${company}</td>${inbox ? `<td class="email-subject"><button class="row-link" data-detail="${key}" data-email="${row.id}">${escapeHTML(row.subject)}</button></td><td>${badge(row.label)}</td><td>${date(row.date)}</td>` : `<td>${escapeHTML(a.role)}</td><td>${badge(a.status)}</td><td>${date(a.last_activity)}</td><td>${a.followup_due ? `<span class="due">◷ Follow up · ${a.days_waiting}d</span>` : a.status === 'scam' ? '<span class="badge scam">Review flag</span>' : a.status === 'offer' ? '<span class="green">Review offer</span>' : a.status === 'rejected' ? '<span class="muted">Closed</span>' : '<span class="muted">Awaiting update</span>'}</td>`}<td><button class="row-action" data-detail="${key}" aria-label="View ${escapeHTML(a.company)} application">↗</button></td></tr>`;
  }).join('')}</tbody></table>`;
  document.querySelectorAll('[data-detail]').forEach(button => button.addEventListener('click', () => openDetails(button.dataset.detail, button.dataset.email)));
}

function openDetails(id, emailId) {
  const a = state.applications.find(app => app.id === id);
  if (!a) return;
  selected = id;
  const draft = `Hello ${a.company} team,\n\nI’m following up on my application for the ${a.role} position. I’m still interested in the opportunity and would appreciate any update on next steps. Please let me know if I can provide anything further.\n\nThank you for your time.`;
  const reasons = a.reasons.length ? `<section class="reason-list"><h3>Why this application was flagged</h3>${a.reasons.map(r => `<div class="reason"><strong>${escapeHTML(r.reason)}</strong><blockquote>${escapeHTML(r.evidence)}</blockquote></div>`).join('')}<p>Verify the request independently before sharing information or making a payment. Follow-up reminders are disabled for flagged applications.</p></section>` : '';
  $('#detail-content').innerHTML = `<div class="modal-head"><div><p class="eyebrow">APPLICATION DETAILS</p><h2 id="detail-title">${escapeHTML(a.company)}</h2><p>${escapeHTML(a.role)}</p></div><button class="close-button" data-close="details" aria-label="Close application details">×</button></div><div class="modal-body"><div class="detail-summary">${badge(a.status)}<span>${a.email_count} emails · Latest ${date(a.last_activity)}</span></div>${reasons}${a.followup_due ? `<section class="followup-panel"><h3>It’s a good time to check in.</h3><p>No new activity for ${a.days_waiting} days. Copy this draft, send it from your email app, then log it below.</p><pre class="draft">${escapeHTML(draft)}</pre><div class="followup-actions"><button class="secondary" id="copy-draft">Copy draft</button><button class="primary" id="log-followup">I sent a follow-up</button></div><p>Logging restarts the seven-day reminder. Nothing is sent by Autopilot.</p></section>` : a.followup_at ? `<p class="fine-print">Follow-up logged ${date(a.followup_at)}. ${a.followup_due_at ? `Next reminder eligible ${date(a.followup_due_at)} if no new activity.` : 'This application is no longer awaiting a follow-up.'}</p>` : ''}<h3 class="timeline-heading">Email timeline</h3>${[...a.history].reverse().map((email, index) => `<details class="email-detail" ${emailId ? email.id === emailId ? 'open' : '' : index === 0 ? 'open' : ''}><summary>${badge(email.label)}${escapeHTML(email.subject)}<small>${escapeHTML(email.sender)} · ${date(email.date)}</small></summary><div class="email-body"><pre>${escapeHTML(email.body)}</pre><div class="explanation">${escapeHTML(email.explanation)}<br>Classifier: ${escapeHTML(email.classifier_version)}${email.model_score !== null ? `<br>Posting-model signal: ${(email.model_score * 100).toFixed(1)}% (uncalibrated; not an email scam probability).` : ''}</div></div></details>`).join('')}</div>`;
  $('#details [data-close]').addEventListener('click', () => $('#details').close());
  $('#copy-draft')?.addEventListener('click', async () => {try {await navigator.clipboard.writeText(draft);toast('Follow-up draft copied.');} catch {toast('Clipboard unavailable. Select and copy the draft above.',true);}});
  $('#log-followup')?.addEventListener('click', async event => {
    event.target.disabled = true;
    try {await api('/api/followup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({application_id:id})});await refresh();openDetails(id);toast('Follow-up logged. Your reminder clock has restarted.');}
    catch(error) {toast(error.message,true);event.target.disabled=false;}
  });
  if (!$('#details').open) $('#details').showModal();
}

async function performImport(path, options) {
  if (busy) return;
  busy = true;
  $('#demo-button').disabled = true; $('#upload-button').disabled = true;
  $('#demo-button').textContent = 'Processing inbox…';
  try {
    const result = await api(path, options);
    view = 'all'; filter = 'all'; query = ''; $('#search').value = '';
    await refresh();
    toast(`${result.added} emails classified. ${result.skipped} duplicates skipped. Tracker updated.`);
  } catch (error) {toast(error.message,true);}
  finally {busy=false;$('#demo-button').disabled=false;$('#upload-button').disabled=false;$('#demo-button').textContent='Load demo inbox ↗';}
}
function loadDemo() {return performImport('/api/demo', {method:'POST'});}
$('#demo-button').addEventListener('click', loadDemo);
$('#upload-button').addEventListener('click', () => $('#file-input').click());
$('#file-input').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 10 * 1024 * 1024) {toast('Choose a file smaller than 10 MB.',true);event.target.value='';return;}
  await performImport(`/api/import?filename=${encodeURIComponent(file.name)}`, {method:'POST',body:file});
  event.target.value = '';
});
$('#search').addEventListener('input', event => {query=event.target.value;render();});
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => setFilter(button.dataset.filter)));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(`#${button.dataset.close}`).close()));
$('#how-button').addEventListener('click', () => {
  const m = state.model;
  $('#model-info').innerHTML = m ? `<strong>Grounded in your Kaggle dataset</strong><p>${m.raw_rows.toLocaleString()} postings · ${m.training_rows.toLocaleString()} training rows · ${m.test_rows.toLocaleString()} held-out rows.</p><p>Fraud precision: ${(m.precision*100).toFixed(1)}% · Recall: ${(m.recall*100).toFixed(1)}% · F1: ${m.f1.toFixed(3)}</p><p>${escapeHTML(m.limitation)} A model score alone never triggers a scam label.</p>` : '<strong>Explainable rules are active.</strong><p>The optional posting model has not been trained. Run python scripts/train_model.py and restart the server to enable its supporting signal.</p>';
  $('#how-dialog').showModal();
});
refresh().catch(error => toast(error.message,true));
setInterval(() => {if (!document.hidden && !busy) refresh().catch(() => {});}, 30000);
document.addEventListener('visibilitychange', () => {if (!document.hidden && !busy) refresh().catch(() => {});});

async function refreshGmail() {
  gmailState = await api('/api/gmail/status');
  $('#gmail-connect').hidden = gmailState.connected;
  $('#gmail-scan').hidden = !gmailState.connected;
  $('#gmail-disconnect').hidden = !gmailState.connected;
  $('#gmail-window-label').hidden = !gmailState.connected;
  $('#gmail-status').textContent = gmailState.connected
    ? `${gmailState.account} · Read-only access${gmailState.last_sync ? ' · Last scan ' + new Date(gmailState.last_sync).toLocaleString() : ' · Ready for your first scan'}`
    : gmailState.configured ? 'Gmail is ready to connect. Sign in with Google to scan your job-related emails.' : 'One-time Google setup required. File imports and the demo remain available.';
  if (!gmailState.connected && gmailState.last_error) {
    $('#gmail-status').textContent = gmailState.last_error;
  }
  updateScanLabel();
}
function updateScanLabel() {
  $('#gmail-scan').textContent = gmailState.has_more && Number($('#gmail-days').value) === gmailState.days ? 'Scan next 100 matches' : 'Scan job emails';
}
function gmailAction(action, body = {}) {
  return api(`/api/gmail/${action}`, {method:'POST', headers:{'Content-Type':'application/json','X-Autopilot-Request':'1'}, body:JSON.stringify(body)});
}
function setGmailBusy(value) {
  gmailBusy = value;
  for (const id of ['gmail-connect','gmail-scan','gmail-disconnect','gmail-days']) $(`#${id}`).disabled = value;
}
$('#gmail-setup').addEventListener('click', () => $('#gmail-dialog').showModal());
$('#gmail-days').addEventListener('change', updateScanLabel);
$('#gmail-connect').addEventListener('click', async () => {
  if (gmailBusy) return;
  if (!gmailState.configured) {$('#gmail-dialog').showModal(); return;}
  setGmailBusy(true);
  try {const result = await gmailAction('connect'); window.location.assign(result.url);}
  catch (error) {toast(error.message, true);setGmailBusy(false);}
});
$('#gmail-scan').addEventListener('click', async () => {
  if (gmailBusy) return;
  setGmailBusy(true);
  $('#gmail-scan').textContent = 'Scanning messages…';
  $('#gmail-status').textContent = 'Reading up to 100 matching emails and checking job relevance. This may take a minute.';
  try {
    const result = await gmailAction('sync', {days:Number($('#gmail-days').value)});
    const counts = result.assessment;
    $('#gmail-assessment').hidden = false;
    $('#gmail-assessment').textContent = `Assessment: ${result.scanned} messages checked · ${result.job_related} job-related · ${result.added} newly imported · ${result.skipped} already tracked · ${result.ignored} excluded · ${result.unreadable} unreadable. This batch: ${counts.applied} applied, ${counts.interview} interview, ${counts.rejected} rejected, ${counts.offer} offer, ${counts.scam} potential scam. ${result.has_more ? 'More matches are available—scan the next batch to continue.' : 'Scan complete for this search window. Scan again later to check for new messages.'}`;
    view='all';filter='all';query='';$('#search').value='';
    await refresh();
    toast(`${result.added} Gmail messages added. Open any application to review its assessment and evidence.`);
  } catch (error) {toast(error.message,true);}
  finally {setGmailBusy(false);refreshGmail().catch(error => toast(error.message,true));}
});
$('#gmail-disconnect').addEventListener('click', async () => {
  if (gmailBusy) return;
  setGmailBusy(true);
  try {
    const result = await gmailAction('disconnect');
    $('#gmail-assessment').hidden = true;
    toast(result.revoked ? 'Gmail disconnected. Imported emails remain in your tracker.' : 'Local connection cleared. Google revocation failed; remove access in your Google Account permissions.', !result.revoked);
  } catch (error) {toast(error.message,true);}
  finally {setGmailBusy(false);refreshGmail().catch(() => {});}
});
const gmailOutcome = new URLSearchParams(window.location.search).get('gmail');
if (gmailOutcome) {
  history.replaceState(null, '', '/');
  toast(gmailOutcome === 'connected' ? 'Gmail connected. Choose a scan window and click Scan job emails.' : 'Gmail connection was not completed. Check setup, test-user access and permission, then try again.', gmailOutcome !== 'connected');
}
