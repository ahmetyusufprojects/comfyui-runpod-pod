'use strict';
const $ = id => document.getElementById(id);
const labels = {idle:'Kurulu değil',queued:'Sırada',running:'Hazırlanıyor',paused:'Duraklatıldı',ready:'Hazır',installed:'Dosyalar hazır',error:'Hata',cancelled:'İptal edildi'};
let lastStatus = null, busy = false, logBusy = false, currentUser = null;
let systemSettingsLoaded = false, systemSettingsData = null;

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
  if(action==='saveLora') return saveLora(data.id,data.row);
  if(action==='deleteLora') return deleteLora(data.id);
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
    if(state==='running'||state==='queued'||state==='paused') badge.classList.add('busy');
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
      actions.append(button('Duraklat','duraklat',{grup:item.id},false,'quiet'));
      actions.append(button('Sıradan çıkar','iptal',{grup:item.id},false,'quiet'));
    } else if(active===item.id && state==='running') {
      actions.append(button('Duraklat','duraklat',{grup:item.id},false,'quiet'));
      actions.append(button('İndirmeyi iptal et','iptal',{grup:item.id}));
    } else if(state==='paused') {
      actions.append(button('Devam et','devam',{grup:item.id}));
      actions.append(button('İndirmeyi iptal et','iptal',{grup:item.id},false,'quiet'));
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
function switchView(id) {
  for(const view of ['downloadsView','settingsView']) $(view).hidden=view!==id;
  document.querySelectorAll('.setup-tabs button').forEach(button=>
    button.classList.toggle('active',button.dataset.view===id));
  if(id==='settingsView') loadSystemSettings();
}
function drawSystemSettings(data) {
  systemSettingsData=data;
  const settings=data.ayarlar||{}, groups=data.gruplar||[];
  $('autoDownload').checked=settings.auto_download===true;
  const groupMap=new Map(groups.map(group=>[group.id,group]));
  const order=[...(settings.default_download_order||[]).filter(id=>groupMap.has(id)),
    ...groups.map(group=>group.id).filter(id=>!(settings.default_download_order||[]).includes(id))];
  const list=$('defaultOrder'); list.replaceChildren();
  for(const id of order){
    const group=groupMap.get(id); if(!group) continue;
    const row=document.createElement('div'); row.className='order-row'; row.dataset.group=id;
    const name=document.createElement('label'); name.className='order-name';
    const check=document.createElement('input'); check.type='checkbox';
    check.checked=(settings.default_download_order||[]).includes(id);
    check.dataset.group=id;
    const label=document.createElement('span'); label.textContent=group.label;
    name.append(check,label);
    const controls=document.createElement('div'); controls.className='order-controls';
    const up=document.createElement('button'); up.type='button'; up.className='quiet'; up.textContent='↑'; up.title='Öne al';
    const down=document.createElement('button'); down.type='button'; down.className='quiet'; down.textContent='↓'; down.title='Sona al';
    up.onclick=()=>{if(row.previousElementSibling) list.insertBefore(row,row.previousElementSibling);};
    down.onclick=()=>{if(row.nextElementSibling) list.insertBefore(row.nextElementSibling,row);};
    controls.append(up,down); row.append(name,controls); list.append(row);
  }
  const llm=$('llmDefaults'); llm.replaceChildren();
  const models=[['llama','Gemma 4 E4B'],['llm_12b','Gemma 4 12B'],['llm_26b','Gemma 4 26B A4B']];
  const modelsPresent=new Set(groups.filter(group=>group.id==='llama'||group.id==='llm_12b'||group.id==='llm_26b').map(group=>group.id));
  const images=[['z_image','Z-Image · Moody Pro'],['flux2','Flux 2 Klein'],['flux3','Flux 3 Klein'],['krea2','Krea 2'],['illustrious','Illustrious']];
  const makeSelect=(caption,key,value)=>{
    const row=document.createElement('label'); row.className='llm-default-row';
    const name=document.createElement('span'); name.textContent=caption;
    const select=document.createElement('select'); select.dataset.key=key;
    for(const [id,label] of models){if(!modelsPresent.has(id)) continue; const option=document.createElement('option');option.value=id;option.textContent=label;select.append(option);}
    if(Array.from(select.options).some(option=>option.value===value)) select.value=value;
    row.append(name,select); llm.append(row);
  };
  for(const [key,label] of images) makeSelect(label,key,(settings.llm_defaults?.image||{})[key]||'llama');
  makeSelect('Yalnızca sohbet','chat',settings.llm_defaults?.chat||'llm_26b');
  const links=$('modelLinks'); links.replaceChildren();
  for(const group of groups){
    for(const file of group.files||[]){
      if(!file.link) continue;
      const row=document.createElement('div'); row.className='link-row';
      const label=document.createElement('label'); label.textContent=group.label+' · '+file.name;
      const input=document.createElement('input'); input.type='url'; input.value=file.link; input.setAttribute('aria-label',label.textContent+' bağlantısı');
      const actions=document.createElement('div'); actions.className='link-actions';
      const save=document.createElement('button'); save.type='button'; save.textContent='Kaydet';
      save.onclick=()=>saveModelLink(group.id,file.name,input.value,false);
      const reset=document.createElement('button'); reset.type='button'; reset.className='quiet'; reset.textContent='Varsayılan';
      reset.onclick=()=>saveModelLink(group.id,file.name,'',true);
      actions.append(save,reset); row.append(label,input,actions); links.append(row);
    }
  }
  const loraList=$('loraList'); loraList.replaceChildren();
  for(const lora of settings.loras||[]){
    const row=document.createElement('article'); row.className='lora-row'; row.dataset.id=lora.id;
    const title=document.createElement('div'); title.className='lora-row-title';
    const heading=document.createElement('b'); heading.textContent=lora.name;
    const state=document.createElement('span'); state.className='state';
    const status=(data.lora_durum||[]).find(item=>item.id===lora.id);
    state.textContent=status?.hazir?'Dosya hazır':(status?.durum==='running'?'İndiriliyor':'İndirilmemiş');
    if(status?.hazir) state.classList.add('good'); else if(status?.durum==='running') state.classList.add('busy');
    title.append(heading,state);
    const model=document.createElement('p'); model.className='muted';
    model.textContent='Uyumlu model: '+({z_image:'Z-Image',flux2:'Flux 2 Klein',flux3:'Flux 3 Klein',krea2:'Krea 2',illustrious:'Illustrious'}[lora.model]||lora.model);
    const controls=document.createElement('div'); controls.className='lora-row-controls';
    const checkbox=(caption,key)=>{const label=document.createElement('label');label.className='setting-toggle compact';const input=document.createElement('input');input.type='checkbox';input.checked=!!lora[key];input.dataset.key=key;const span=document.createElement('span');span.textContent=caption;label.append(input,span);return label;};
    controls.append(checkbox('Kullanılabilir','enabled'),checkbox('Varsayılan seç','default_selected'),checkbox('Varsayılan sıraya ekle','auto_download'));
    const weightLabel=document.createElement('label');weightLabel.className='lora-weight';weightLabel.textContent='Ağırlık';
    const weight=document.createElement('input');weight.type='number';weight.min='0';weight.max='2';weight.step='0.05';weight.value=Number(lora.weight||1).toFixed(2);weight.dataset.key='weight';weightLabel.append(weight);controls.append(weightLabel);
    const actions=document.createElement('div');actions.className='actions';
    actions.append(button('Ayarları kaydet','saveLora',{id:lora.id,row},false,'quiet'));
    if(!lora.built_in) actions.append(button('LoRA kaydını sil','deleteLora',{id:lora.id},false,'quiet'));
    row.append(title,model,controls,actions); loraList.append(row);
  }
  systemSettingsLoaded=true;
}
async function loadSystemSettings(force=false) {
  if(systemSettingsLoaded&&!force) return;
  try { drawSystemSettings(await api('/api/system-settings')); }
  catch(error) { showError(error.message); }
}
async function saveModelLink(group,file,link,restore) {
  showError(''); $('settingsStatus').textContent='';
  try {
    const result=await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({islem:restore?'varsayilan':'baglanti',grup:group,dosya:file,baglanti:link})});
    drawSystemSettings(result); $('settingsStatus').textContent='Bağlantı kaydedildi; model dosyası yeniden indirilecek.';
    lastStatus=await api('/api/pod-kurulum'); draw(lastStatus);
  } catch(error) { showError(error.message); }
}
async function saveLora(id,row){
  const payload={islem:'lora_ayar',id};
  for(const input of row.querySelectorAll('[data-key]')){
    const key=input.dataset.key; payload[key]=key==='weight'?Number(input.value):input.checked;
  }
  try{drawSystemSettings(await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));$('settingsStatus').textContent='LoRA ayarları kaydedildi.';}
  catch(error){showError(error.message);}
}
async function deleteLora(id){
  if(!window.confirm('LoRA kaydını ve geçici disk üzerindeki dosyasını silmek istiyor musun?')) return;
  try{drawSystemSettings(await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({islem:'lora_sil',id})}));$('settingsStatus').textContent='LoRA kaldırıldı.';}
  catch(error){showError(error.message);}
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
document.querySelectorAll('.setup-tabs button').forEach(button=>
  button.addEventListener('click',()=>switchView(button.dataset.view)));
