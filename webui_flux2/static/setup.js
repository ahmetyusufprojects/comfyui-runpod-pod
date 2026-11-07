'use strict';
const $ = id => document.getElementById(id);
const labels = {idle:'Kurulu değil',queued:'Sırada',running:'Hazırlanıyor',ready:'Hazır',installed:'Dosyalar hazır',error:'Hata',cancelled:'İptal edildi'};
let lastStatus = null, busy = false, logBusy = false, currentUser = null;

async function api(path, options={}) {
  const response = await fetch(path, {cache:'no-store', credentials:'same-origin', ...options});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(body.hata || `HTTP ${response.status}`), {status:response.status});
  return body;
}
function showError(message) { const box=$('errorBox'); box.hidden=!message; box.textContent=message||''; }
function bytes(value) { return Number.isFinite(value) ? `${(value/1048576).toFixed(1)} MiB` : ''; }
function button(text, action, data, disabled=false, style='') {
  const el=document.createElement('button'); el.type='button'; el.textContent=text; el.disabled=disabled;
  if(style) el.className=style;
  el.addEventListener('click',()=>runAction(action,data)); return el;
}
async function runAction(action, data) {
  showError('');
  try {
    lastStatus = await api('/api/pod-kurulum',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...data,islem:action})});
    draw(lastStatus);
  } catch(error) { showError(error.message); }
}
function draw(info) {
  lastStatus=info;
  const groups=info.gruplar||[], queued=info.sirada||[], active=info.aktif;
  const lookup=new Map(groups.map(group=>[group.id,group]));
  const list=$('groups'); list.replaceChildren();
  for(const item of groups) {
    const card=document.createElement('article'); card.className='group';
    const head=document.createElement('div'); head.className='group-head';
    const title=document.createElement('h3'); title.textContent=item.ad;
    const badge=document.createElement('span'); badge.className='state';
    const state=item.durum||'idle'; badge.textContent=labels[state]||state;
    if(state==='ready'||state==='installed') badge.classList.add('good');
    if(state==='running'||state==='queued') badge.classList.add('busy');
    if(state==='error'||state==='cancelled') badge.classList.add('bad');
    head.append(title,badge); card.append(head);
    const detail=document.createElement('p'); detail.className='detail'; detail.textContent=item.ayrinti||''; card.append(detail);
    if(item.bagimliliklar?.length) {
      const deps=document.createElement('p'); deps.className='deps';
      deps.textContent='Önce hazırlanır: '+item.bagimliliklar.map(id=>lookup.get(id)?.ad||id).join(', '); card.append(deps);
    }
    const actions=document.createElement('div'); actions.className='actions';
    const index=queued.indexOf(item.id);
    if(index>=0) {
      actions.append(button('Yukarı al','sira',{grup:item.id,yon:'yukari'},index===0,'quiet'));
      actions.append(button('Aşağı al','sira',{grup:item.id,yon:'asagi'},index===queued.length-1,'quiet'));
      actions.append(button('Sıradan çıkar','iptal',{grup:item.id},false,'quiet'));
    } else if(active===item.id && state==='running') {
      actions.append(button('İndirmeyi iptal et','iptal',{grup:item.id}));
    } else if(state==='ready'||state==='installed') {
      actions.append(button('Modeli kaldır','kaldir',{grup:item.id},false,'quiet'));
    } else {
      actions.append(button(state==='error'||state==='cancelled'?'Yeniden dene':'Kuruluma ekle','hazirla',{grup:item.id}));
      if(state==='error'||state==='cancelled') actions.append(button('Kısmi indirmeyi sil','kaldir',{grup:item.id},false,'quiet'));
    }
    card.append(actions); list.append(card);
  }
  const progress=info.ilerleme||{};
  $('progressPanel').hidden=!active;
  if(active) {
    const model=lookup.get(active);
    $('progressStage').textContent=(model?.ad||active)+' · '+(progress.stage||'Hazırlanıyor');
    $('progressFile').textContent=progress.file||'';
    $('progressSpeed').textContent=Number.isFinite(progress.rate_mib_s)?`${progress.rate_mib_s.toFixed(1)} MiB/sn`:'';
    const downloaded=Number(progress.downloaded), total=Number(progress.total);
    const known=Number.isFinite(downloaded)&&Number.isFinite(total)&&total>0;
    $('progressBar').style.width=known?`${Math.max(0,Math.min(100,100*downloaded/total))}%`:'12%';
    $('progressAmount').textContent=known?`${bytes(downloaded)} / ${bytes(total)}`:
      (Number.isFinite(downloaded)?`${bytes(downloaded)} indirildi · toplam boyut kaynakta belirtilmemiş`:'Kurulum adımı sürüyor…');
  }
}
function loginView(name) {
  currentUser=name||null;
  $('preparing').hidden=true;
  $('loginPanel').hidden=!!currentUser;
  $('dashboard').hidden=!currentUser;
  if(currentUser) $('whoami').textContent=currentUser;
}
async function refresh() {
  if(busy) return; busy=true;
  try {
    const readiness=await api('/api/setup-state');
    $('connection').textContent=readiness.hazir?'Bağlı · kurulum hazır':'Bağlı · hazırlanıyor';
    if(!readiness.hazir) {
      $('preparing').hidden=false; $('loginPanel').hidden=true; $('dashboard').hidden=true;
      $('prepareTitle').textContent=readiness.hata?'Pod hazırlığı tamamlanamadı':'Pod hazırlanıyor';
      $('prepareText').textContent=readiness.hata
        ?'Kurulum servisi açık, ancak sistem hazırlığı tamamlanmadı. RunPod Pod olaylarını ve kurulum loglarını kontrol edin.'
        :'WebUI çalışma alanı ve veri alanı hazırlanıyor. Bu sayfa açık kalabilir; ana WebUI girişinden bağımsızdır.';
      return;
    }
    if(!currentUser) {
      try {
        const me=await api('/api/ben');
        if(me.rol!=='admin') { loginView(null); $('loginError').textContent='Bu panel yönetici girişi gerektiriyor.'; return; }
        loginView(me.kullanici);
      } catch(error) {
        if(error.status!==401) throw error;
        $('preparing').hidden=true; $('loginPanel').hidden=false; $('dashboard').hidden=true;
        return;
      }
    }
    $('preparing').hidden=true; $('loginPanel').hidden=true; $('dashboard').hidden=false;
    const info=await api('/api/pod-kurulum'); draw(info);
  } catch(error) {
    $('connection').textContent='Bağlantı yeniden deneniyor';
    if(error.status===401) { currentUser=null; $('preparing').hidden=true; $('loginPanel').hidden=false; $('dashboard').hidden=true; }
    else showError(error.message);
  } finally { busy=false; }
}
async function loadLogs() {
  if(logBusy||!currentUser) return; logBusy=true;
  $('logs').textContent='Loglar alınıyor…';
  try { const params=new URLSearchParams({kaynak:$('logSource').value,satir:'200'}); const data=await api('/api/log?'+params); $('logs').textContent=data.satirlar?.join('\n')||'Bu kaynakta gösterilecek log yok.'; $('logs').scrollTop=$('logs').scrollHeight; }
  catch(error) { $('logs').textContent='Loglar alınamadı: '+error.message; }
  finally { logBusy=false; }
}
$('loginForm').addEventListener('submit',async event=>{
  event.preventDefault(); $('loginError').textContent='Giriş yapılıyor…';
  try { const result=await api('/api/giris',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kullanici:$('username').value.trim(),sifre:$('password').value})}); $('password').value=''; if(result.rol!=='admin'){ currentUser=null; $('loginError').textContent='Bu panel yönetici girişi gerektiriyor.'; return; } $('loginError').textContent=''; loginView(result.kullanici); await refresh(); }
  catch(error) { $('loginError').textContent=error.status===401?'Kullanıcı adı veya parola hatalı.':error.message; }
});
$('logout').addEventListener('click',async()=>{
  try { await api('/api/cikis',{method:'POST'}); } catch(_) {}
  currentUser=null; lastStatus=null; $('loginError').textContent=''; loginView(null);
});
$('refresh').addEventListener('click',refresh);
$('loadLogs').addEventListener('click',loadLogs);
$('logSource').addEventListener('change',loadLogs);
setInterval(refresh,2200);
setInterval(()=>{if(currentUser&&!$('dashboard').hidden) loadLogs();},8000);
refresh();
