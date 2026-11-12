'use strict';
const $ = id => document.getElementById(id);
const labels = {idle:'Kurulu değil',queued:'Sırada',running:'Hazırlanıyor',paused:'Duraklatıldı',ready:'Hazır',installed:'Dosyalar hazır',error:'Hata',cancelled:'İptal edildi'};
let lastStatus = null, busy = false, logBusy = false, currentUser = null;
let passwordlessInstaller = false;
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
  if(id==='settingsView') { loadSystemSettings(); loadBackupUsers(); }
}
async function loadBackupUsers(){
  const select=$('backupTargetUser'); if(!select||!currentUser)return;
  try{
    const data=await api('/api/backup-users');
    select.replaceChildren();
    for(const user of data.users||[]){const option=document.createElement('option');option.value=user;option.textContent=user;select.append(option);}
    if(!select.options.length) throw new Error('Hesap bulunamadı.');
  }catch(error){$('backupImportStatus').textContent=error.message;}
}
$('backupImportForm')?.addEventListener('submit',async event=>{
  event.preventDefault();
  const file=$('backupFile').files?.[0], user=$('backupTargetUser').value, password=$('backupPassword').value;
  if(!file||!user||!password)return;
  if(!window.confirm(`${user} hesabının kasası bu yedekle değiştirilsin mi?`))return;
  const status=$('backupImportStatus'), button=event.currentTarget.querySelector('button[type="submit"]');
  status.textContent='Şifreli yedek doğrulanıyor…';button.disabled=true;
  try{
    await api('/api/backup-import',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Backup-User':user,'X-Backup-Password':password},body:file});
    $('backupPassword').value='';$('backupFile').value='';
    status.textContent='Yedek içe aktarıldı. Kullanıcı WebUI’den kasasını açabilir.';
  }catch(error){status.textContent='İçe aktarma başarısız: '+error.message;}
  finally{button.disabled=false;}
});
function drawVariantFiles(data) {
  const root=$('modelVariantFiles'); if(!root) return;
  const family=$('modelFamily').value;
  const instance=(data.model_instances||[]).find(item=>item.id===family);
  root.replaceChildren();
  if(!instance) return;
  for(const file of instance.files||[]){
    const row=document.createElement('div'); row.className='model-variant-file';
    const head=document.createElement('div'); head.className='model-variant-file-head';
    const name=document.createElement('span'); name.textContent=file.name;
    if(file.optional){
      const toggle=document.createElement('label'); toggle.className='model-variant-optional';
      const check=document.createElement('input'); check.type='checkbox';
      check.dataset.optionalFile=file.name;
      const caption=document.createElement('span'); caption.textContent='İsteğe bağlı';
      toggle.append(check,caption); head.append(toggle);
    }
    head.append(name);
    const input=document.createElement('input'); input.type='url';
    input.value=file.optional?'':(file.default_link||file.link||''); input.dataset.file=file.name;
    input.dataset.defaultLink=file.default_link||file.link||'';
    input.required=file.name===instance.primary_file;
    input.disabled=!!file.optional;
    input.placeholder=file.optional?'Dahil edeceksen indirme bağlantısını gir.':
      (file.name===instance.primary_file?'Ana dosyanın yeni indirme bağlantısı':'Varsayılan bağlantı kullanılır');
    input.setAttribute('aria-label',file.name+' model bağlantısı');
    if(file.optional){
      const check=head.querySelector('input[data-optional-file]');
      check.onchange=()=>{
        input.disabled=!check.checked;
        input.required=check.checked;
        if(!check.checked) input.value='';
      };
    }
    row.append(head,input); root.append(row);
  }
}
function drawModelInstances(data) {
  const families=data.model_families||[], instances=data.model_instances||[];
  const familySelect=$('modelFamily'), previous=familySelect.value;
  familySelect.replaceChildren();
  for(const family of families){
    const option=document.createElement('option'); option.value=family.id;
    option.textContent=family.name; familySelect.append(option);
  }
  if(families.some(item=>item.id===previous)) familySelect.value=previous;
  familySelect.onchange=()=>drawVariantFiles(data);
  drawVariantFiles(data);
  const root=$('modelInstances'); root.replaceChildren();
  for(const instance of instances){
    const row=document.createElement('article'); row.className='model-instance-row';
    const type=document.createElement('span'); type.className='muted';
    type.textContent=(families.find(item=>item.id===instance.family)?.name||instance.family)
      +' · '+(instance.ready?'Dosya hazır':'Henüz indirilmedi');
    const name=document.createElement('input'); name.value=instance.name;
    name.maxLength=80; name.setAttribute('aria-label','Model adı: '+instance.name);
    const actions=document.createElement('div'); actions.className='actions';
    const save=document.createElement('button'); save.type='button'; save.className='quiet'; save.textContent='Adı kaydet';
    save.onclick=async()=>{
      try{
        const result=await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({islem:'model_ad',id:instance.id,name:name.value.trim()})});
        drawSystemSettings(result); $('settingsStatus').textContent='Model adı kaydedildi.';
      }catch(error){showError(error.message);}
    };
    actions.append(save);
    if(!instance.builtin){
      const remove=document.createElement('button'); remove.type='button'; remove.className='quiet'; remove.textContent='Model kaydını sil';
      remove.onclick=async()=>{
        if(!window.confirm('Bu model kaydını ve yalnızca ona ait indirilen dosyaları silmek istiyor musun?')) return;
        try{
          const result=await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({islem:'model_sil',id:instance.id})});
          drawSystemSettings(result); lastStatus=await api('/api/pod-kurulum'); draw(lastStatus);
        }catch(error){showError(error.message);}
      };
      actions.append(remove);
    }
    row.append(type,name,actions); root.append(row);
  }
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
  const imageGroups={z_image:'zimage',flux2:'flux',flux3:'flux3',krea2:'krea2',illustrious:'illustrious'};
  const availableGroups=new Set(groups.map(group=>group.id));
  const images=[['z_image','Z-Image · Moody Pro'],['flux2','Flux 2 Klein'],['flux3','Flux 3 Klein'],['krea2','Krea 2'],['illustrious','Illustrious']]
    .filter(([id])=>availableGroups.has(imageGroups[id]));
  const availableImageModels=new Set(images.map(([id])=>id));
  for(const option of $('loraModel').options)
    option.disabled=!availableImageModels.has(option.value);
  if($('loraModel').selectedOptions[0]?.disabled)
    $('loraModel').value=images[0]?.[0]||'';
  drawModelInstances(data);
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
    if(!availableImageModels.has(lora.model)) continue;
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
    drawSystemSettings(result);
    $('settingsStatus').textContent=result.restarted_groups?.length
      ? 'Kurulu model dosyası yenileniyor; indirme kuyruğa alındı.'
      : 'Bağlantı kaydedildi; model henüz kurulu olmadığı için indirme başlamadı.';
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
  $('logout').hidden=passwordlessInstaller;
  if(currentUser) $('whoami').textContent=passwordlessInstaller?'Kurulum paneli · parolasız':currentUser;
}
async function refresh() {
  if(busy) return; busy=true;
  try {
    const readiness=await api('/api/setup-state');
    $('connection').textContent=readiness.hazir?'Bağlı · kurulum hazır':'Bağlı · hazırlanıyor';
    const storage=$('storageMode');
    if(storage){
      const storageKnown=['ephemeral','persistent'].includes(readiness.storage_mode);
      storage.hidden=!storageKnown;
      storage.textContent=readiness.storage_persistent?'Kalıcı volume bağlı':'Volume yok · geçici disk';
      storage.title=readiness.storage_persistent?'Genel ayarlar ve doğrulanmış kasa kopyaları volume üzerinde saklanır.':'Pod durdurulur, yeniden başlatılır veya silinirse kullanıcı verileri ve ayarlar kaybolur.';
    }
    const storageExplanation=$('storageExplanation');
    if(storageExplanation){
      storageExplanation.textContent=readiness.storage_persistent
        ?'Modeller ve kurulum dosyaları geçici Pod diskinde hazırlanır. Genel ayarlar ve doğrulanmış kasa yedekleri bağlı volume üzerinde saklanır.'
        :'Volume bağlı değil. Modeller, ayarlar, hesap ve kullanıcı verileri geçici diskte tutulur; Pod durdurulur, yeniden başlatılır veya silinirse silinir.';
    }
    const noPassword=readiness.parolasiz===true;
    if(noPassword!==passwordlessInstaller){passwordlessInstaller=noPassword;currentUser=null;}
    $('logout').hidden=passwordlessInstaller;
    if(!readiness.hazir) {
      $('preparing').hidden=false; $('loginPanel').hidden=true; $('dashboard').hidden=true;
      $('prepareTitle').textContent=readiness.hata?'Pod hazırlığı tamamlanamadı':'Pod hazırlanıyor';
      $('prepareText').textContent=readiness.hata
        ?'Kurulum servisi açık, ancak sistem hazırlığı tamamlanmadı. RunPod Pod olaylarını ve kurulum loglarını kontrol edin.'
        :'WebUI çalışma alanı ve veri alanı hazırlanıyor. Bu sayfa açık kalabilir; ana WebUI girişinden bağımsızdır.';
      return;
    }
    if(passwordlessInstaller) {
      loginView('Kurulum paneli');
    } else if(!currentUser) {
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
$('cleanStorage').addEventListener('click',async()=>{
  const button=$('cleanStorage'), status=$('cleanStorageStatus');
  if(!window.confirm('ComfyUI, indirilen modeller, LLM dosyaları ve önbellekler silinsin mi? Kullanıcı hesapları, kasa, arşiv ve genel ayarlar korunur. Modelleri daha sonra yeniden indirmen gerekir.')) return;
  button.disabled=true; status.textContent='Aktif işler kontrol ediliyor…';
  try{
    const result=await api('/api/storage-clean',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({onay:true})});
    const amount=Number(result.freed_bytes)||0;
    status.textContent=`Temizlendi · ${result.removed||0} konum, ${(amount/1073741824).toFixed(1)} GiB boşaltıldı. Kullanıcı verileri ve genel ayarlar korundu.`;
    lastStatus=await api('/api/pod-kurulum'); draw(lastStatus);
  }catch(error){status.textContent=error.message;}
  finally{button.disabled=false;}
});
$('modelForm').addEventListener('submit',async event=>{
  event.preventDefault();
  const status=$('modelFormStatus'), family=$('modelFamily').value;
  const base=(systemSettingsData?.model_instances||[]).find(item=>item.id===family);
  const links={}, exclude_files=[];
  for(const input of $('modelVariantFiles').querySelectorAll('input[data-file]')){
    const optional=Array.from($('modelVariantFiles').querySelectorAll('input[data-optional-file]'))
      .find(item=>item.dataset.optionalFile===input.dataset.file);
    if(optional&&!optional.checked){exclude_files.push(input.dataset.file);continue;}
    const link=input.value.trim();
    if(optional&&!link){
      status.textContent=`${input.dataset.file} için bağlantı gir veya bu dosyayı isteğe bağlı bölümünde kapalı bırak.`;
      return;
    }
    if(link!==input.dataset.defaultLink) links[input.dataset.file]=link;
  }
  if(!base || !links[base.primary_file]){
    status.textContent='Ana model dosyası için farklı bir indirme bağlantısı gir.';
    return;
  }
  status.textContent='Model kaydı ekleniyor…';
  try{
    const data=await api('/api/system-settings',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({islem:'model_ekle',family,name:$('modelName').value.trim(),links,exclude_files})});
    drawSystemSettings(data); $('modelName').value='';
    status.textContent='Model kaydedildi. İndirmeyi İndirmeler bölümünden başlatabilirsin.';
    lastStatus=await api('/api/pod-kurulum'); draw(lastStatus);
  }catch(error){status.textContent=error.message;}
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
$('updateApp').addEventListener('click',async()=>{
  const status=$('updateAppStatus'), button=$('updateApp');
  button.disabled=true; status.textContent='Güncelleme hazırlanıyor…';
  try{
    await api('/api/app-update',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    let disconnected=false;
    for(let i=0;i<180;i++){
      await new Promise(resolve=>setTimeout(resolve,2000));
      try{
        const state=await api('/api/app-update');
        if(state.status==='error') throw new Error(state.message||'Güncelleme başarısız.');
        if(state.status==='ready'){ status.textContent='Güncelleme tamamlandı. Sayfa yenileniyor…'; location.reload(); return; }
        if(state.status==='restarting') status.textContent='Kasalar mühürlendi; uygulama yeniden açılıyor…';
      }catch(error){
        if(error.status===401 && disconnected){ location.reload(); return; }
        if(error.status || error.message==='Failed to fetch'){ disconnected=true; status.textContent='Uygulama yeniden açılıyor…'; continue; }
        throw error;
      }
    }
    status.textContent='Uygulamanın açılması bekleniyor; sayfayı biraz sonra yenileyin.';
  }catch(error){ status.textContent=error.message; }
  finally{ button.disabled=false; }
});
$('restartApp').addEventListener('click',async()=>{
  const button=$('restartApp'), status=$('restartAppStatus');
  if(!window.confirm('Uygulama servisleri yeniden başlatılsın mı? İndirilen model dosyaları korunur.')) return;
  button.disabled=true; status.textContent='Güvenli kapanış ve yeniden başlatma bekleniyor…';
  try{
    await api('/api/app-restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    for(let i=0;i<120;i++){
      await new Promise(resolve=>setTimeout(resolve,2000));
      try{
        const state=await api('/api/setup-state');
        if(state.hazir){status.textContent='Uygulama yeniden başladı; indirilen modeller korundu.';await refresh();return;}
      }catch(_){}
    }
    throw new Error('Uygulama 4 dakika içinde yeniden yanıt vermedi.');
  }catch(error){status.textContent=error.message;}
  finally{button.disabled=false;}
});
$('loadLogs').addEventListener('click',loadLogs);
$('logSource').addEventListener('change',loadLogs);
setInterval(refresh,2200);
setInterval(()=>{if(currentUser&&!$('dashboard').hidden) loadLogs();},8000);
refresh();
