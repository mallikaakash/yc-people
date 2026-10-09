const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const href = value => { try { const u = new URL(value); return ['https:','http:'].includes(u.protocol) ? esc(u.href) : '#'; } catch { return '#'; } };
const labels = {yc_founder:'YC founder',company_listed:'Company-listed',github_member:'GitHub member'};
const icons = {linkedin:'in ↗',github:'GitHub ↗',x:'X ↗',website:'Web ↗'};
const snapshot = document.getElementById('snapshot');
const offline = snapshot ? JSON.parse(snapshot.textContent) : null;
let state = {companies:[],people:[],runs:[],years:[]}, tab = 'people', page = 0, timer, loading = false, activeRun = null, requestId = 0;
$('import-year').value = new Date().getFullYear();
$('import-year').max = new Date().getFullYear();
const filterKeys = ['year','batch','platform','relation'];
const query = () => { const q = new URLSearchParams(); for (const key of filterKeys) if ($(key).value) q.set(key,$(key).value); if ($('search').value) q.set('q',$('search').value); return q; };
function options(id, values, label) { const el = $(id), selected = el.value; el.innerHTML = `<option value="">${label}</option>` + values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join(''); el.value = values.map(String).includes(selected) ? selected : ''; }
function notice(message) { $('notice').textContent = message; $('notice').hidden = !message; }
async function load() {
  const id = ++requestId;
  try {
    let data;
    if (offline) {
      const q=query(), text=(q.get('q')||'').toLowerCase();
      const matchYear=c=>(!q.get('year')||String(c.year)===q.get('year'));
      const matchCompany=c=>matchYear(c)&&(!q.get('batch')||c.batch===q.get('batch'));
      data={...offline,companies:offline.companies.filter(matchCompany),people:offline.people.filter(p=>matchCompany(p)&&(!text||[p.name,p.company,p.role].join(' ').toLowerCase().includes(text))&&(!q.get('relation')||p.relation===q.get('relation'))&&(!q.get('platform')||p.profiles.some(u=>u.platform===q.get('platform'))))};
    } else {
      const r = await fetch('/api/data?'+query()); if (!r.ok) throw new Error('Could not read the local database.');
      data = await r.json();
    }
    if (id !== requestId) return; state = data;
    options('year',state.years,'All years');
    const availableBatches = [...new Set(state.companies.map(c=>c.batch))].sort();
    if (!$('batch').value) options('batch',availableBatches,'All batches');
    const run = state.runs.find(r=>r.id===activeRun) || state.runs[0];
    if (run && !offline) {
      const running = ['running','queued'].includes(run.status);
      if (running) notice(`Import ${run.year} · ${run.processed}/${run.total} scanned · ${run.message}`);
      else if (activeRun === run.id) { notice(run.message); activeRun = null; }
      // A stale job after a process restart must not prevent a new resume request.
      $('import-button').disabled = activeRun !== null && running;
      $('import-button').textContent = activeRun !== null && running ? 'Importing…' : 'Import year →';
    }
    render();
  } catch(e) { notice(e.message); }
}
function render() {
  $('company-count').textContent = state.companies.length.toLocaleString();
  $('people-count').textContent = state.people.length.toLocaleString();
  $('profile-count').textContent = state.people.reduce((n,p)=>n+p.profiles.length,0).toLocaleString();
  $('scan-count').textContent = state.companies.filter(c=>c.scan_status==='complete').length.toLocaleString();
  $('export').href = '/api/export?'+query();
  let rows = tab === 'people' ? state.people : tab === 'companies' ? state.companies : state.runs;
  if (tab === 'companies' && $('search').value) rows = rows.filter(c=>(c.name+' '+c.description).toLowerCase().includes($('search').value.toLowerCase()));
  page = Math.max(0,Math.min(page,Math.ceil(rows.length/25)-1));
  const slice = rows.slice(page*25,page*25+25);
  $('empty').hidden = !!rows.length;
  $('empty').querySelector('h3').textContent = state.years.length ? 'No matching records yet.' : 'Your next connection starts here.';
  $('result-count').textContent = `${rows.length.toLocaleString()} records`;
  $('pagination').hidden = rows.length <= 25;
  $('page-label').textContent = `Page ${page+1} of ${Math.max(1,Math.ceil(rows.length/25))}`;
  $('prev').disabled = page===0; $('next').disabled = (page+1)*25>=rows.length;
  let headers, body;
  if (tab === 'people') {
    headers = ['Person','Company','Batch','Public profiles','Affiliation'];
    body = slice.map(p=>`<tr><td><div class="person"><span class="avatar">${esc(p.name.split(/\s+/).map(n=>n[0]).slice(0,2).join('').toUpperCase())}</span><div><button class="name" data-person="${p.id}">${esc(p.name)}</button><span class="sub">${esc(p.role||'Role not listed')}</span></div></div></td><td>${esc(p.company)}</td><td><span class="batch">${esc(p.batch)}</span></td><td>${p.profiles.map(u=>`<a class="profile" href="${href(u.url)}" target="_blank" rel="noopener noreferrer">${icons[u.platform]||'Web ↗'}</a>`).join('')||'<span class="muted">Not published</span>'}</td><td><span class="badge ${p.relation==='github_member'?'warning':''}">${labels[p.relation]||esc(p.relation)}</span></td></tr>`).join('');
  } else if (tab === 'companies') {
    headers = ['Company','Batch','Industry','Scan status','Source'];
    body = slice.map(c=>`<tr><td><button class="name" data-company="${esc(c.slug)}">${esc(c.name)}</button><span class="sub">${esc(c.description)}</span></td><td><span class="batch">${esc(c.batch)}</span></td><td>${esc(c.industry||'—')}</td><td><span class="badge ${['partial','failed'].includes(c.scan_status)?'warning':''}">${esc(c.scan_status)}</span>${c.warnings.length?`<span class="sub">${c.warnings.length} source notes · click company</span>`:''}</td><td><a class="profile" href="${href(c.yc_url)}" target="_blank" rel="noopener noreferrer">YC ↗</a></td></tr>`).join('');
  } else {
    headers = ['Year','Started','Status','Progress','Details'];
    body = slice.map(r=>`<tr><td>${r.year}</td><td>${esc(new Date(r.started_at).toLocaleString())}</td><td><span class="badge">${esc(r.status)}</span></td><td>${r.processed}/${r.total}</td><td>${esc(r.message)}</td></tr>`).join('');
  }
  $('table-wrap').innerHTML = rows.length ? `<table><thead><tr>${headers.map(h=>`<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>` : '';
}
$('table-wrap').addEventListener('click',e=>{
  const personButton=e.target.closest('[data-person]'), companyButton=e.target.closest('[data-company]');
  if (personButton) {
    const p=state.people.find(p=>p.id===Number(personButton.dataset.person));
    $('detail-title').textContent=p.name;
    $('detail-body').innerHTML=`<p>${esc(p.role)} · ${esc(p.company)}</p><p>First observed ${esc(new Date(p.first_seen).toLocaleDateString())}. Last observed ${esc(new Date(p.last_seen).toLocaleDateString())}. Observations do not verify current employment.</p>`+p.evidence.map(e=>`<article><strong>${labels[e.relation]}</strong><p>${esc(e.note)}</p><a href="${href(e.source_url)}" target="_blank" rel="noopener noreferrer">View source ↗</a><small>Observed ${esc(e.last_seen)}</small></article>`).join('');
  } else if (companyButton) {
    const c=state.companies.find(c=>c.slug===companyButton.dataset.company);
    $('detail-title').textContent=c.name;
    $('detail-body').innerHTML=`<p>${esc(c.description)}</p><p>${esc(c.batch)} · Reported team size: ${esc(c.team_size??'Unknown')}</p><p>Last scan: ${esc(c.scanned_at||'Not scanned')}</p>`+c.warnings.map(w=>`<article>${esc(w)}</article>`).join('')+'<p>Team size is source metadata, not a measure of profile coverage.</p>';
  } else return;
  $('details').showModal();
});
$('close-details').onclick=()=>$('details').close();
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{
  tab=b.dataset.tab; page=0;
  document.querySelectorAll('[data-tab]').forEach(n=>n.classList.toggle('active',n===b));
  const label={people:'People directory',companies:'Companies',runs:'Import history'}[tab];
  $('section-title').textContent=label; $('crumb').textContent=label;
  $('platform').hidden=tab!=='people'; $('relation').hidden=tab!=='people';
  render();
});
$('prev').onclick=()=>{page--;render();}; $('next').onclick=()=>{page++;render();};
for (const key of filterKeys) $(key).onchange=()=>{page=0;if(key==='year') options('batch',[],'All batches');load();};
$('search').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{page=0;load();},250);};
$('import-form').onsubmit=async e=>{
  e.preventDefault(); if(loading)return;loading=true;$('import-button').disabled=true;
  try {
    const r=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({year:Number($('import-year').value),limit:Number($('limit').value),refresh:$('refresh').checked})});
    const data=await r.json();if(!r.ok)throw new Error(data.error||'Import could not start');
    activeRun=data.run_id; notice('Discovering all public batches for your year…'); await load();
  } catch(e) {notice(e.message);$('import-button').disabled=false;} finally {loading=false;}
};
if (offline) {
  document.querySelector('.import-card').hidden=true;
  document.querySelector('.brand').href='#';
  document.querySelector('.local').textContent='SNAPSHOT';
  document.querySelector('.private').textContent='◈ Static directory';
  document.querySelector('.side-bottom').textContent='Public sources · Downloadable snapshot';
  notice('Directory snapshot · Generated '+new Date(offline.generated_at).toLocaleString()+'. To collect a year, use Run a year in GitHub Actions.');
  if (offline.workflow_url) {
    const action=document.createElement('a'); action.className='button primary';action.href=offline.workflow_url;action.textContent='Run a year ↗';action.target='_blank';action.rel='noopener noreferrer';
    document.querySelector('.heading').appendChild(action);
  }
  $('export').onclick=e=>{
    e.preventDefault();
    const rows=[['company','batch','person','role','affiliation','platform','profile_url','source_urls','first_seen','last_seen']];
    state.people.forEach(p=>(p.profiles.length?p.profiles:[{platform:'',url:''}]).forEach(u=>rows.push([p.company,p.batch,p.name,p.role,p.relation,u.platform,u.url,p.evidence.map(e=>e.source_url).join(' | '),p.first_seen,p.last_seen])));
    const cell=v=>{let s=String(v??'');if(/^[\s]*[=+\-@]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
    const blob=new Blob(['\ufeff'+rows.map(row=>row.map(cell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='yc-people.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  $('empty').querySelector('p').textContent='Try another filter, or run the GitHub Actions workflow to collect more profiles.';
} else setInterval(load,3000);
load();