$('saveSystemSettings').addEventListener('click',async()=>{
  const order=Array.from($('defaultOrder').children).filter(row=>row.querySelector('input').checked).map(row=>row.dataset.group);
  $('settingsStatus').textContent='Kaydediliyor…';
  try {
    const data=await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({islem:'kaydet',auto_download:$('autoDownload').checked,default_download_order:order})});
    drawSystemSettings(data); $('settingsStatus').textContent='Genel ayarlar kaydedildi.';
  } catch(error) { $('settingsStatus').textContent=error.message; }
});
$('saveLlmDefaults').addEventListener('click',async()=>{
  $('llmDefaultsStatus').textContent='Kaydediliyor…';
  const image={}; for(const select of $('llmDefaults').querySelectorAll('select[data-key]')) if(select.dataset.key!=='chat') image[select.dataset.key]=select.value;
  const chat=$('llmDefaults').querySelector('select[data-key="chat"]')?.value||'llm_26b';
  try {
    const data=await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({islem:'kaydet',llm_defaults:{image,chat}})});
    drawSystemSettings(data); $('llmDefaultsStatus').textContent='Model eşleşmeleri kaydedildi.';
  } catch(error) { $('llmDefaultsStatus').textContent=error.message; }
});
$('loraForm').addEventListener('submit',async event=>{
  event.preventDefault(); $('loraFormStatus').textContent='LoRA kaydediliyor…';
  try{
    const data=await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({islem:'lora_ekle',name:$('loraName').value.trim(),model:$('loraModel').value,link:$('loraLink').value.trim()})});
    $('loraName').value=''; $('loraLink').value=''; drawSystemSettings(data); $('loraFormStatus').textContent='LoRA eklendi. Kurulum listesinden indirebilirsin.';
  }catch(error){$('loraFormStatus').textContent=error.message;}
});
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
