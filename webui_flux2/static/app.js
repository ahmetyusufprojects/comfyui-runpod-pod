"use strict";
/* Video ve görsel üretim aynı sıradaki GPU iş kuyruğunu kullanır. */
const VIDEO_HAZIRLIK = {suresn: 0, fps: 0};
let RUNPOD_WEBUI = false;
const RUNPOD_IMAGE_DEFAULTS = {
  z_image: {steps: 9, sampler: 'dpmpp_2m_sde', cfg: 1},
  flux2: {steps: 4, sampler: 'euler', cfg: 1},
  flux3: {steps: 16, sampler: 'euler', cfg: 1},
  krea2: {steps: 8, sampler: 'euler_ancestral', cfg: 1},
  qwen_image_edit: {steps: 25, sampler: 'euler', cfg: 1},
  qwen_image_edit_turbo: {steps: 8, sampler: 'euler', cfg: 1},
  illustrious: {steps: 28, sampler: 'euler_ancestral', cfg: 5}
};
let fotoB64 = null, fotoB64_2 = null, fotoMime = null, fotoMime2 = null, takipIs = null;
let gecSayfa = 1, gecListe = [], gecIdx = 0, gecToplam = 0, gecGorunum = 'izgara', prOnbellek = [];
let gecFavoriler = false;
let gecAktifAlbum = null, gecAlbumKayitIdx = -1;
let gecAlbumSayfa = 1, gecAlbumToplam = 0, gecAlbumBaslangic = 0;
let gecListeIstekNesli = 0, gecAlbumIstekNesli = 0, gecGorselSecimNesli = 0;
let gecOnizlemeObserver = null;
const gecFavoriIstekleri = new Map();
let uretimSeciliKayit = null;
let uretimZoom = 1, uretimOffsetX = 0, uretimOffsetY = 0;
let uretimPinchMesafe = 0, uretimPinchDurum = null, uretimPanDurum = null, uretimPinchZamani = 0;
let uretimGecmisGenislikUygula = null;
let psGecmisSayfa = 1, psGecmisTum = [], psGecmisFavoriler = false;
let promptDestekYaraticilik = 0.7, promptDestekUzunluk = 'orta';
let uretimVarsayilan = {steps: 9, seed: -1, max_side: 1536};
let uretimKesintisizAktif = false, uretimKesintisizIstek = null;
let llmVarsayilanlar = null;
let runpodModelOptions = [], runpodLlmOptions = [];
let runpodModelSignature = '', runpodLlmSignature = '';
let runpodLlmTuningDraft = {}, runpodLlmTuningReady = false;
let runpodLoraCurrentModel = null, runpodLoraSaved = null;
const runpodLoraSelections = new Map();
let runpodLoraRequestId = 0;
let aktifMenu = 'uretim';
function runpodKurulumAdres(){
  const url=new URL(location.href);
  if(url.hostname.includes('-8189.proxy.runpod.net'))
    url.hostname=url.hostname.replace('-8189.proxy.runpod.net','-8192.proxy.runpod.net');
  else url.port='8192';
  url.pathname='/';url.search='';url.hash='';return url.href;
}
function videoMu(){
  try{ return $('wf').value === 'video'; }catch(e){ return false; }
}
function isTipiSec(){
  if(videoMu()) return 'video-uretim';
  const refVar = !!(fotoB64 || fotoB64_2);
  if(!refVar) return 'goruntu-uretim';
  return 'goruntu-duzenleme';
}
/* Otomatik model/iş akışı: RunPod seçili modeli kullanır; Windows Anima tercihini korur. */
function refSayisi(){ return (fotoB64 ? 1 : 0) + (fotoB64_2 ? 1 : 0); }
function animaModelSecimiUygula(model){
  const anima = $('animaModel'), moody = $('moodyAnimaModel');
  anima.checked = model === 'anima';
  moody.checked = model === 'moody_anima';
}
function animaSecili(){ return !RUNPOD_WEBUI && !videoMu() && !refSayisi() &&
  ($('animaModel').checked || $('moodyAnimaModel').checked); }
function runpodLoraOku(){
  const root=$('runpodLoraOptions');
  if(!root) return [];
  return Array.from(root.querySelectorAll('input[data-lora-id]:checked')).map(input=>{
    const weight=root.querySelector(`[data-lora-weight="${CSS.escape(input.dataset.loraId)}"]`);
    return {id:input.dataset.loraId,weight:weight?Number(weight.value):1};
  });
}
async function runpodLoraYukle(){
  if(!RUNPOD_WEBUI) return;
  const model=otomatikModel();
  if(runpodLoraCurrentModel) runpodLoraSelections.set(runpodLoraCurrentModel,runpodLoraOku());
  runpodLoraCurrentModel=model;
  const requestId=++runpodLoraRequestId;
  const picker=$('runpodLoraPicker'), root=$('runpodLoraOptions');
  if(!picker||!root) return;
  picker.hidden=videoMu();
  root.replaceChildren();
  try{
    const response=await _fetch('/api/loras?model='+encodeURIComponent(model));
    const data=await response.json();
    if(requestId!==runpodLoraRequestId||model!==otomatikModel()) return;
    if(!response.ok) throw new Error(data.hata||'HTTP '+response.status);
    const loras=data.loras||[];
    const chosen=runpodLoraSelections.has(model)?runpodLoraSelections.get(model):runpodLoraSaved;
    for(const lora of loras){
      const row=document.createElement('label');row.className='runpod-lora-option';
      const check=document.createElement('input');check.type='checkbox';check.dataset.loraId=lora.id;
      const isChosen=Array.isArray(chosen)?chosen.find(item=>item.id===lora.id):null;
      check.checked=isChosen?true:(!Array.isArray(chosen)&&!!lora.default_selected);
      const title=document.createElement('span');title.className='runpod-lora-name';title.textContent=lora.name+(lora.hazir?'':' · indirilecek');
      const weight=document.createElement('input');weight.type='number';weight.min='0';weight.max='2';weight.step='0.05';weight.value=Number(isChosen?.weight??lora.weight??1).toFixed(2);weight.dataset.loraWeight=lora.id;weight.setAttribute('aria-label',lora.name+' ağırlığı');
      check.addEventListener('change',()=>{runpodLoraSelections.set(model,runpodLoraOku());formKaydet();});
      weight.addEventListener('change',()=>{runpodLoraSelections.set(model,runpodLoraOku());formKaydet();});
      row.append(check,title,weight);root.append(row);
    }
    if(!loras.length){const note=document.createElement('span');note.className='kucuk';note.textContent='Bu model için LoRA tanımlanmamış.';root.append(note);}
  }catch(error){if(requestId!==runpodLoraRequestId)return;const note=document.createElement('span');note.className='kucuk';note.textContent='LoRA listesi alınamadı: '+error.message;root.append(note);}
}
function runpodModelSecili(){
  const secim = $('runpodImageModel');
  if(!RUNPOD_WEBUI || !secim || !secim.value || secim.value === 'auto') return null;
  const instance=runpodModelOptions.find(item=>item.id===secim.value);
  return instance?.family || secim.selectedOptions[0]?.dataset.family || secim.value;
}
function runpodModelInstanceSecili(){
  const secim=$('runpodImageModel');
  if(!RUNPOD_WEBUI||!secim||!secim.value||secim.value==='auto') return null;
  return secim.value;
}
function runpodVideoModelSecili(){
  return RUNPOD_WEBUI && $('videoModel') ? ($('videoModel').value || 'wan22') : 'video';
}
function runpodModelListesiGuncelle(data){
  if(!RUNPOD_WEBUI) return;
  const select=$('runpodImageModel');
  const allowed=new Set(['z_image','flux2','flux3','krea2','qwen_image_edit',
    'qwen_image_edit_turbo','illustrious']);
  const items=(data.model_instances||[]).filter(item=>item.engine==='comfy'&&allowed.has(item.family));
  if(!select||!items.length) return;
  const signature=JSON.stringify(items.map(item=>[item.id,item.name,item.ready]));
  if(signature===runpodModelSignature) return;
  runpodModelSignature=signature;
  const previous=select.value;
  runpodModelOptions=items;
  select.replaceChildren();
  for(const item of items){
    const option=document.createElement('option');
    option.value=item.id; option.dataset.family=item.family;
    option.textContent=item.name+(item.ready?'':' · kurulu değil');
    select.append(option);
  }
  select.value=items.some(item=>item.id===previous)?previous:
    (items.some(item=>item.id==='z_image')?'z_image':items[0].id);
  uretimModGuncelle();
}
function runpodLlmListesiGuncelle(data, preferred){
  if(!RUNPOD_WEBUI) return;
  const items=data.llm_models||[];
  if(!items.length) return;
  const signature=JSON.stringify(items.map(item=>[item.id,item.ad,item.hazir,
    item.gorsel_dosyasi,item.gorsel_hazir,item.mtp_dahili,item.mtp_dosyasi,
    item.tuning_family,item.tuning_defaults]));
  if(signature===runpodLlmSignature){
    if(preferred&&items.some(item=>item.id===preferred)&&$('llmModel'))
      $('llmModel').value=preferred;
    if(runpodLlmTuningReady) llmTuningSeciliModeliYukle();
    return;
  }
  runpodLlmSignature=signature;
  runpodLlmOptions=items;
  const defaultId=items.some(item=>item.id==='llm_26b')?'llm_26b':items[0].id;
  const selectors=['llmModel','sohbetLlmModel','psLlmModel','uretimLlmModel','llmTuneModel'];
  for(const id of selectors){
    const select=$(id); if(!select) continue;
    const previous=select.value;
    let stored=null;
    try{
      const key={sohbetLlmModel:'sohbet_llm_model',psLlmModel:'ps_llm_model',
        uretimLlmModel:'uretim_llm_model'}[id];
      if(key) stored=localStorage.getItem(key);
    }catch(_){}
    const saved=preferred&&id==='llmModel'?preferred:
      (id==='llmTuneModel'&&preferred?preferred:stored);
    select.replaceChildren();
    for(const item of items){
      const option=document.createElement('option'); option.value=item.id;
      const durum=item.gorsel_hazir?'':(item.gorsel_dosyasi?' · görsel eki bekliyor':' · metin modeli');
      option.textContent=item.ad+(item.hazir?'':' · kurulu değil')+durum;
      select.append(option);
    }
    const wanted=[saved,previous,defaultId].find(value=>items.some(item=>item.id===value));
    select.value=wanted||items[0].id;
  }
  if(runpodLlmTuningReady) llmTuningSeciliModeliYukle();
}
function llmTuningVarsayilan(item){
  return Object.assign({gpu_layers:'auto',context:item?.ctx||8192,threads:8,
    batch:2048,ubatch:512,flash_attn:'on',cache_type_k:'f16',cache_type_v:'f16',
    top_p:0.95,top_k:40,repeat_penalty:1.05,temperature:0.7,use_mtp:false,
    multimodal:!!item?.gorsel_dosyasi,reasoning:'on',reasoning_effort:'default',
    reasoning_budget:160,mtp_draft_tokens:3},item?.tuning_defaults||{});
}
function llmTuningFormOku(){
  const layers=$('llmGpuLayers').value;
  return {gpu_layers:['auto','all'].includes(layers)?layers:Number(layers),
    context:Number($('llmCtxModel').value),threads:Number($('llmThreads').value),
    batch:Number($('llmBatch').value),ubatch:Number($('llmUbatch').value),
    flash_attn:$('llmFlashAttention').value,cache_type_k:$('llmCacheK').value,
    cache_type_v:$('llmCacheV').value,top_p:Number($('llmTopP').value),
    top_k:Number($('llmTopK').value),repeat_penalty:Number($('llmRepeatPenalty').value),
    temperature:Number($('llmTemperature').value),use_mtp:$('llmUseMtp').checked,
    multimodal:$('llmMultimodal').checked,reasoning:$('llmReasoning').value,
    reasoning_effort:$('llmReasoningEffort').value,
    reasoning_budget:Number($('llmReasoningBudget').value),
    mtp_draft_tokens:Number($('llmMtpTokens').value)};
}
function llmTuningFormYaz(t){
  $('llmGpuLayers').value=String(t.gpu_layers??'auto');
  $('llmCtxModel').value=t.context??8192;$('llmThreads').value=t.threads??8;
  $('llmBatch').value=t.batch??2048;$('llmUbatch').value=t.ubatch??512;
  $('llmFlashAttention').value=t.flash_attn||'on';
  $('llmCacheK').value=t.cache_type_k||'f16';$('llmCacheV').value=t.cache_type_v||'f16';
  $('llmTopP').value=t.top_p??0.95;$('llmTopK').value=t.top_k??40;
  $('llmRepeatPenalty').value=t.repeat_penalty??1.05;
  $('llmTemperature').value=t.temperature??0.7;
  $('llmUseMtp').checked=!!t.use_mtp;$('llmMultimodal').checked=!!t.multimodal;
  $('llmReasoning').value=t.reasoning||'on';
  $('llmReasoningEffort').value=t.reasoning_effort||'default';
  $('llmReasoningBudget').value=t.reasoning_budget??160;
  $('llmMtpTokens').value=t.mtp_draft_tokens??3;
}
function llmTuningSeciliModeliKaydet(){
  const id=$('llmTuneModel')?.value;
  if(id&&runpodLlmOptions.some(item=>item.id===id))
    runpodLlmTuningDraft[id]=llmTuningFormOku();
}
function llmTuningSeciliModeliYukle(){
  if(!RUNPOD_WEBUI||!$('llmTuneModel')) return;
  const item=runpodLlmOptions.find(value=>value.id===$('llmTuneModel').value)||runpodLlmOptions[0];
  if(!item) return;
  $('llmTuneModel').value=item.id;
  const t=Object.assign(llmTuningVarsayilan(item),runpodLlmTuningDraft[item.id]||{});
  llmTuningFormYaz(t);
  const mtp=!!(item.mtp_dahili||item.mtp_dosyasi);
  $('llmUseMtp').disabled=!mtp;
  $('llmMtpTokens').disabled=!mtp;
  $('llmMtpInfo').textContent=item.mtp_dahili?'Bu modelin dosyasında yerleşik MTP var.':
    (item.mtp_dosyasi?'Ayrı MTP dosyası tanımlı.':
      'Bu modelde MTP desteği bulunamadı; ayar diğer modelleri etkilemez.');
}
function otomatikModel(){
  const runpodModel = runpodModelSecili();
  if(runpodModel) return runpodModel;
  if(refSayisi()) return 'flux2';
  if(animaSecili()) return $('moodyAnimaModel').checked ? 'moody_anima' : 'anima';
  return 'z_image';
}
function otomatikWorkflow(){ return videoMu() ? 'video' : (refSayisi() >= 2 ? 'cift' : 'tek'); }
let otomatikSonModel = null;
function uretimModGuncelle(){
  const runpodModel=runpodModelSecili();
  const model = otomatikModel();
  const modelDegisti = !!otomatikSonModel && otomatikSonModel !== model;
  if(modelDegisti){
    const varsayilanlar = RUNPOD_WEBUI ? RUNPOD_IMAGE_DEFAULTS : {
      flux2: {steps: 4, sampler: 'euler'},
      z_image: {steps: 8, sampler: 'res_multistep'},
      anima: {steps: 8, sampler: 'euler'},
      moody_anima: {steps: 4, sampler: 'euler'}
    };
    const eski = varsayilanlar[otomatikSonModel], yeni = varsayilanlar[model];
    if(eski && yeni && +$('steps').value === eski.steps) $('steps').value = yeni.steps;
    if(eski && yeni && $('sampler').value === eski.sampler) $('sampler').value = yeni.sampler;
    if(eski && yeni && eski.cfg != null && yeni.cfg != null && +$('cfg').value === eski.cfg) $('cfg').value = yeni.cfg;
  }
  otomatikSonModel = model;
  if(modelDegisti) runpodLoraYukle();
  const runpodModelWrap = $('runpodModelWrap');
  if(runpodModelWrap) runpodModelWrap.hidden = !RUNPOD_WEBUI || videoMu();
  const bboxWrap = $('flux3BboxModeWrap');
  if(bboxWrap) bboxWrap.hidden = !RUNPOD_WEBUI || model !== 'flux3';
  const turboQwen = RUNPOD_WEBUI && model === 'qwen_image_edit_turbo';
  $('steps').disabled = turboQwen;
  $('steps').title = turboQwen ? 'Turbo, yayımlanan sabit sigmalarla 8 adımda çalışır.' : '';
  $('cfg').disabled = turboQwen;
  $('cfg').title = turboQwen ? 'Turbo modelinin önerilen CFG değeri 1’dir.' : '';
  if($('runpodLoraPicker')) $('runpodLoraPicker').hidden = !RUNPOD_WEBUI || videoMu() ||
    model === 'qwen_image_edit' || model === 'qwen_image_edit_turbo';
  if(RUNPOD_WEBUI && $('runpodModelInfo')){
    const names = {z_image:'Moody Pro v13.2',flux2:'Flux 2 Klein',flux3:'Flux 3 Klein · 16 adım',
      krea2:'Moody Krea Mix v7SE',qwen_image_edit:'Qwen Image 2.1 · Düzenleme',
      qwen_image_edit_turbo:'Qwen Image 2.1 · Turbo düzenleme · 8 adım',illustrious:'Illustrious XL'};
    $('runpodModelInfo').textContent = names[model] + (refSayisi() && model === 'flux2'
      ? ' · referans düzenleme' : (model === 'qwen_image_edit' || model === 'qwen_image_edit_turbo'
        ? ' · en az bir referans fotoğraf gerekir.'
        : ' · model hazır değilse Kurulum sayfasından ekle.'));
  }
  if(RUNPOD_WEBUI && $('runpodImageModel')){
    for(const option of $('runpodImageModel').options)
      option.disabled = refSayisi() > 0 && (option.value === 'krea2' || option.value === 'illustrious');
  }
  $('animaModelSecim').hidden = RUNPOD_WEBUI || !!refSayisi() || videoMu();
  const bilgi = $('uretimModBilgi');
  if(bilgi){
    if(videoMu()) bilgi.textContent = RUNPOD_WEBUI
      ? (runpodVideoModelSecili() === 'wan22'
        ? 'Wan 2.2 · büyük 14B NSFW model · metinden videoya · kısa taslak ayarları.'
        : 'MiniMax H3 · referans fotoğraflı ve sesli video üretimi.')
      : 'Video iş akışı: MiniMax H3; model seçimi bu akışta etkisizdir.';
    else if(RUNPOD_WEBUI && runpodModel) bilgi.textContent =
      (runpodModel === 'flux2' || runpodModel === 'flux3' ? 'Referanslı düzenleme: ' : 'Seçili model: ') +
      ({z_image:'Z-Image · Moody Pro',flux2:'Flux 2 Klein',flux3:'Flux 3 Klein',krea2:'Krea 2',
        qwen_image_edit:'Qwen Image 2.1 · Düzenleme',
        qwen_image_edit_turbo:'Qwen Image 2.1 · Turbo düzenleme',illustrious:'Illustrious'}[model] || model) +
      (model === 'qwen_image_edit_turbo' ? ' · 8 adım · CFG 1' : ' · ' + $('steps').value + ' adım');
    else if(refSayisi() >= 2) bilgi.textContent = 'Otomatik: Flux2 Klein · çift referans düzenleme · ' + $('steps').value + ' adım';
    else if(refSayisi() === 1) bilgi.textContent = 'Otomatik: Flux2 Klein · düzenleme (1 referans) · ' + $('steps').value + ' adım';
    else if(model === 'anima') bilgi.textContent = 'Anima Turbo v1.1 · saf görsel üretimi · ' + $('steps').value + ' adım';
    else if(model === 'moody_anima') bilgi.textContent = 'Anima Moody Mix v1.0 · saf görsel üretimi · ' + $('steps').value + ' adım';
    else bilgi.textContent = 'Otomatik: Z-Image Turbo · sıfırdan üretim · ' + $('steps').value + ' adım';
  }
  if(modelDegisti) durum();
}
function uretimIcGorunum(){
  const vm = videoMu();
  const goster = (id, acik) => { const el = $(id); if(el) el.style.display = acik ? 'block' : 'none'; };
  goster('videoKart', vm);
  const wan = vm && RUNPOD_WEBUI && runpodVideoModelSecili() === 'wan22';
  goster('kartRef', !vm || !wan);
  if($('videoModelWrap')) $('videoModelWrap').hidden = !RUNPOD_WEBUI;
  if($('videoProfileBlok')) $('videoProfileBlok').hidden = !vm || (RUNPOD_WEBUI && wan);
  if($('videoKareNotu')) $('videoKareNotu').textContent = wan
    ? 'Wan 2.2 kare sayısını 4n+1 kuralına göre ayarlar; taslakta 4 örnekleme adımı kullanılır.'
    : 'Kare sayısı 17k+5 ızgarasına yuvarlanır · tek işçi sırayla çalışır.';
  if($('videoBilgi')) $('videoBilgi').textContent = wan
    ? 'Wan 2.2 14B: metinden sessiz kısa video üretir. Modeli önce Kurulum ekranından indirin.'
    : 'MiniMax H3: en az bir referans fotoğrafıyla sesli video üretir.';
  goster('kartCoz', !vm);
  goster('kartOrn', !vm);
  if($('uretimNegatifPanel')) $('uretimNegatifPanel').hidden = vm;
  if($('uretimPromptCeviri')) $('uretimPromptCeviri').disabled = vm;
}
function menuUygula(){
  const known = ['uretim','studyo','kuyruk','kutuphane','arsiv','promptlar','model','izleme','ayarlar','yonetim'];
  if(!known.includes(aktifMenu)) aktifMenu = 'uretim';
  const esnek = {kartModel: 1, wsUretim: 1, wsStudyo: 1};
  let shown = false;
  document.querySelectorAll('#uygulama > .kart').forEach(k => {
    const menu = k.getAttribute('data-menu') || 'uretim';
    const ok = menu === aktifMenu;
    k.style.display = ok ? (esnek[k.id] ? 'flex' : 'block') : 'none';
    if(ok) shown = true;
  });
  if(!shown){
    aktifMenu = 'uretim';
    document.querySelectorAll('#uygulama > .kart').forEach(k => {
      const menu = k.getAttribute('data-menu') || 'uretim';
      k.style.display = menu === 'uretim' ? (esnek[k.id] ? 'flex' : 'block') : 'none';
    });
  }
  if(aktifMenu === 'uretim') uretimIcGorunum();
  document.querySelectorAll('#menuNav button').forEach(b =>
    b.classList.toggle('aktif', b.getAttribute('data-menu') === aktifMenu));
  const alt = document.querySelector('.altbar');
  if(alt) alt.style.display = 'none';
  document.body.classList.toggle('chat-active', aktifMenu === 'model');
  document.body.classList.toggle('tam-calisma', ['uretim','studyo','model'].includes(aktifMenu));
  if(aktifMenu !== 'model'){ try{ sohbetMenyuAyarla(false); }catch(e){} }
  sinemaRenkTemasiGuncelle();
}
function kullaniciAyarlariniVarsayilanaAl(){
  const degerler = {
    prompt: '', negatif: '', w: 1024, h: 1024, maxside: 1536,
    maxolcek: true, vmax: 1344, vsure: 5, vfps: '24', videoProfile: 'quality', steps: 9,
    cfg: 1, sampler: 'dpmpp_2m_sde', seed: -1, wf: 'otomatik',
    preset: '1024x1024', animaModel: false, moodyAnimaModel: false,
    uretimPromptDestek: false, uretimPromptCeviri: false,
    limitInput: 0, beklemeInput: 15, llmCtx: 8000,
    promptDestekYaraticilik: 0.7, promptDestekUzunluk: 'orta',
    uretimVarsayilanSteps: 8, uretimVarsayilanSeed: -1,
    uretimVarsayilanMaxSide: 1536, llmStreaming: true,
    llmLowBandwidth: false, llmSistem: '', llmSistemSohbet: '',
    llmModFikir: '', llmModGorsel: '', llmModBirlestir: '',
    llmModCeviri: '', llmModSohbet: '', psYaraticilik: 0.7,
    psDil: 'en', logSatir: 200, logKaynak: 'llama',
    psGecmisArama: '', gecArama: '',
    historyAlbums: false, continuousGeneration: false, cinematicMode: 'off',
    autoOpenResult: true
  };
  Object.keys(degerler).forEach(id => {
    const el = $(id);
    if(!el) return;
    if(el.type === 'checkbox') el.checked = degerler[id];
    else el.value = degerler[id];
  });
  promptDestekYaraticilik = 0.7;
  promptDestekUzunluk = 'orta';
  uretimKesintisizAktif = false; uretimKesintisizIstek = null;
  uretimVarsayilan = {steps: 9, seed: -1, max_side: 1536};
  kilitOran = 1;
  sohbetBaglamBoyutu = 8000;
  sohbetBaglamGuncelle(null);
  document.body.classList.remove('low-bandwidth');
  $('logCikti').textContent = '';
  sinemaTemasiniSifirla();
  $('logSatirSayisi').textContent = '';
  $('beklemeBilgi').textContent = '';
  $('llmCtxBilgi').textContent = '';
  $('promptDestekBilgi').textContent = '';
  $('uretimVarsayilanBilgi').textContent = '';
  $('uretimPromptCeviriBilgi').textContent = '';
  $('llmSistemBilgi').textContent = '';
  $('psPozitif').value = ''; $('psPozitifTr').value = '';
  $('psNegatif').value = ''; $('psAciklama').textContent = '';
  $('psEtiketler').textContent = ''; $('psBilgi').textContent = '';
  $('psPromptProfile').value = 'auto'; $('psFlux3BboxMode').checked = false;
  $('psPromptProfile').disabled = false; $('psFlux3BboxMode').disabled = false;
  $('psNegatifDetay').open = false;
  psTemizle(1); psTemizle(2); psModGuncelle();
  if($('llmLowBandwidth').onchange) $('llmLowBandwidth').onchange();
  cozunurlukGorunumu(); oranKilitGuncelle(); uretimIcGorunum(); uretimModGuncelle();
}
document.querySelectorAll('#menuNav button').forEach(b => b.onclick = () => {
  aktifMenu = b.getAttribute('data-menu'); menuUygula(); window.scrollTo({top: 0});
  try{ $('menuNav').classList.remove('open'); }catch(e){}
  try{
    if(aktifMenu === 'studyo'){ psGecmisYukle(); psModelDurumuYukle(); }
    if(aktifMenu === 'kutuphane'){ kutuphaneYukle(); }
    if(aktifMenu === 'arsiv'){ arsivYukle(true); }
    if(aktifMenu === 'model'){ sohbetKayitYukle(); }
    if(aktifMenu === 'izleme'){ izlemeYukle(); logYukle(); }
    if(aktifMenu === 'ayarlar'){ llmSistemYukle(); }
  }catch(e){}
});
function durumuSifirla(){
  /* Hesap degisiminde onceki hesabin prompt/sonuc/kutuphane/gecmis gorunumu kalmasin. */
  oturumNesli++;
  runpodLoraCurrentModel = null; runpodLoraSaved = null; runpodLoraSelections.clear();
  try{ if(progTimer){ clearInterval(progTimer); progTimer = null; } }catch(e){}
  try{ if(kuyrukTimer){ clearInterval(kuyrukTimer); kuyrukTimer = null; } }catch(e){}
  try{ if(formKayitSayac){ clearTimeout(formKayitSayac); formKayitSayac = null; } }catch(e){}
  try{ if(uiAyarZamanlayici){ clearTimeout(uiAyarZamanlayici); uiAyarZamanlayici = null; } }catch(e){}
  uiAyarDurum = {}; uiAyarYamasi = {}; uiAyarKayitBekliyor = false;
  try{ Object.values(llmIsTakipleri).forEach(t => clearInterval(t.timer)); Object.keys(llmIsTakipleri).forEach(k => delete llmIsTakipleri[k]); }catch(e){}
  try{ psSuruyor = false; psAktifIs = null; sohbetSuruyor = false; sohbetAktifIs = null; }catch(e){}
  takipIs = null;
  fotoB64 = null; fotoB64_2 = null;
  fotoMime = null; fotoMime2 = null;
  try{ $('foto').value = ''; $('foto2').value = ''; }catch(e){}
  try{ fotoTemizle(1); fotoTemizle(2); }catch(e){}
  try{ psTemizle(1); psTemizle(2); psSonuc = null; }catch(e){}
  try{
    $('prompt').value = ''; $('negatif').value = '';
    $('sohbetGirdi').value = ''; $('psMetin').value = '';
  }catch(e){}
  kullaniciAyarlariniVarsayilanaAl();
  uiAyarlariniUygula({});
  sohbetYeni();
  sohbetKayitlari = [];
  $('sohbetArama').value = '';
  $('sohbetKayitListe').replaceChildren();
  ['psPozitif','psPozitifTr','psNegatif'].forEach(id => {
    const el = $(id); if(el) el.value = '';
  });
  ['psAciklama','psEtiketler','psDurum','psBilgi'].forEach(id => {
    const el = $(id); if(el) el.textContent = '';
  });
  ['psUygula','psKopyala','psKaydet','psHizliUret'].forEach(id => {
    const el = $(id); if(el) el.disabled = true;
  });
  try{ $('galeri').replaceChildren(); }catch(e){}
  try{
    $('uretimSeciliBilgi').replaceChildren();
    $('uretimSeciliBilgi').hidden = true;
    $('uretimGoruntuStage').hidden = true;
    $('uretimGoruntuEtiket').hidden = true;
    $('uretimGoruntuStage').classList.remove('tam-ekran');
    $('uretimGoruntuBos').hidden = false;
    document.body.classList.remove('uretim-tam-ekran');
  }catch(e){}
  try{ $('ilerleme').textContent = ''; }catch(e){}
  try{ $('psCanli').textContent = ''; document.getElementById('sohbetCanli')?.replaceChildren(); }catch(e){}
  try{ $('psGecmisListe').innerHTML = '<div class="kucuk">Henüz kayıt yok.</div>'; }catch(e){}
  try{ ilerlemeDurdur(); }catch(e){}
  gecSayfa = 1;
  try{ $('gecIzgara').innerHTML = ''; }catch(e){}
  try{ $('libIzgara').innerHTML = ''; $('libBos').hidden = true; }catch(e){}
  try{ kutuphaneKayitlari = []; $('libSayac').textContent = ''; $('libHata').textContent = ''; }catch(e){}
  try{ $('prListe').innerHTML = ''; }catch(e){}
  try{ $('adminListe').innerHTML = ''; }catch(e){}
  try{ $('kuyrukListe').innerHTML = '<div class="kucuk">Kuyruk boş.</div>'; $('kuyrukSayi').textContent = ''; }catch(e){}
  gecListe = []; gecToplam = 0; gecIdx = 0;
}
const $ = id => document.getElementById(id);
function parolaGozuAyarla(btn, acik){
  const giris = $(btn.dataset.hedef);
  if(!giris) return;
  giris.type = acik ? 'text' : 'password';
  btn.textContent = acik ? 'Gizle' : 'Göster';
  btn.setAttribute('aria-label', acik ? 'Şifreyi gizle' : 'Şifreyi göster');
  btn.setAttribute('aria-pressed', acik ? 'true' : 'false');
}
function parolaGozleri(){
  document.querySelectorAll('.parolaGoz').forEach(btn => {
    btn.onclick = () => parolaGozuAyarla(btn, btn.getAttribute('aria-pressed') !== 'true');
  });
}
function parolaGozleriniKapat(){
  document.querySelectorAll('.parolaGoz').forEach(btn => parolaGozuAyarla(btn, false));
}

/* Kimlik: giris/kurulum/oturum yonetimi. Oturum duserse (401) girise donulur.
   son_is baskasina aitse detay 404 doner, sonIsiBaglan sessizce yok sayar. */
let BEN = null;
let oturumNesli = 0;
const _fetch = window.fetch.bind(window);
window.fetch = async (...a) => {
  const istekNesli = oturumNesli;
  const r = await _fetch(...a);
  if(istekNesli !== oturumNesli)
    return new Response('{"hata":"oturum degisti"}',
      {status: 409, headers: {'Content-Type': 'application/json'}});
  if (r.status === 401 && BEN){
    girisEkraniAc();
    window.location.replace('/');
  }
  else if (r.status === 423 && BEN) kasaEkraniAc();
  return r;
};
window.addEventListener('storage', e => {
  if(e.key !== null || !BEN) return;
  BEN = null;
  durumuSifirla();
  window.location.replace('/');
});
function istemciOturumVerisiniTemizle(){
  try{ localStorage.clear(); }catch(e){}
  try{ sessionStorage.clear(); }catch(e){}
  try{
    document.cookie.split(';').forEach(cerez => {
      const ad = cerez.split('=')[0].trim();
      if(ad) document.cookie = ad + '=; Max-Age=0; path=/; SameSite=Lax';
    });
  }catch(e){}
}
function gorunum(kim){
  $('baslangicEkrani').style.display = 'none';
  document.querySelector('header.ust').style.display = kim === 'uygulama' ? '' : 'none';
  $('girisAlani').style.display = (kim === 'kurulum' || kim === 'giris') ? 'block' : 'none';
  $('kurulumKart').style.display = kim === 'kurulum' ? 'block' : 'none';
  $('girisKart').style.display = kim === 'giris' ? 'block' : 'none';
  $('kasaAlani').style.display = kim === 'kasa' ? 'block' : 'none';
  $('uygulama').style.display = kim === 'uygulama' ? 'flex' : 'none';
  document.body.classList.toggle('uygulama-acik', kim === 'uygulama');
  document.querySelector('.altbar').style.display = kim === 'uygulama' ? '' : 'none';
  $('kasaRozet').style.display = (kim === 'kasa' || kim === 'uygulama') ? '' : 'none';
  $('menuNav').style.display = kim === 'uygulama' ? '' : 'none';
  $('menuBtn').style.display = kim === 'uygulama' ? '' : 'none';
}
function girisEkraniAc(){
  BEN = null;
  istemciOturumVerisiniTemizle();
  parolaGozleriniKapat();
  kasaSaatiDurdur();
  durumuSifirla();
  try{ localStorage.removeItem('son_is'); }catch(e){}
  $('kullaniciRozet').style.display = 'none'; $('cikisBtn').style.display = 'none';
  $('kasaRozet').style.display = 'none';
  gorunum('giris');
}
function uygulamaGoster(b){
  BEN = b;
  durumuSifirla();
  $('kullaniciRozet').textContent = b.kullanici + (b.rol === 'admin' ? ' (admin)' : '');
  $('kullaniciRozet').style.display = '';
  $('cikisBtn').style.display = '';
  if (b.rol === 'admin'){ $('menuYonetim').style.display = ''; adminYukle(); }
  else { $('menuYonetim').style.display = 'none'; }
  kasaKontrol();
}
async function kasaKontrol(){
  /* Giris tamam, kasa aciksa verileri yukle, degilse kilit ekrani. */
  let kd = null;
  try{ kd = await (await _fetch('/api/kasa/durum')).json(); }catch(e){}
  if(kd && kd.acik) veriYukle(kd);
  else kasaEkraniAc(kd);
}
async function veriYukle(){
  try{
    gorunum('uygulama');
    cozunurlukGorunumu(); oranKilitGuncelle(); pinEtiket(); gecGorunumUygula();
    await durum();
    kutuphaneYukle(); promptListele(); gecSayfa = 1; gecmisYukle(true); ayarYukle(); kuyrukYenile(); arsivYukle(true);
    try{ llmSistemYukle(); }catch(e){}
    aktifMenu = 'uretim';
    menuUygula(); llmIsleriniBagla(); sonIsiBaglan(); kasaSaatiBaslat();
    try{ if(aktifMenu === 'studyo'){ psGecmisYukle(); psModelDurumuYukle(); } }catch(e){}
  }finally{
    /* Asla boş ekran kalmasın: en az bir kart görünür olsun. */
    try{
      const anyVisible = Array.from(document.querySelectorAll('#uygulama > .kart')).some(k => k.style.display !== 'none');
      if(!anyVisible){ aktifMenu = 'uretim'; menuUygula(); }
    }catch(e){}
  }
  /* DOM tamamen yerleştikten sonra bir kez daha emin ol. */
  setTimeout(()=>{
    try{
      const anyVisible = Array.from(document.querySelectorAll('#uygulama > .kart')).some(k => k.style.display !== 'none');
      if(!anyVisible){ aktifMenu = 'uretim'; menuUygula(); window.scrollTo({top:0}); }
    }catch(e){}
  }, 200);
}
let bootCalisiyor = false;
function bootMesaji(metin, tekrarGoster=false){
  $('baslangicMesaj').textContent = metin;
  $('baslangicYenidenDene').style.display = tekrarGoster ? '' : 'none';
}
async function boot(){
  if(bootCalisiyor) return;
  bootCalisiyor = true;
  $('baslangicEkrani').style.display = 'grid';
  $('baslangicYenidenDene').disabled = true;
  for(let deneme = 0; deneme < 8; deneme++){
    try{
      bootMesaji('Sunucuya bağlanılıyor…');
      const kurulumYaniti = await _fetch('/api/kurulum-durumu');
      if(!kurulumYaniti.ok) throw new Error('Kurulum durumu alınamadı (HTTP ' + kurulumYaniti.status + ').');
      const k = await kurulumYaniti.json();
      if(!k){ girisEkraniAc(); bootCalisiyor = false; return; }
      RUNPOD_WEBUI = k.runpod === true;
      const storageNote=$('firstRunStorageNote');
      if(storageNote){
        const ephemeral=RUNPOD_WEBUI&&k.storage_mode==='ephemeral';
        storageNote.hidden=!ephemeral;
        storageNote.textContent=ephemeral
          ?'Kalıcı volume bağlı değil. Sistem çalışır; ancak Pod durdurulursa, yeniden başlatılırsa veya silinirse kullanıcı verileri ve ayarlar kaybolur. Kalıcılık için küçük bir volume bağlayın.'
          :'';
      }
      for(const id of ['sohbetLlmModel','psLlmModel','uretimLlmModel']){
        const field=$(id);
        const wrapper=id==='sohbetLlmModel'||id==='uretimLlmModel'?
          field?.closest('label'):field?.parentElement;
        if(wrapper) wrapper.hidden=!RUNPOD_WEBUI;
      }
      if($('runpodInstallerLink')){
        $('runpodInstallerLink').hidden=!RUNPOD_WEBUI;
        if(RUNPOD_WEBUI) $('runpodInstallerLink').href=runpodKurulumAdres();
      }
      $('videoProfileBlok').hidden = !RUNPOD_WEBUI;
      if($('videoModelWrap')) $('videoModelWrap').hidden = !RUNPOD_WEBUI;
      if($('videoModel') && RUNPOD_WEBUI) $('videoModel').value = 'wan22';
      if(RUNPOD_WEBUI){
        $('vsure').value = '0.5'; $('vfps').value = '8'; $('vmax').value = '320';
        $('videoSureLabel').textContent = 'Taslak klip süresi (sn)';
        $('videoBilgi').textContent = 'Wan 2.2 metinden sessiz kısa video üretir; MiniMax H3 referans fotoğrafından sesli video üretir.';
      }
      if(!k.kurulu){ gorunum('kurulum'); bootCalisiyor = false; return; }
      const ben = await _fetch('/api/ben');
      if(ben.status === 401){ girisEkraniAc(); bootCalisiyor = false; return; }
      if(!ben.ok) throw new Error('Oturum durumu alınamadı (HTTP ' + ben.status + ').');
      const b = await ben.json();
      if(!b) throw new Error('Sunucudan kullanıcı bilgisi alınamadı.');
      uygulamaGoster(b);
      bootCalisiyor = false;
      return;
    }catch(e){
      const sonDeneme = deneme === 7;
      bootMesaji(sonDeneme
        ? 'WebUI sunucusuna ulaşılamıyor. ' + (e.message || e)
        : 'WebUI hazırlanıyor; bağlantı yeniden denenecek…',
        sonDeneme);
      if(!sonDeneme) await new Promise(resolve => setTimeout(resolve, Math.min(500 * (2 ** deneme), 5000)));
    }
  }
  bootCalisiyor = false;
  $('baslangicYenidenDene').disabled = false;
}
async function girisYap(){
  $('girHata').textContent = 'Giriş yapılıyor...';
  try{
    const r = await _fetch('/api/giris', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kullanici: $('girAd').value.trim(), sifre: $('girSifre').value})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok){
      $('girHata').textContent = 'Kullanıcı adı veya şifre hatalı.';
      return;
    }
    $('girSifre').value = '';
    uygulamaGoster(j);
  }catch(e){ $('girHata').textContent = 'HATA: ' + e; }
}
async function kurulumYap(){
  $('kurHata').textContent = 'Kuruluyor...';
  try{
    const r = await _fetch('/api/kurulum', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kullanici: $('kurAd').value.trim(), sifre: $('kurSifre').value})});
    const j = await r.json().catch(() => ({}));
    if (!r.ok){ $('kurHata').textContent = 'HATA: ' + (j.hata || r.status); return; }
    await girisYapDogrudan($('kurAd').value.trim(), $('kurSifre').value);
    $('kurSifre').value = '';
  }catch(e){ $('kurHata').textContent = 'HATA: ' + e; }
}
async function girisYapDogrudan(kullanici, sifre){
  const r = await _fetch('/api/giris', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({kullanici, sifre})});
  if (r.ok) uygulamaGoster(await r.json());
  else girisEkraniAc();
}
$('girBtn').onclick = girisYap;
$('kurBtn').onclick = kurulumYap;
$('girSifre').addEventListener('keydown', e => { if (e.key === 'Enter') girisYap(); });
$('kurSifre').addEventListener('keydown', e => { if (e.key === 'Enter') kurulumYap(); });

/* Kasa: kilit ekrani, acma/kurma/kurtarma, sure sayaci, nabiz. */
let kasaTimer = null, sonNabiz = 0, bilgiTimer = null;
function bilgi(metin){
  const el = $('bildirim');
  if(!el) return;
  el.textContent = metin;
  el.classList.add('goster');
  if(bilgiTimer) clearTimeout(bilgiTimer);
  bilgiTimer = setTimeout(() => el.classList.remove('goster'), 3200);
}
async function kasaEkraniAc(kd){
  kasaSaatiDurdur();
  durumuSifirla();
  try{ localStorage.removeItem('son_is'); }catch(e){}
  if(!kd){ try{ kd = await (await _fetch('/api/kasa/durum')).json(); }catch(e){ kd = null; } }
  const varMi = !!(kd && kd.kasa_var), kurt = !!(kd && kd.kurtarma);
  $('kasaAcKart').style.display = (varMi && !kurt) ? 'block' : 'none';
  $('kasaKurKart').style.display = (!varMi && !kurt) ? 'block' : 'none';
  $('kasaKurtarKart').style.display = kurt ? 'block' : 'none';
  if(!kd) $('kasaHata').textContent = 'Sunucuya erişilemiyor.';
  gorunum('kasa');
  kasaRozetGuncelle(kd);
}
let rozetAnlik = null;
function kasaRozetGuncelle(kd){
  rozetAnlik = kd ? {acik: !!kd.acik, kapaniyor: !!kd.kapaniyor,
    kalan: kd.kalan_sn || 0, alindi: Date.now()} : null;
  rozetCiz();
}
function rozetCiz(){
  /* Sunucu 15 sn'de bir soyler, aradaki saniyeleri ekranda biz sayariz. */
  const el = $('kasaRozet');
  if(!el) return;
  if(!rozetAnlik){ el.textContent = ''; el.setAttribute('data-durum', 'yok'); return; }
  if(!rozetAnlik.acik){
    el.textContent = 'kasa: kilitli'; el.setAttribute('data-durum', 'kilitli'); return;
  }
  const gecen = Math.floor((Date.now() - rozetAnlik.alindi) / 1000);
  const s = Math.max(0, rozetAnlik.kalan - gecen);
  const dk = Math.floor(s / 60), sn = s % 60;
  el.textContent = rozetAnlik.kapaniyor ? 'kasa: kapanıyor' :
    'kasa: açık · ' + dk + ' dk ' + (sn < 10 ? '0' : '') + sn + ' sn';
  el.setAttribute('data-durum', rozetAnlik.kapaniyor ? 'kapaniyor' : 'acik');
}
setInterval(rozetCiz, 1000);
function kasaSaatiBaslat(){ kasaSaatiDurdur(); kasaSaati(); kasaTimer = setInterval(kasaSaati, 15000); }
function kasaSaatiDurdur(){ if(kasaTimer){ clearInterval(kasaTimer); kasaTimer = null; } }
async function kasaSaati(){
  if(!BEN) return;
  let kd = null;
  try{ kd = await (await _fetch('/api/kasa/durum')).json(); }catch(e){ return; }
  kasaRozetGuncelle(kd);
  if(!kd.acik){
    /* Kilit ekranindaysak bir sey silme, sadece yaziyi tazele. */
    if($('kasaAlani').style.display !== 'block') kasaEkraniAc(kd);
    return;
  }
  if(kd.kapaniyor && $('uygulama').style.display !== 'none'){
    try{ $('ilerleme').textContent = 'Kasa kapanıyor: yeni iş alınmıyor, süren iş bitiriliyor...'; }catch(e){}
  }
}
async function uiAyarBekleyenleriKaydet(){
  if(uiAyarZamanlayici){ clearTimeout(uiAyarZamanlayici); uiAyarZamanlayici = null; }
  while(BEN && (uiAyarKayitBekliyor || uiAyarKaydediyor)){
    if(!uiAyarKaydediyor && uiAyarKayitBekliyor)
      await uiAyarKaydetSimdi();
    else
      await new Promise(resolve => setTimeout(resolve, 20));
  }
}
function nabiz(){
  if(!BEN) return;
  const simdi = Date.now();
  if(simdi - sonNabiz < 60000) return;
  sonNabiz = simdi;
  _fetch('/api/kasa/nabiz', {method: 'POST'}).catch(() => {});
}
['mousemove', 'keydown', 'click', 'touchstart'].forEach(t =>
  document.addEventListener(t, nabiz, {passive: true}));
async function kasaAc(){
  const dugme = $('kasaAcBtn');
  if(dugme.disabled) return;
  dugme.disabled = true;
  $('kasaHata').textContent = 'Açılıyor...';
  try{
    const r = await _fetch('/api/kasa/ac', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({parola: $('kasaParola').value})});
    const j = await r.json().catch(() => ({}));
    $('kasaParola').value = '';
    if(!r.ok){ $('kasaHata').textContent = 'HATA: ' + (j.hata || r.status); return; }
    veriYukle(); bilgi('Kasa açıldı');
  }catch(e){ $('kasaHata').textContent = 'HATA: ' + e; }
  finally{ dugme.disabled = false; }
}
async function kasaKur(){
  const dugme = $('kasaKurBtn');
  if(dugme.disabled) return;
  dugme.disabled = true;
  $('kasaKurHata').textContent = 'Kuruluyor...';
  try{
    const r = await _fetch('/api/kasa/olustur', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({parola: $('kasaKur1').value, parola2: $('kasaKur2').value,
        riski_anladim: $('kasaRisk').checked})});
    const j = await r.json().catch(() => ({}));
    $('kasaKur1').value = ''; $('kasaKur2').value = ''; $('kasaRisk').checked = false;
    if(!r.ok){ $('kasaKurHata').textContent = 'HATA: ' + (j.hata || r.status); return; }
    veriYukle(); bilgi('Kasa kuruldu ve açıldı');
  }catch(e){ $('kasaKurHata').textContent = 'HATA: ' + e; }
  finally{ dugme.disabled = false; }
}
async function kasaKurtar(){
  const dugme = $('kasaKurtarBtn');
  if(dugme.disabled) return;
  dugme.disabled = true;
  $('kurtarHata').textContent = 'Kurtarılıyor...';
  try{
    const r = await _fetch('/api/kasa/kurtar', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({parola: $('kasaKurtarParola').value})});
    const j = await r.json().catch(() => ({}));
    $('kasaKurtarParola').value = '';
    if(!r.ok){ $('kurtarHata').textContent = 'HATA: ' + (j.hata || r.status); return; }
    veriYukle(); bilgi('Kurtarıldı, kasa açıldı');
  }catch(e){ $('kurtarHata').textContent = 'HATA: ' + e; }
  finally{ dugme.disabled = false; }
}
$('kasaAcBtn').onclick = kasaAc;
$('menuBtn').onclick = () => {
  try{ $('menuNav').classList.toggle('open'); }catch(e){}
};
$('kasaKurBtn').onclick = kasaKur;
$('kasaKurtarBtn').onclick = kasaKurtar;
$('kasaParola').addEventListener('keydown', e => { if (e.key === 'Enter') kasaAc(); });
$('kasaKurtarParola').addEventListener('keydown', e => { if (e.key === 'Enter') kasaKurtar(); });
$('beklemeKaydet').onclick = async () => {
  const el = $('beklemeBilgi');
  el.textContent = 'Gönderiliyor...';
  try{
    const r = await _fetch('/api/kasa/sure', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({dakika: +$('beklemeInput').value})});
    const j = await r.json().catch(() => ({}));
    el.textContent = r.ok ? 'Kaydedildi: ' + j.dakika + ' dk.' : 'HATA: ' + (j.hata || r.status);
    if(r.ok) kasaSaati();
  }catch(e){ el.textContent = 'HATA: ' + e; }
};
ayariDegisinceKaydet(['beklemeInput'], 'beklemeKaydet');
$('cikisBtn').onclick = async () => {
  const dugme = $('cikisBtn');
  if(dugme.disabled) return;
  const etiket = dugme.textContent;
  dugme.disabled = true;
  dugme.textContent = 'Çıkılıyor…';
  try{
    await ayarDegisiklikleriniKaydet();
    await formSimdiKaydet();
    await uiAyarBekleyenleriKaydet();
    await promptSecenekKayitKuyrugu;
    await ayarlarYazmaKuyrugu;
    const kapatma = await _fetch('/api/kasa/kapat', {method: 'POST'});
    const kapatmaSonuc = await kapatma.json().catch(() => ({}));
    if(!kapatma.ok) throw new Error(kapatmaSonuc.hata || 'Kasa kapanışı başlatılamadı.');
    const cikis = await _fetch('/api/cikis', {method: 'POST'});
    const cikisSonuc = await cikis.json().catch(() => ({}));
    if(!cikis.ok) throw new Error(cikisSonuc.hata || 'Oturum kapatılamadı.');
  }catch(e){
    bilgi('Çıkış yapılamadı: ' + (e.message || e));
    return;
  }finally{
    dugme.disabled = false;
    dugme.textContent = etiket;
  }
  girisEkraniAc();
  window.location.replace('/');
};
$('sfrBtn').onclick = async () => {
  $('sfrBilgi').textContent = 'Gönderiliyor...';
  try{
    const r = await _fetch('/api/sifre', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({eski: $('sfrEski').value, yeni: $('sfrYeni').value})});
    const j = await r.json().catch(() => ({}));
    $('sfrBilgi').textContent = r.ok ? 'Şifre değişti.' : 'HATA: ' + (j.hata || r.status);
    if (r.ok){ $('sfrEski').value = ''; $('sfrYeni').value = ''; }
  }catch(e){ $('sfrBilgi').textContent = 'HATA: ' + e; }
};
async function kasaParolasiDegistir(){
  const button = $('kasaParolaDegistir');
  const info = $('kasaParolaBilgi');
  if(button.disabled) return;
  const mevcut = $('kasaEskiParola').value;
  const yeni = $('kasaYeniParola').value;
  const tekrar = $('kasaYeniParolaTekrar').value;
  if(!mevcut || !yeni || !tekrar){
    info.textContent = 'Üç parola alanını da doldur.';
    return;
  }
  button.disabled = true;
  info.textContent = 'Kasa güvenli biçimde yeniden şifreleniyor...';
  try{
    const r = await _fetch('/api/kasa/parola', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mevcut, yeni, tekrar})});
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    info.textContent = 'Kasa parolası değişti. Yeni parola sonraki açılışlarda kullanılacak.';
    bilgi('Kasa parolası değiştirildi.');
  }catch(e){
    info.textContent = 'Değiştirilemedi: ' + (e.message || e);
  }finally{
    $('kasaEskiParola').value = '';
    $('kasaYeniParola').value = '';
    $('kasaYeniParolaTekrar').value = '';
    parolaGozleriniKapat();
    button.disabled = false;
  }
}
$('kasaParolaDegistir').onclick = kasaParolasiDegistir;
['kasaEskiParola','kasaYeniParola','kasaYeniParolaTekrar'].forEach(id =>
  $(id).addEventListener('keydown', e => { if(e.key === 'Enter') kasaParolasiDegistir(); }));
async function adminYukle(){
  try{
    const r = await _fetch('/api/kullanicilar');
    if (!r.ok) return;
    const j = await r.json();
    const div = $('adminListe'); div.innerHTML = '';
    (j.kullanicilar || []).forEach(k => {
      const sat = document.createElement('div'); sat.className = 'listeogesi';
      const b = document.createElement('div'); b.className = 'bilgi';
      const ad = document.createElement('b'); ad.textContent = k.kullanici + (k.rol === 'admin' ? ' (admin)' : '');
      const alt = document.createElement('span'); alt.className = 'kucuk';
      alt.textContent = 'kota ' + k.kota_mb + ' MB / ' + k.kota_kayit + ' kayıt · kullanım ' +
        k.kullanim_mb + ' MB / ' + k.kayit_sayisi + ' kayıt';
      b.appendChild(ad); b.appendChild(alt);
      const kmb = document.createElement('input'); kmb.type = 'number'; kmb.min = '0';
      kmb.value = k.kota_mb; kmb.style.maxWidth = '90px'; kmb.title = 'kota MB';
      const kkt = document.createElement('input'); kkt.type = 'number'; kkt.min = '0';
      kkt.value = k.kota_kayit; kkt.style.maxWidth = '90px'; kkt.title = 'kota kayıt';
      const uyg = document.createElement('button'); uyg.className = 'kucukbtn'; uyg.textContent = 'Kota';
      uyg.onclick = async () => {
        await _fetch('/api/kullanici/kota', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({kullanici: k.kullanici, kota_mb: +kmb.value, kota_kayit: +kkt.value})});
        adminYukle();
      };
      const sil = document.createElement('button'); sil.className = 'kucukbtn tehlike'; sil.textContent = 'Sil';
      sil.onclick = async () => {
        if (!confirm('Silinsin mi? ' + k.kullanici + ' (verileriyle birlikte)')) return;
        const rr = await _fetch('/api/kullanici/sil', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({kullanici: k.kullanici})});
        if (!rr.ok){ const jj = await rr.json().catch(() => ({})); alert('HATA: ' + (jj.hata || rr.status)); }
        adminYukle();
      };
      sat.appendChild(b); sat.appendChild(kmb); sat.appendChild(kkt); sat.appendChild(uyg); sat.appendChild(sil);
      div.appendChild(sat);
    });
  }catch(e){}
}
$('admEkle').onclick = async () => {
  $('admBilgi').textContent = 'Ekleniyor...';
  try{
    const r = await _fetch('/api/kullanici/ekle', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kullanici: $('admAd').value.trim(), sifre: $('admSifre').value,
        rol: $('admRol').value, kota_mb: +$('admKotaMb').value, kota_kayit: +$('admKotaKayit').value})});
    const j = await r.json().catch(() => ({}));
    $('admBilgi').textContent = r.ok ? 'Eklendi: ' + j.kullanici : 'HATA: ' + (j.hata || r.status);
    if (r.ok){ $('admAd').value = ''; $('admSifre').value = ''; adminYukle(); }
  }catch(e){ $('admBilgi').textContent = 'HATA: ' + e; }
};

function kaplaAc(html){ $('kaplaIcerik').innerHTML = html; $('kapla').classList.add('acik'); document.body.classList.add('kapla-kitli'); }
$('kaplaKapat').onclick = () => $('kapla').classList.remove('acik');
$('kapla').addEventListener('click', e => { if(e.target === $('kapla')) $('kapla').classList.remove('acik'); });
/* Modal acilip kapandiginda govde kilidi senkron kalsin (tum kapatma
   noktalari classList.remove('acik') kullandigi icin observer yeterli). */
if(window.MutationObserver){
  new MutationObserver(() => {
    const acik = $('kapla').classList.contains('acik');
    document.body.classList.toggle('kapla-kitli', acik);
  }).observe($('kapla'), {attributes: true, attributeFilter: ['class']});
}

const wfEl = $('wf');
if(wfEl) wfEl.onchange = e => {
  const video = $('wf').value === 'video';
  $('uretimGorselMod').classList.toggle('aktif', !video);
  $('uretimVideoMod').classList.toggle('aktif', video);
  $('wfAciklama').textContent = video ?
    (RUNPOD_WEBUI ? 'Video modelini aşağıdan seç. Wan 2.2 metinden, MiniMax H3 referans fotoğrafından üretir.' : 'Referans fotoğraftan sesli video üretir (en az 1 referans). Ölçü en uzun kenara göre alınır.') :
    (RUNPOD_WEBUI ? 'Modeli aşağıdan seçebilirsin. Qwen Image 2.1 ve Flux 2 referanslı düzenlemeyi destekler.' :
     'Model otomatik seçilir: referans varsa Flux2 Klein düzenleme; yoksa Z-Image Turbo veya Anima modeli.');
  uretimIcGorunum();
  uretimModGuncelle();
  formKaydet();
};
if($('videoModel')) $('videoModel').addEventListener('change', () => {
  uretimIcGorunum(); uretimModGuncelle(); formKaydet();
});
if($('runpodImageModel')) $('runpodImageModel').addEventListener('change', () => {
  const previous = RUNPOD_IMAGE_DEFAULTS[otomatikSonModel];
  const next = RUNPOD_IMAGE_DEFAULTS[otomatikModel()];
  if(previous && next && +$('steps').value === previous.steps) $('steps').value = next.steps;
  if(previous && next && $('sampler').value === previous.sampler) $('sampler').value = next.sampler;
  if(previous && next && previous.cfg != null && next.cfg != null && +$('cfg').value === previous.cfg) $('cfg').value = next.cfg;
  uretimModGuncelle(); formKaydet();
});
if($('flux3BboxMode')) $('flux3BboxMode').addEventListener('change', formKaydet);
function fotoTemizle(slot){
  if(slot === 1){
    fotoB64 = null; fotoMime = null; $('foto').value = '';
    $('oniz').removeAttribute('src'); $('oniz').style.display = 'none';
    $('fotoKaldir').style.display = 'none'; $('fotoBoy').textContent = '';
    $('foto2Blok').style.display = 'none';
  }else{
    fotoB64_2 = null; fotoMime2 = null; $('foto2').value = '';
    $('oniz2').removeAttribute('src'); $('oniz2').style.display = 'none';
    $('foto2Kaldir').style.display = 'none'; $('fotoBoy2').textContent = '';
  }
  cozunurlukGorunumu();
  presetOranUygula();
  uretimModGuncelle();
  uretimOnizlemeGuncelle();
}
$('fotoKaldir').onclick = () => fotoTemizle(1);
$('foto2Kaldir').onclick = () => fotoTemizle(2);
function refVarMi(){ return !!(fotoB64 || fotoB64_2); }
function cozunurlukGorunumu(){
  const ref = refVarMi();
  $('oranBlok').style.display = ref ? 'none' : 'flex';
  $('olcekBlok').style.display = ref ? 'flex' : 'none';
  $('olcekAciklama').style.display = ref ? 'block' : 'none';
  $('olcuBilgi').textContent = ref ?
    'Ölçü fotoğraftan alınır; oran seçimi kapalı.' :
    'Fotoğraf yok: ölçüyü buradan seç (kilitliyken diğer kenar orana göre 16’nın katına yuvarlanır).';
}
function dosyaOku(input, slot){
  const f = input.files[0];
  if(!f){ fotoTemizle(slot); return; }
  const istekNesli = oturumNesli;
  const r = new FileReader();
  r.onload = () => {
    if(istekNesli !== oturumNesli) return;
    const b64 = String(r.result).split(',')[1];
    const im = new Image();
    im.onload = () => {
      if(istekNesli !== oturumNesli) return;
      if(slot === 1){
        fotoB64 = b64;
        fotoMime = f.type || 'image/jpeg';
        $('oniz').src = r.result; $('oniz').style.display = 'block';
        $('fotoKaldir').style.display = 'block';
        $('w').value = im.naturalWidth; $('h').value = im.naturalHeight;
        kilitOran = im.naturalWidth / im.naturalHeight; oranKilitGuncelle();
        $('fotoBoy').textContent = 'Fotoğraf boyutu: ' + im.naturalWidth + 'x' + im.naturalHeight + ' (ölçek bu orandan alınır)';
        $('foto2Blok').style.display = 'block';
        cozunurlukGorunumu();
        uretimModGuncelle();
        uretimOnizlemeGuncelle();
      }else{
        fotoB64_2 = b64;
        fotoMime2 = f.type || 'image/jpeg';
        $('oniz2').src = r.result; $('oniz2').style.display = 'block';
        $('foto2Kaldir').style.display = 'block';
        $('fotoBoy2').textContent = '2. fotoğraf boyutu: ' + im.naturalWidth + 'x' + im.naturalHeight;
        cozunurlukGorunumu();
        uretimModGuncelle();
        uretimOnizlemeGuncelle();
      }
    };
    im.src = r.result;
  };
  r.readAsDataURL(f);
}
$('foto').onchange = e => dosyaOku(e.target, 1);
$('foto2').onchange = e => dosyaOku(e.target, 2);
$('preset').onchange = e => {
  const v = e.target.value;
  if(v === 'ozel'){ kilitOran = null; oranKilitGuncelle(); return; }
  const p = v.split('x'); $('w').value = p[0]; $('h').value = p[1];
  kilitOran = (+p[0]) / (+p[1]); oranKilitGuncelle();
};
let kilitOran = 1;
function oranKilitGuncelle(){
  $('oranKilit').textContent = kilitOran ? 'Oran: kilitli' : 'Oran: serbest';
}
function onAltiyaYuvarla(v){ return Math.max(16, Math.round(v / 16) * 16); }
function kilitliDiger(deger, oran, ters){
  const tam = ters ? Math.round(deger * oran) : Math.round(deger / oran);
  return onAltiyaYuvarla(tam);
}
function presetOranUygula(){
  const v = $('preset').value;
  if(v === 'ozel'){ kilitOran = null; }
  else { const p = v.split('x'); kilitOran = (+p[0]) / (+p[1]); }
  oranKilitGuncelle();
}
$('w').addEventListener('input', () => {
  if(!kilitOran) return;
  const w = +$('w').value;
  if(!(w > 0)) return;
  $('h').value = kilitliDiger(w, kilitOran, false);
});
$('h').addEventListener('input', () => {
  if(!kilitOran) return;
  const h = +$('h').value;
  if(!(h > 0)) return;
  $('w').value = kilitliDiger(h, kilitOran, true);
});
$('oranKilit').onclick = () => {
  if(kilitOran){ kilitOran = null; }
  else {
    const w = +$('w').value, h = +$('h').value;
    kilitOran = (w > 0 && h > 0) ? w / h : null;
  }
  oranKilitGuncelle();
  formKaydet();
};
let durumIstegi = null;
function durum(){
  if(!durumIstegi) durumIstegi = durumVerisiniCiz().finally(() => { durumIstegi = null; });
  return durumIstegi;
}
async function durumVerisiniCiz(){
  try{
    const r = await fetch('/api/durum'); const j = await r.json();
    runpodModelListesiGuncelle(j);
    runpodLlmListesiGuncelle(j);
    const ok = !!j.comfy;
    const llm = j.llm || {};
    $('nokta').className = 'nokta ' + (ok ? 'acik' : 'kapali');
    let satir = 'ComfyUI: ' + (ok ? 'BAĞLI (8188)' : 'ERİŞİLEMİYOR - önce ComfyUI başlat!');
    const model = otomatikModel();
    const modeller = model === 'z_image' ? j.z_image_modeller :
      (model === 'moody_anima' ? j.moody_anima_modeller :
        (model === 'anima' ? j.anima_modeller : j.modeller));
    const modSatir = Object.entries(modeller || {}).map(([k, v]) => k + ': ' + (v.var ? 'OK' : 'EKSİK') + ' ' + v.dosya).join('\n');
    if(modSatir) satir += '\n' + modSatir;
    satir += '\nllama-server: ' + (llm.canli ? 'ÇALIŞIYOR (pid ' + llm.pid + ', ngl ' + llm.ngl + ', ctx ' + llm.ctx + (llm.saglik ? ', ' + llm.saglik : '') + ')' : 'duruyor');
    if(j.vram_bos_mb != null) satir += '\nBoş VRAM: ' + j.vram_bos_mb + ' MB';
    if(j.uretim_suruyor) satir += '\nGörsel üretimi sürüyor';
    if(j.kuyruk_bekleyen) satir += '\nKuyrukta bekleyen: ' + j.kuyruk_bekleyen;
    $('durum').textContent = satir;
    const sayi = Object.values(modeller || {}).filter(v => v.var).length;
    const kisaDurum = $('durumKisa');
    if(kisaDurum) kisaDurum.textContent = (ok ? ('bağlı · ' + sayi + '/3 model') : 'ComfyUI yok') + (llm.canli ? ' · LLM açık' : '');
  }catch(e){
    $('nokta').className = 'nokta kapali'; $('durum').textContent = 'WebUI sunucusuna erişilemiyor.';
    const kisaDurum = $('durumKisa');
    if(kisaDurum) kisaDurum.textContent = 'sunucu yok';
  }
}
durum();
let progTimer = null, kuyrukTimer = null;
function oranYuzde(il){
  if(!il) return null;
  if(il.oran != null && !isNaN(+il.oran)) return Math.round(100 * +il.oran);
  if(il.adim != null && il.max) return Math.round(100 * il.adim / il.max);
  if(il.bitti) return 100;
  return null;
}
function sonIsKaydet(is_id){ try{ localStorage.setItem('son_is', is_id); }catch(e){} }
function sonIsOku(){ try{ return localStorage.getItem('son_is'); }catch(e){ return null; } }
function ilerlemeDurdur(){
  if(progTimer){ clearInterval(progTimer); progTimer = null; }
  $('progBlok').style.display = 'none';
  $('progBar').style.width = '0%'; $('progBarBar').style.width = '0%';
}
function formVerisi(istekSecenekleri){
  const vm = videoMu();
  let suresn = VIDEO_HAZIRLIK.suresn, fps = VIDEO_HAZIRLIK.fps;
  if(vm){
    suresn = +($('vsure') ? $('vsure').value : (RUNPOD_WEBUI ? 0.5 : 5));
    fps = +($('vfps') ? $('vfps').value : (RUNPOD_WEBUI ? 8 : 24));
    VIDEO_HAZIRLIK.suresn = suresn; VIDEO_HAZIRLIK.fps = fps;
  }
  const body=Object.assign({
    prompt: $('prompt').value, negative: $('negatif').value,
    model: otomatikModel(),
    image_model: RUNPOD_WEBUI && $('runpodImageModel') ? $('runpodImageModel').value : 'auto',
    model_instance: RUNPOD_WEBUI ? runpodModelInstanceSecili() : null,
    llm_model: RUNPOD_WEBUI && $('uretimLlmModel') ? $('uretimLlmModel').value : undefined,
    flux3_bbox_mode: RUNPOD_WEBUI && $('flux3BboxMode') ? $('flux3BboxMode').checked : false,
    width: +$('w').value, height: +$('h').value,
    max_side: +$('maxside').value, max_olcek: $('maxolcek').checked,
    video_max_side: vm ? +($('vmax') ? $('vmax').value : (RUNPOD_WEBUI ? 320 : 1344)) : 0,
    video_profile: vm && $('videoProfile') ? ($('videoProfile').value || 'quality') : 'quality',
    video_model_instance: vm ? runpodVideoModelSecili() : null,
    steps: +$('steps').value, cfg: +$('cfg').value,
    sampler: $('sampler').value, seed: +$('seed').value,
    workflow: otomatikWorkflow(), image_base64: fotoB64, image2_base64: fotoB64_2,
    is_tipi: isTipiSec(), suresn: suresn, fps: fps,
    image_mime: fotoMime || 'image/jpeg', image2_mime: fotoMime2 || 'image/jpeg',
    prompt_destek: $('uretimPromptDestek').checked,
    prompt_ceviri: !vm && $('uretimPromptCeviri').checked
  }, istekSecenekleri || {});
  if(RUNPOD_WEBUI && runpodLoraCurrentModel===otomatikModel()) body.loras=runpodLoraOku();
  return body;
}
/* Uretilen sonuc ile gecmisten acilan medya TEK kapsayiciya (#galeri) ve
   TEK sinifla (.uretim-goruntu) girer; iki yon de ayni contain kuralini kullanir. */
function galeriMedyasiEkle(tur, src, alt, poster){
  const el = document.createElement(tur === 'video' ? 'video' : 'img');
  el.className = 'uretim-goruntu';
  el.src = src;
  if(tur === 'video'){
    el.controls = true;
    el.preload = 'metadata';
    if(poster) el.poster = poster;
  }else{
    el.alt = alt || 'Üretilen görsel';
    el.draggable = false;
    el.loading = 'lazy';
  }
  $('galeri').appendChild(el);
  return el;
}
let sonGosterilenSonuc = 0;
function sonucuGoster(s){
  const sonucNesli = ++sonGosterilenSonuc;
  /* Ayar: uretilen gorsel uretim sayfasina cekilsin mi (varsayilan: evet). */
  const cek = $('autoOpenResult') ? $('autoOpenResult').checked : true;
  if(aktifMenu === 'ayarlar') bilgi('Üretim tamamlandı. Sonuç Üretim sekmesinde hazır.');
  else if(cek && aktifMenu !== 'uretim'){ aktifMenu = 'uretim'; menuUygula(); }
  $('ilerleme').textContent = '';
  $('progYazi').textContent = '';
  $('progBlok').style.display = 'none';
  $('progBar').style.width = '0%';
  $('progBarBar').style.width = '0%';
  $('galeri').replaceChildren();
  $('uretimGoruntuEtiket').hidden = true;
  $('uretimGoruntuBos').hidden = true;
  $('uretimGoruntuStage').hidden = false;
  uretimSeciliKayit = null;
  /* Onceki gorselin ayrinti bilgisi kalmasin: yeni kayit acilana kadar bos. */
  $('uretimSeciliBilgi').hidden = true;
  $('uretimSeciliBilgi').replaceChildren();
  if(s.video_url) galeriMedyasiEkle('video', s.video_url, '', s.video_poster_url);
  (s.gorseller || []).forEach(u => galeriMedyasiEkle('gorsel', u, 'Yeni üretilen görsel'));
  kutuphaneYukle(); gecSayfa = 1; gecFavoriler = false;
  try{ arsivYukle(true); }catch(e){}
  $('gecFavoriBtn').setAttribute('aria-pressed', 'false'); $('gecFavoriBtn').textContent = '☆'; $('gecFavoriBtn').title = 'Favoriler';
  $('gecArama').value = '';
  /* Sonuç zaten üretim yanıtında bulunduğu için burada medya URL'sini yeniden
     indirmeyiz. Geçmiş listesi yenilenir, ekrandaki hazır sonuç korunur. */
  gecmisYukle(true).then(async () => {
    if(sonucNesli !== sonGosterilenSonuc || uretimSeciliKayit || !s.gecmis) return;
    const secimNesli = gecGorselSecimNesli;
    const dosya = (s.gecmis_dosyalar || [])[0];
    if(!dosya) return;
    const k = {kayit: s.gecmis + '.json', dosya, tur: s.video_url ? 'video' : 'goruntu',
      url: '/gecmis/' + encodeURIComponent(dosya), video_poster_url: s.video_poster_url,
      prompt: s.prompt || '', yildiz: false};
    try{
      const response = await fetch('/gecmis/' + encodeURIComponent(k.kayit));
      if(!response.ok) return;
      const meta = await response.json();
      if(sonucNesli !== sonGosterilenSonuc || secimNesli !== gecGorselSecimNesli || uretimSeciliKayit) return;
      gecmisKaydiGoster(k, meta, -1, null, true);
    }catch(e){}
  });
  kuyrukYenile();
}
async function uret(istekSecenekleri, kesintisizDevam = false){
  formSimdiKaydet();
  if(!kesintisizDevam && takipIs && uretimKesintisizAktif){
    uretimKesintisizAktif = false; uretimKesintisizIstek = null;
  }
  if(!kesintisizDevam && !takipIs){
    uretimKesintisizAktif = $('continuousGeneration').checked && !videoMu();
    uretimKesintisizIstek = uretimKesintisizAktif ?
      formVerisi(istekSecenekleri) : null;
    if(uretimKesintisizAktif){
      uretimKesintisizIstek.seed = -1;
      $('seed').value = -1;
    }
  }
  const istek = formVerisi(uretimKesintisizAktif ? {
    ...(uretimKesintisizIstek || {}), ...(istekSecenekleri || {}),
    seed: -1
  } : istekSecenekleri);
  $('ilerleme').textContent = 'Kuyruğa ekleniyor...';
  try{
    const r = await fetch('/api/kuyruk', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(istek)});
    const j = await r.json();
    if(j.hata){
      uretimKesintisizAktif = false; uretimKesintisizIstek = null;
      $('ilerleme').textContent = 'HATA: ' + j.hata; return;
    }
    kuyrukYenile(); kuyrukDevamli();
    if(takipIs){
      /* Bir is zaten takipte: onu birakma, bitince zincir bu ise gecer. */
      sonIsKaydet(j.is_id);
      $('ilerleme').textContent = (j.sira > 1) ? 'Kuyruğa eklendi (sırada ' + j.sira + '). Önceki iş bitince otomatik geçilecek.' : 'Kuyruğa eklendi. Önceki iş bitince otomatik geçilecek.';
      return;
    }
    $('ilerleme').textContent = (j.sira > 1) ? 'Kuyruğa eklendi (sırada ' + j.sira + ').' : (videoMu() ? 'Video işi başladı, takip ediliyor... (dakikalar sürebilir)' : 'İş başladı, takip ediliyor... (Turbo ~dakika altı)');
    takipEt(j.is_id);
  }catch(e){
    uretimKesintisizAktif = false; uretimKesintisizIstek = null;
    $('ilerleme').textContent = 'HATA: ' + e;
  }
}
function takipEt(is_id){
  if(progTimer){ clearInterval(progTimer); }
  takipIs = is_id; sonIsKaydet(is_id);
  $('progBlok').style.display = 'block';
  $('durdur').disabled = false; $('durdurBar').disabled = false;
  const nesil = oturumNesli;
  let istekSuruyor = false;
  const yokla = async () => {
    if(istekSuruyor || takipIs !== is_id || nesil !== oturumNesli) return;
    istekSuruyor = true;
    let j;
    try{
      const r = await fetch('/api/is/' + encodeURIComponent(is_id));
      if(!r.ok) return;
      j = await r.json();
    }catch(e){ return; }
    finally{ istekSuruyor = false; }
    if(takipIs !== is_id || nesil !== oturumNesli) return;
    const il = j.ilerleme || {};
    const bar = $('progBar'), bar2 = $('progBarBar'), yazi = $('progYazi');
    const y = oranYuzde(il);
    if(y != null){ bar.style.width = y + '%'; bar2.style.width = y + '%'; }
    if(j.durum === 'kuyrukta') yazi.textContent = 'Sırada bekliyor' + (j.sira ? ' (sırada ' + j.sira + ')' : '') + '...';
    else if(j.durum === 'calisiyor'){
      const videoIsi = j.is_tipi === 'video-uretim';
      const llm = j.llm_canli;
      if(j.tur === 'goruntu' && il.asama === 'ceviri'){
        yazi.textContent = il.calisan || 'Türkçe prompt İngilizceye çevriliyor...';
      }else if(j.tur === 'goruntu' && il.asama === 'prompt'){
        const olcum = [];
        if(llm && llm.aktif){
          if(llm.token != null) olcum.push(llm.token + ' çıktı token');
          if(llm.tok_s != null) olcum.push((+llm.tok_s).toFixed(1) + ' token/sn');
          if(llm.gecen_sn != null) olcum.push(llm.gecen_sn + ' sn');
        }
        const modelDurumu = llm && llm.aktif ?
          (llm.faz === 'prompt' ? 'Prompt işleniyor' :
            llm.faz === 'uretim' ? 'Prompt üretiliyor' : 'Model hazırlanıyor') :
          'Model hazırlanıyor · ' + Math.round(j.gecen || 0) + ' sn';
        yazi.textContent = [modelDurumu].concat(olcum).join(' · ');
      }else if(il.adim != null && il.max) yazi.textContent = (videoIsi ? 'Video üretiliyor' : 'Örnekleniyor') + ': adım ' + il.adim + '/' + il.max + (il.node ? ' (' + il.node + ')' : '');
      else if(il.bitti) yazi.textContent = 'Son işlemler...';
      else if(il.calisan) yazi.textContent = videoIsi ? ('Video: ' + il.calisan) : ('Çalışan: ' + il.calisan);
      else yazi.textContent = 'Kuyrukta bekleniyor...';
    }
    if(j.durum === 'bitti' || j.durum === 'hata' || j.durum === 'durduruldu'){
      if(progTimer){ clearInterval(progTimer); progTimer = null; }
      takipIs = null;
      ilerlemeDurdur();
      $('durdur').disabled = true; $('durdurBar').disabled = true;
      if(j.durum === 'bitti' && j.sonuc) sonucuGoster(j.sonuc);
      else if(j.durum === 'durduruldu'){
        uretimKesintisizAktif = false; uretimKesintisizIstek = null;
        $('ilerleme').textContent = 'Durduruldu.'; kuyrukYenile(); kutuphaneYukle(); gecSayfa = 1; gecmisYukle(true);
      }else {
        uretimKesintisizAktif = false; uretimKesintisizIstek = null;
        $('ilerleme').textContent = 'HATA: ' + (j.hata || 'üretim başarısız'); kuyrukYenile(); kutuphaneYukle(); gecSayfa = 1; gecmisYukle(true);
      }
      if(j.durum === 'bitti' && j.sonuc && uretimKesintisizAktif){
        const onceki = uretimKesintisizIstek || {};
        uretimKesintisizIstek = Object.assign({}, onceki, {
          prompt: j.sonuc.prompt_en || j.sonuc.prompt || onceki.prompt,
          prompt_destek: false,
          prompt_ceviri: false,
          seed: -1
        });
        uret(uretimKesintisizIstek, true);
        return;
      }
      siradakiIsiTakipEt(is_id);
    }
  };
  yokla();
  progTimer = setInterval(yokla, 1000);
}
async function siradakiIsiTakipEt(biten){
  kuyrukYenile();
  try{
    const r = await fetch('/api/isler'); const j = await r.json();
    const siradaki = (j.isler || []).find(k => k.tur === 'goruntu' && k.is_id !== biten && (k.durum === 'calisiyor' || k.durum === 'kuyrukta'));
    if(siradaki){ $('ilerleme').textContent += ' Sıradaki işe geçildi.'; takipEt(siradaki.is_id); }
  }catch(e){}
}
let kuyrukIstek = null;
function kuyrukYenile(){
  if(!kuyrukIstek) kuyrukIstek = kuyrukVerisiniCiz().finally(() => { kuyrukIstek = null; });
  return kuyrukIstek;
}
async function kuyrukVerisiniCiz(){
  const nesil = oturumNesli;
  let aktifSayi = 0;
  try{
    const r = await fetch('/api/isler'); const j = await r.json();
    if(!r.ok || nesil !== oturumNesli) return null;
    const div = $('kuyrukListe'); div.innerHTML = '';
    const aktifler = (j.isler || []).filter(k => k.durum === 'kuyrukta' || k.durum === 'calisiyor');
    aktifSayi = aktifler.length;
    $('kuyrukSayi').textContent = aktifSayi ? '(' + aktifSayi + ' iş)' : '';
    if(!aktifSayi){ div.innerHTML = '<div class="kucuk">Kuyruk boş.</div>'; return 0; }
    aktifler.forEach(k => {
      const sat = document.createElement('div'); sat.className = 'listeogesi';
      const b = document.createElement('div'); b.className = 'bilgi';
      const ad = document.createElement('b');
      const llmIs = k.tur === 'llm_prompt' || k.tur === 'llm_chat';
      const turAdi = k.tur === 'llm_prompt' ? 'LLM prompt' : (k.tur === 'llm_chat' ? 'LLM sohbet' : 'Görsel');
      ad.textContent = (k.durum === 'calisiyor' ? '● Çalışıyor' : '○ Sırada ' + k.sira) + ' — ' + turAdi + (k.prompt ? ': ' + k.prompt : '');
      const alt = document.createElement('span'); alt.className = 'kucuk';
      const y = oranYuzde(k.ilerleme);
      alt.textContent = (y != null && k.durum === 'calisiyor' ? '%' + y + ' · ' : '') + (llmIs ? ((k.ilerleme && k.ilerleme.calisan) || 'LLM sırası') + (k.gecen ? ' · ' + Math.round(k.gecen) + ' sn' : '') : (k.is_tipi === 'video-uretim' ? 'video' : (k.workflow === 'cift' ? 'çift referans' : (k.workflow === 'video' ? 'video' : 'tek'))));
      const ceviriAsamalari = {
        bekliyor: 'Çeviri sırası',
        calisiyor: 'Çevriliyor',
        hazir: 'Çeviri hazır',
        hata: 'Çeviri başarısız'
      };
      if(ceviriAsamalari[k.ceviri_durum]) alt.textContent += ' · ' + ceviriAsamalari[k.ceviri_durum];
      b.appendChild(ad); b.appendChild(alt);
      const ipt = document.createElement('button'); ipt.className = 'kucukbtn tehlike'; ipt.textContent = 'İptal';
      ipt.onclick = async () => {
        ipt.disabled = true;
        try{
          const r = await fetch('/api/is/iptal', {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify({is_id: k.is_id})});
          const j = await r.json().catch(() => ({}));
          if(!r.ok) bilgi(j.hata || 'İş iptal edilemedi.');
          await kuyrukIstek;
          await kuyrukYenile();
        }catch(e){ bilgi('İş iptal edilemedi.'); }
        finally{ ipt.disabled = false; }
      };
      sat.appendChild(b); sat.appendChild(ipt);
      div.appendChild(sat);
    });
  }catch(e){ return null; }
  return aktifSayi;
}
function kuyrukDevamli(){
  if(kuyrukTimer) return;
  kuyrukTimer = setInterval(async () => {
    const n = await kuyrukYenile();
    if(n === 0 && !takipIs){ clearInterval(kuyrukTimer); kuyrukTimer = null; }
  }, 3000);
}
async function sonIsiBaglan(){
  /* Yeniden yuklemede en eski aktif ise baglan (kuyruktakiler zincirle sirasiyla gosterilir). */
  try{
    const r = await fetch('/api/isler'); const j = await r.json();
    const aktif = (j.isler || []).filter(k => k.tur === 'goruntu' && (k.durum === 'calisiyor' || k.durum === 'kuyrukta'));
    if(aktif.length){
      aktif.sort((a, b) => ((a.durum === 'calisiyor' ? 0 : 1) - (b.durum === 'calisiyor' ? 0 : 1)) || ((a.sira || 0) - (b.sira || 0)));
      $('ilerleme').textContent = 'Devam eden işe bağlanıldı.';
      takipEt(aktif[0].is_id); kuyrukDevamli();
      return;
    }
  }catch(e){}
  const is_id = sonIsOku();
  if(!is_id) return;
  try{
    const r = await fetch('/api/is/' + encodeURIComponent(is_id));
    if(!r.ok) return;
    const j = await r.json();
    if(j.durum === 'kuyrukta' || j.durum === 'calisiyor'){
      $('ilerleme').textContent = 'Devam eden işe bağlanıldı.';
      takipEt(is_id); kuyrukDevamli();
    }else if(j.durum === 'bitti' && j.sonuc){
      sonucuGoster(j.sonuc);
      $('ilerleme').textContent += ' (önceki işin sonucu)';
    }
  }catch(e){}
}
$('uret').onclick = uret;
$('uretBar').onclick = () => $('uret').click();
$('videoUret').onclick = () => {
  if($('wf').value !== 'video'){ $('wf').value = 'video'; $('wf').onchange({target: $('wf')}); }
  uret();
};
async function durdur(){
  uretimKesintisizAktif = false;
  uretimKesintisizIstek = null;
  $('ilerleme').textContent = 'Durdurma gönderildi...';
  try{
    const r = await fetch('/api/durdur', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({is_id: takipIs})});
    const j = await r.json().catch(() => ({}));
    if(!r.ok || !j.durduruldu) throw new Error(j.hata || 'İş artık etkin değil');
    $('ilerleme').textContent = 'Durduruluyor…';
  }catch(e){
    const hata = 'Durdurma isteği başarısız: ' + (e.message || e);
    $('ilerleme').textContent = hata;
    bilgi(hata);
  }
}
$('durdur').onclick = durdur;
$('durdurBar').onclick = durdur;
/* Kütüphane + secici: referans fotoyu kutuphane veya gecmisten secer.
   hedef='sohbet' ise secim sohbet gorseline atanir. */
let secSlot = 1, secKaynak = 'kutuphane', secHedef = 'uretim';
async function seciciAc(slot, hedef){
  secSlot = slot || 1; secKaynak = 'kutuphane'; secHedef = hedef || 'uretim';
  kaplaAc('<h3 style="margin-top:0">' + (secHedef === 'sohbet' ?
    'Sohbete görsel seç' : (secHedef === 'studyo' ?
      'Stüdyoya görsel seç (' + secSlot + '. konuma)' :
      'Fotoğraf seç (' + secSlot + '. konuma)')) +
    '</h3><div><button id="secTabK" class="hayalet kucukbtn">Kütüphane</button> <button id="secTabG" class="hayalet kucukbtn">Geçmiş</button></div><div id="secYuk">Yükleniyor...</div>');
  let lib = [], gec = [], libHata = '', gecHata = '';
  try{
    const r = await fetch('/api/kutuphane');
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    lib = j.fotograflar || [];
  }catch(e){ libHata = e.message || 'Kütüphane yüklenemedi'; }
  try{
    const r = await fetch('/api/gecmis?sayfa=1&adet=24');
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    gec = (j.kayitlar || []).filter(k => k.tur !== 'video');
  }catch(e){ gecHata = e.message || 'Geçmiş yüklenemedi'; }
  const secildi = k => {
    if(secHedef === 'sohbet') sohbetKutuphanedenSecildi(k);
    else if(secHedef === 'studyo') psKutuphanedenSecildi(k);
    else libAta(secKaynak === 'kutuphane' ? {url: k.url, boyut: k.boyut} : {url: k.url}, secSlot);
  };
  const ciz = () => {
    const div = $('secYuk');
    if(!div) return;
    div.innerHTML = '';
    const g = document.createElement('div'); g.className = 'secim-grid'; div.appendChild(g);
    const liste = secKaynak === 'kutuphane' ? lib : gec;
    if(!liste.length){
      const b = document.createElement('div'); b.className = 'kucuk';
      b.textContent = secKaynak === 'kutuphane' ? (libHata || 'Kütüphanede henüz görsel yok.') :
        (gecHata || 'Geçmişte seçilebilir görsel yok.');
      div.appendChild(b);
    }
    liste.forEach(k => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'secim-kart';
      const im = document.createElement('img');
      if(secKaynak === 'kutuphane'){ im.src = k.thumb; im.alt = k.ad; }
      else { im.src = k.url; im.alt = k.kayit || ''; }
      im.loading = 'lazy';
      btn.appendChild(im);
      btn.onclick = () => secildi(k);
      g.appendChild(btn);
    });
    $('secTabK').disabled = secKaynak === 'kutuphane';
    $('secTabG').disabled = secKaynak === 'gecmis';
    $('secTabK').onclick = () => { secKaynak = 'kutuphane'; ciz(); };
    $('secTabG').onclick = () => { secKaynak = 'gecmis'; ciz(); };
  };
  ciz();
}
$('libTemizle').onclick = async () => {
  if(!confirm('Kütüphanedeki TÜM fotoğraflar silinsin mi?')) return;
  try{
    const r = await fetch('/api/kutuphane/temizle', {method: 'POST'});
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    await kutuphaneYukle();
    bilgi('Kütüphane temizlendi');
  }catch(e){ bilgi('Kütüphane temizlenemedi: ' + (e.message || e)); }
};
let kutuphaneKayitlari = [];
function kutuphaneIzgarasiCiz(){
  const iz = $('libIzgara'), arama = $('libArama').value.trim().toLocaleLowerCase('tr');
  const liste = kutuphaneKayitlari.filter(k => !arama || String(k.ad || '').toLocaleLowerCase('tr').includes(arama));
  iz.replaceChildren();
  $('libSayac').textContent = arama ?
    liste.length + ' / ' + kutuphaneKayitlari.length + ' görsel' :
    kutuphaneKayitlari.length + (kutuphaneKayitlari.length === 1 ? ' görsel' : ' görsel');
  $('libTemizle').hidden = !kutuphaneKayitlari.length;
  $('libBos').hidden = liste.length > 0;
  if(!liste.length){
    $('libBosBaslik').textContent = kutuphaneKayitlari.length ? 'Eşleşen görsel bulunamadı' : 'Henüz görsel yok';
    $('libBosMetin').textContent = kutuphaneKayitlari.length ?
      'Farklı bir adla tekrar arayabilir veya aramayı temizleyebilirsin.' :
      'Üretim yaptığında kullanılan görseller burada görünür. Daha sonra kütüphaneden referans olarak ekleyebilirsin.';
    return;
  }
  liste.forEach(k => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'lib-kart';
    btn.setAttribute('aria-label', (k.ad || 'Görsel') + ' ayrıntılarını aç');
    if(k.boyut && k.boyut[0] > 0 && k.boyut[1] > 0)
      btn.style.setProperty('--lib-oren', (k.boyut[0] / k.boyut[1]).toFixed(4));
    const frame = document.createElement('span'); frame.className = 'lib-kart-gorsel';
    const im = document.createElement('img');
    im.src = k.thumb; im.loading = 'lazy'; im.alt = '';
    frame.appendChild(im);
    const detail = document.createElement('span'); detail.className = 'lib-kart-bilgi';
    const name = document.createElement('span'); name.className = 'lib-kart-ad'; name.textContent = k.ad || 'Görsel';
    const meta = document.createElement('span'); meta.className = 'lib-kart-meta';
    meta.textContent = [k.boyut && k.boyut.join(' × '), k.bayt != null && boyutYaz(k.bayt), k.bicim]
      .filter(Boolean).join(' · ');
    detail.append(name, meta);
    btn.append(frame, detail);
    btn.onclick = () => lbAcListe(kutuphaneKayitlari.filter(x => !arama || String(x.ad || '').toLocaleLowerCase('tr').includes(arama)),
      kutuphaneKayitlari.filter(x => !arama || String(x.ad || '').toLocaleLowerCase('tr').includes(arama)).indexOf(k));
    iz.appendChild(btn);
  });
  libGorunumUygula();
}
async function kutuphaneYukle(){
  try{
    $('libHata').textContent = '';
    $('libSayac').textContent = 'Yükleniyor';
    const r = await fetch('/api/kutuphane');
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    if(!Array.isArray(j.fotograflar)) throw new Error('Sunucu kütüphane listesini beklenen biçimde döndürmedi.');
    kutuphaneKayitlari = j.fotograflar;
    kutuphaneIzgarasiCiz();
  }catch(e){
    kutuphaneKayitlari = [];
    $('libIzgara').replaceChildren();
    $('libBos').hidden = true;
    $('libTemizle').hidden = true;
    $('libSayac').textContent = 'Yüklenemedi';
    $('libHata').textContent = 'Kütüphane yüklenemedi: ' + (e.message || e);
  }
}
$('libArama').addEventListener('input', kutuphaneIzgarasiCiz);
$('libIzgaraBtn').onclick = () => { libGorunum = 'izgara'; libGorunumUygula(); };
$('libAkisBtn').onclick = () => { libGorunum = 'akis'; libGorunumUygula(); };
$('arsivIzgaraBtn').onclick = () => { arsivGorunum = 'izgara'; libGorunumUygula(); };
$('arsivAkisBtn').onclick = () => { arsivGorunum = 'akis'; libGorunumUygula(); };
/* --- Arşiv: tüm fotoğraflar, hash ile tekilleştirilmiş --- */
let arsivKayitlari = [], arsivSayfa = 1, arsivToplam = 0, arsivYukleniyor = false;
let arsivGorunum = 'izgara', libGorunum = 'izgara';
let arsivSecimAktif = false;
const arsivSecilenler = new Set();
const ARSIV_TOPLU_SIL_SINIRI = 100;
function arsivSecimDurumuGuncelle(){
  const bar=$('arsivSecimBar'), button=$('arsivSecBtn');
  if($('arsivIzgara')) $('arsivIzgara').classList.toggle('arsiv-secim-modu',arsivSecimAktif);
  if(bar) bar.hidden=!arsivSecimAktif;
  if(button){button.setAttribute('aria-pressed',arsivSecimAktif?'true':'false');button.textContent=arsivSecimAktif?'Seçim açık':'Seç';}
  if($('arsivSecimSayisi')) $('arsivSecimSayisi').textContent=arsivSecilenler.size+' fotoğraf seçildi';
  if($('arsivSecilenSil')) $('arsivSecilenSil').disabled=!arsivSecilenler.size;
}
function arsivSecToggle(k){
  if(!k?.hash) return;
  if(arsivSecilenler.has(k.hash)) arsivSecilenler.delete(k.hash);
  else if(arsivSecilenler.size >= ARSIV_TOPLU_SIL_SINIRI){
    if($('arsivHata')) $('arsivHata').textContent='Bir seferde en fazla '+ARSIV_TOPLU_SIL_SINIRI+' fotoğraf seçebilirsin.';
    return;
  } else arsivSecilenler.add(k.hash);
  const kart=document.querySelector('.arsiv-sec-kart[data-hash="'+k.hash+'"]');
  if(kart){kart.classList.toggle('secili',arsivSecilenler.has(k.hash));
    const sec=kart.querySelector('.arsiv-kart-sec');
    if(sec) sec.setAttribute('aria-pressed',arsivSecilenler.has(k.hash)?'true':'false');}
  arsivSecimDurumuGuncelle();
}
function arsivSecimTemizle(){
  arsivSecilenler.clear();
  document.querySelectorAll('.arsiv-sec-kart.secili').forEach(k=>k.classList.remove('secili'));
  document.querySelectorAll('.arsiv-kart-sec[aria-pressed="true"]').forEach(k=>k.setAttribute('aria-pressed','false'));
  arsivSecimDurumuGuncelle();
}
function libGorunumUygula(){
  const iz = $('libIzgara'), ak = $('arsivIzgara');
  if(iz) iz.classList.toggle('akis', libGorunum === 'akis');
  if(ak) ak.classList.toggle('akis', arsivGorunum === 'akis');
  const b1 = $('libIzgaraBtn'), b2 = $('libAkisBtn');
  if(b1) b1.classList.toggle('aktif', libGorunum === 'izgara');
  if(b2) b2.classList.toggle('aktif', libGorunum === 'akis');
  const a1 = $('arsivIzgaraBtn'), a2 = $('arsivAkisBtn');
  if(a1) a1.classList.toggle('aktif', arsivGorunum === 'izgara');
  if(a2) a2.classList.toggle('aktif', arsivGorunum === 'akis');
}
function arsivIzgarasiCiz(){
  const iz = $('arsivIzgara');
  if(!iz) return;
  iz.replaceChildren();
  const sayac = $('arsivSayac');
  if(sayac) sayac.textContent = arsivToplam ?
    arsivToplam + ' benzersiz fotoğraf' : 'Arşiv taranıyor';
  const bos = $('arsivBos');
  if(bos) bos.hidden = arsivKayitlari.length > 0;
  if(!arsivKayitlari.length){
    if($('arsivBosBaslik')) $('arsivBosBaslik').textContent =
      arsivToplam ? 'Eşleşen fotoğraf yok' : 'Henüz fotoğraf yok';
    return;
  }
  arsivKayitlari.forEach(k => {
    const wrap = document.createElement('div');
    wrap.className = 'arsiv-sec-kart'+(arsivSecilenler.has(k.hash)?' secili':'');
    wrap.dataset.hash = k.hash || '';
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'lib-kart';
    btn.setAttribute('aria-label', (k.ad || 'Fotoğraf') + ' ayrıntılarını aç');
    if(k.boyut && k.boyut[0] > 0 && k.boyut[1] > 0)
      btn.style.setProperty('--lib-oren', (k.boyut[0] / k.boyut[1]).toFixed(4));
    const frame = document.createElement('span'); frame.className = 'lib-kart-gorsel';
    const im = document.createElement('img');
    im.src = k.thumb; im.loading = 'lazy'; im.alt = '';
    frame.appendChild(im);
    const detail = document.createElement('span'); detail.className = 'lib-kart-bilgi';
    const name = document.createElement('span'); name.className = 'lib-kart-ad';
    name.textContent = k.ad || (k.hash || '').slice(0, 12);
    const meta = document.createElement('span'); meta.className = 'lib-kart-meta';
    const p = [];
    if(k.boyut) p.push(k.boyut.join(' × '));
    if(k.bayt != null) p.push(boyutYaz(k.bayt));
    if(k.kaynak_sayisi > 1) p.push(k.kaynak_sayisi + ' kopya');
    if(k.tarih) p.push(String(k.tarih).replace('T', ' '));
    meta.textContent = p.join(' · ');
    detail.append(name, meta);
    btn.append(frame, detail);
    btn.onclick = () => {
      if(arsivSecimAktif){arsivSecToggle(k);return;}
      lbAcListe(arsivKayitlari, arsivKayitlari.indexOf(k));
    };
    const select=document.createElement('button');
    select.type='button';select.className='arsiv-kart-sec';
    select.textContent=arsivSecilenler.has(k.hash)?'✓':'+';
    select.title='Arşiv görünümünden kaldır; kaynak kayıtları korunur';
    select.setAttribute('aria-label',select.title);
    select.setAttribute('aria-pressed',arsivSecilenler.has(k.hash)?'true':'false');
    select.onclick=e=>{e.stopPropagation();arsivSecToggle(k);};
    wrap.append(btn,select);
    iz.appendChild(wrap);
  });
  libGorunumUygula();
  arsivSecimDurumuGuncelle();
}
function arsivSayfaCiz(){
  const kutu = $('arsivSayfalar');
  if(!kutu) return;
  kutu.replaceChildren();
  const sayfaAdet = Math.max(1, Math.ceil(arsivToplam / 24));
  if(sayfaAdet <= 1) return;
  const mk = (metin, hedef, aktif, kapali) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'hayalet kucukbtn' + (aktif ? ' aktif' : '');
    b.textContent = metin; b.disabled = !!kapali;
    b.onclick = () => { arsivSayfa = hedef; arsivYukle(true); };
    return b;
  };
  kutu.appendChild(mk('‹', arsivSayfa - 1, false, arsivSayfa <= 1));
  const bas = Math.max(1, arsivSayfa - 2), son = Math.min(sayfaAdet, arsivSayfa + 2);
  for(let i = bas; i <= son; i++) kutu.appendChild(mk(String(i), i, i === arsivSayfa));
  kutu.appendChild(mk('›', arsivSayfa + 1, false, arsivSayfa >= sayfaAdet));
}
async function arsivYukle(temizle){
  if(arsivYukleniyor) return;
  arsivYukleniyor = true;
  if(temizle) arsivSayfa = Math.max(1, arsivSayfa);
  const q = ($('arsivArama') && $('arsivArama').value || '').trim();
  try{
    const r = await fetch('/api/arsiv?sayfa=' + arsivSayfa + '&adet=24&q=' + encodeURIComponent(q));
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    arsivKayitlari = j.kayitlar || [];
    arsivToplam = j.toplam || 0;
    if($('arsivHata')) $('arsivHata').textContent = '';
    arsivIzgarasiCiz();
    arsivSayfaCiz();
  }catch(e){
    arsivKayitlari = []; arsivToplam = 0;
    if($('arsivHata')) $('arsivHata').textContent = 'Arşiv yüklenemedi: ' + (e.message || e);
    arsivIzgarasiCiz();
  }finally{
    arsivYukleniyor = false;
  }
}
function arsivSec(k){
  const p = [];
  if(k.boyut) p.push('Boyut: ' + k.boyut.join(' × '));
  if(k.bayt != null) p.push(boyutYaz(k.bayt));
  if(k.tarih) p.push(String(k.tarih).replace('T', ' '));
  const kaynaklar = (k.kaynaklar || []).map(x => {
    const etiket = x.tur === 'ice-aktarim' ? 'İçe aktarılan' :
      (x.tur === 'kutuphane' ? 'Kütüphane' :
        (x.tur === 'gecmis' ? 'Geçmiş' : 'Sohbet'));
    return etiket + ': ' + x.ad;
  });
  kaplaAc('<h3 style="margin-top:0">' + escHtml(k.ad || (k.hash || '').slice(0, 12)) + '</h3>' +
    '<img class="buyuk" src="' + k.url + '" onclick="lbAc(this.src)">' +
    '<div class="kucuk">' + escHtml(p.join(' · ')) + '</div>' +
    (kaynaklar.length ? '<div class="kucuk"><b>Bulunduğu yerler:</b><br>' +
      kaynaklar.map(escHtml).join('<br>') + '</div>' : '') +
    '<div class="satir"><div><button id="arsivReferans" class="kucukbtn">Üretimde referans olarak ekle</button></div></div>' +
    '<div class="satir"><div><button id="arsivSil" class="tehlike kucukbtn">Arşivden kaldır</button></div></div>');
  const ref = document.getElementById('arsivReferans');
  if(ref) ref.onclick = async () => {
    const slot = uretimBosSlotBul();
    if(!slot){ bilgi('İki referans görsel ekli. Önce birini kaldır.'); return; }
    await libAta({url: k.url}, slot);
    $('kapla').classList.remove('acik');
  };
  const sil = document.getElementById('arsivSil');
  if(sil) sil.onclick = async () => {
    if(!confirm('Bu fotoğraf arşiv görünümünden kaldırılsın mı? İçe aktarılmış arşiv kopyası varsa silinir; kütüphane, geçmiş ve sohbet kayıtları korunur.')) return;
    try{
      const r = await fetch('/api/arsiv/sil', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({hash: k.hash})});
      const j = await r.json().catch(() => ({}));
      if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
      $('kapla').classList.remove('acik');
      await arsivYukle(true);
    }catch(e){ bilgi('Silinemedi: ' + (e.message || e)); }
  };
}
$('arsivArama').addEventListener('input', () => {
  arsivSecimTemizle();
  if(arsivAramaGecikme) clearTimeout(arsivAramaGecikme);
  arsivAramaGecikme = setTimeout(() => { arsivSayfa = 1; arsivYukle(true); }, 250);
});
let arsivAramaGecikme = null;
$('arsivSecBtn').onclick=()=>{
  arsivSecimAktif=!arsivSecimAktif;
  if(!arsivSecimAktif) arsivSecimTemizle();
  else arsivSecimDurumuGuncelle();
};
$('arsivSecimIptal').onclick=()=>{arsivSecimAktif=false;arsivSecimTemizle();};
$('arsivSayfaSec').onclick=()=>{
  let sinirDoldu=false;
  for(const k of arsivKayitlari) if(!arsivSecilenler.has(k.hash)){
    if(arsivSecilenler.size>=ARSIV_TOPLU_SIL_SINIRI){sinirDoldu=true;break;}
    arsivSecilenler.add(k.hash);
  }
  if(sinirDoldu&&$('arsivHata')) $('arsivHata').textContent='Bir seferde en fazla '+ARSIV_TOPLU_SIL_SINIRI+' fotoğraf seçebilirsin.';
  arsivIzgarasiCiz();
};
$('arsivSecilenSil').onclick=async()=>{
  const hashes=Array.from(arsivSecilenler);
  if(!hashes.length) return;
  if(!confirm(hashes.length+' fotoğraf arşiv görünümünden kaldırılsın mı? İçe aktarılmış kopyalar silinir; kütüphane, geçmiş ve sohbet kayıtları korunur.')) return;
  const button=$('arsivSecilenSil');button.disabled=true;
  try{
    const response=await fetch('/api/arsiv/sil-coklu',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hashes})});
    const data=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(data.hata||'HTTP '+response.status);
    const removed=Number(data.silinen)||0;
    arsivToplam=Math.max(0,arsivToplam-removed);
    arsivSayfa=Math.min(arsivSayfa,Math.max(1,Math.ceil(arsivToplam/24)));
    arsivSecimAktif=false;arsivSecimTemizle();
    if($('arsivHata')) $('arsivHata').textContent=removed
      ? removed+' fotoğraf arşiv görünümünden kaldırıldı. Kaynak kayıtlar korundu.'
      : 'Seçili fotoğraflar arşivde bulunamadı.';
    await arsivYukle(true);
  }catch(error){
    if($('arsivHata')) $('arsivHata').textContent='Silme başarısız: '+(error.message||error);
  }finally{button.disabled=!arsivSecilenler.size;}
};
$('arsivIceAktarBtn').onclick = () => $('arsivIceAktar').click();
$('arsivIceAktar').onchange = async e => {
  const dosyalar = Array.from(e.target.files || []);
  e.target.value = '';
  if(!dosyalar.length) return;
  const DURUM = $('arsivHata');
  const PARTI = 12;   // her istekte en fazla 12 dosya: JSON gövdesi küçük kalir
  let eklenen = 0, tekrar = 0, hataSayisi = 0;
  const hatalar = [];
  for(let bas = 0; bas < dosyalar.length; bas += PARTI){
    const dilim = dosyalar.slice(bas, bas + PARTI);
    if(DURUM) DURUM.textContent = 'İçe aktarılıyor... ' +
      Math.min(bas + PARTI, dosyalar.length) + ' / ' + dosyalar.length;
    const gorseller = [];
    for(const f of dilim){
      try{
        const veri = await new Promise((ok, hata) => {
          const rd = new FileReader();
          rd.onload = () => ok(String(rd.result));
          rd.onerror = () => hata(new Error('dosya okunamadı'));
          rd.readAsDataURL(f);
        });
        const parca = veri.split(',');
        gorseller.push({mime: (parca[0].match(/data:([^;,]+)/) || [])[1] || f.type || 'image/png',
                        veri: parca[1], ad: f.name});
      }catch(err){ hataSayisi++; }
    }
    if(!gorseller.length) continue;
    try{
      const r = await fetch('/api/arsiv/ice-aktar', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({gorseller})});
      const j = await r.json().catch(() => ({}));
      if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
      eklenen += j.eklenen || 0;
      tekrar += j.tekrar || 0;
      if(j.hata && j.hata.length){ hataSayisi += j.hata.length; hatalar.push(...j.hata); }
    }catch(err){
      hataSayisi++;
      hatalar.push(String(err && err.message ? err.message : err).slice(0, 120));
    }
  }
  const p = [];
  if(eklenen) p.push(eklenen + ' yeni');
  if(tekrar) p.push(tekrar + ' tekrar (atlandı)');
  if(hataSayisi) p.push(hataSayisi + ' hata');
  bilgi('İçe aktarma: ' + (p.join(' · ') || 'değişiklik yok'));
  if(DURUM) DURUM.textContent = hatalar.length ?
    'Hatalar: ' + hatalar.slice(0, 5).join('; ') : '';
  arsivSayfa = 1;
  await arsivYukle(true);
};
function libSec(k){
    const p = [];
    if(k.boyut) p.push('Boyut: ' + k.boyut.join('x'));
    if(k.bicim) p.push(k.bicim);
    if(k.mod) p.push(k.mod);
    if(k.bayt != null) p.push(boyutYaz(k.bayt));
    if(k.tarih) p.push(k.tarih.replace('T', ' '));
    if(k.orijinal){
      const o = k.orijinal;
      p.push('Orijinal: ' + (o.boyut ? o.boyut.join('x') : '?') +
        (o.bayt != null ? ' · ' + boyutYaz(o.bayt) : ''));
    }
    const buttons = '<div class="satir"><div><button id="libSil" class="tehlike kucukbtn">Kütüphaneden sil</button></div>';
    kaplaAc('<h3 style="margin-top:0">' + escHtml(k.ad) + '</h3>' +
      '<img class="buyuk" src="' + k.url + '" onclick="lbAc(this.src)">' +
      '<div class="kucuk">' + escHtml(p.join(' · ')) + '</div>' +
      '<div class="satir"><div><button id="libReferans" class="kucukbtn">Üretimde referans olarak ekle</button></div></div>' +
      '<div class="satir"><div>' + buttons + '</div></div>');
    const libReferans = document.getElementById('libReferans');
    if(libReferans){
      libReferans.onclick = async () => {
        const slot = uretimBosSlotBul();
        if(!slot){ bilgi('İki referans görsel ekli. Önce birini kaldır.'); return; }
        await libAta(k, slot);
      };
    }
    const libSilBtn = document.getElementById('libSil');
    if(libSilBtn){
      libSilBtn.onclick = async () => {
        if(!confirm('Silinsin mi? ' + k.ad)) return;
        try{
          const r = await fetch('/api/kutuphane/sil', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ad: k.ad})});
          const j = await r.json().catch(() => ({}));
          if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
          $('kapla').classList.remove('acik');
          await kutuphaneYukle();
        }catch(e){ bilgi('Görsel silinemedi: ' + (e.message || e)); }
      };
    }
  }
function gorselSlotUygula(slot, dataUrl, kaynakYazi){
  const istekNesli = oturumNesli;
  const im = new Image();
  im.onload = () => {
    if(istekNesli !== oturumNesli) return;
    const b64 = String(dataUrl).split(',')[1];
    const mime = (String(dataUrl).match(/^data:([^;,]+)/) || [])[1] || 'image/jpeg';
    if(slot === 1){
      fotoB64 = b64; fotoMime = mime; $('oniz').src = dataUrl; $('oniz').style.display = 'block';
      $('fotoKaldir').style.display = 'block';
      $('w').value = im.naturalWidth; $('h').value = im.naturalHeight;
      kilitOran = im.naturalWidth / im.naturalHeight; oranKilitGuncelle();
      $('fotoBoy').textContent = 'Fotoğraf boyutu: ' + im.naturalWidth + 'x' + im.naturalHeight +
        (kaynakYazi ? ' (' + kaynakYazi + ')' : '');
      $('foto2Blok').style.display = 'block';
      cozunurlukGorunumu();
      uretimModGuncelle();
      uretimOnizlemeGuncelle();
    }else{
      fotoB64_2 = b64; fotoMime2 = mime; $('foto2Blok').style.display = 'block';
      $('oniz2').src = dataUrl; $('oniz2').style.display = 'block';
      $('foto2Kaldir').style.display = 'block';
      cozunurlukGorunumu();
      $('fotoBoy2').textContent = '2. fotoğraf boyutu: ' + im.naturalWidth + 'x' + im.naturalHeight +
        (kaynakYazi ? ' (' + kaynakYazi + ')' : '');
      uretimModGuncelle();
      uretimOnizlemeGuncelle();
    }
    window.scrollTo({top: 0, behavior: 'smooth'});
  };
  im.src = dataUrl;
}
async function libAta(k, slot){
  const istekNesli = oturumNesli;
  try{
    const r = await fetch(k.url);
    if(istekNesli !== oturumNesli) return false;
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const blob = await r.blob();
    if(istekNesli !== oturumNesli) return false;
    const dataUrl = await new Promise((resolve, reject) => {
      const rd = new FileReader();
      rd.onload = () => resolve(String(rd.result));
      rd.onerror = () => reject(new Error('Görsel okunamadı'));
      rd.readAsDataURL(blob);
    });
    if(istekNesli !== oturumNesli) return false;
    await new Promise((resolve, reject) => {
      const im = new Image();
      im.onload = resolve;
      im.onerror = () => reject(new Error('Görsel çözümlenemedi'));
      im.src = dataUrl;
    });
    if(istekNesli !== oturumNesli) return false;
    gorselSlotUygula(slot, dataUrl, k.boyut ? 'kütüphaneden' : 'geçmişten');
    $('kapla').classList.remove('acik');
    return true;
  }catch(e){
    if(istekNesli === oturumNesli) bilgi('Görsel eklenemedi: ' + (e.message || e));
    return false;
  }
}
/* Promptlarım */
function formdanPreset(ad){
  return {ad: ad, prompt: $('prompt').value, negative: $('negatif').value,
    model: otomatikModel(),
    workflow: otomatikWorkflow(), steps: +$('steps').value, cfg: +$('cfg').value,
    sampler: $('sampler').value, width: +$('w').value, height: +$('h').value,
    max_side: +$('maxside').value};
}
function formaUygula(k){
  $('prompt').value = k.prompt || ''; $('negatif').value = k.negative || '';
  if(k.model) animaModelSecimiUygula(k.model);
  $('wf').value = k.workflow === 'video' ? 'video' : 'otomatik';
  $('wf').onchange({target: $('wf')});
  if(k.steps != null) $('steps').value = k.steps;
  if(k.cfg != null) $('cfg').value = k.cfg;
  if(k.sampler) $('sampler').value = k.sampler;
  if(k.width != null) $('w').value = k.width;
  if(k.height != null) $('h').value = k.height;
  if(k.width != null && k.height != null){
    const hedef = k.width + 'x' + k.height;
    const varMi = Array.from($('preset').options).some(o => o.value === hedef);
    $('preset').value = varMi ? hedef : 'ozel';
    presetOranUygula();
  }
  if(k.max_side != null) $('maxside').value = k.max_side;
  window.scrollTo({top: 0, behavior: 'smooth'});
  formKaydet();
}
async function promptListele(){
  try{
    const r = await fetch('/api/promptlar'); const j = await r.json();
    prOnbellek = j.promptlar || [];
    const div = $('prListe'); div.innerHTML = '';
    (j.promptlar || []).forEach(k => {
      const sat = document.createElement('div'); sat.className = 'listeogesi';
      const b = document.createElement('div'); b.className = 'bilgi';
      const ad = document.createElement('b'); ad.textContent = k.ad;
      const alt = document.createElement('span'); alt.className = 'kucuk';
      alt.textContent = (k.prompt || '').slice(0, 80);
      b.appendChild(ad); b.appendChild(alt);
      const uyg = document.createElement('button'); uyg.className = 'kucukbtn'; uyg.textContent = 'Uygula';
      uyg.onclick = () => formaUygula(k);
      const sil = document.createElement('button'); sil.className = 'kucukbtn tehlike'; sil.textContent = 'Sil';
      sil.onclick = async () => {
        if(!confirm('Silinsin mi? ' + k.ad)) return;
        await fetch('/api/promptlar/sil', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ad: k.ad})});
        promptListele();
      };
      sat.appendChild(b); sat.appendChild(uyg); sat.appendChild(sil);
      div.appendChild(sat);
    });
  }catch(e){}
}
/* Sabit promptlar: 1 ve 2 hizli erisim, Diger panelden secilir/sabitlenir. */
function pinAnahtar(n){ return 'pin' + n + '_' + (BEN ? BEN.kullanici : ''); }
function pinOku(n){
  try{ const v = localStorage.getItem(pinAnahtar(n)); return v ? JSON.parse(v) : null; }
  catch(e){ return null; }
}
function pinYaz(n, kayit){
  try{ localStorage.setItem(pinAnahtar(n), JSON.stringify(kayit)); }catch(e){}
}
function pinEtiket(){
  [1, 2].forEach(n => {
    const b = $('pin' + n + 'Btn');
    if(!b) return;
    const k = pinOku(n);
    b.textContent = (k && k.ad) ? (n + ' · ' + String(k.ad).slice(0, 14)) : String(n);
    b.title = (k && k.prompt) ? String(k.prompt).slice(0, 120) : 'Önce Diğer’den sabitle';
  });
}
function pinSecili(){
  const v = $('pinSec').value;
  return prOnbellek.find(x => x.ad === v) || null;
}
$('pin1Btn').onclick = () => {
  const k = pinOku(1);
  if(k) formaUygula(k); else bilgi('Önce Diğer’den bir prompt sabitle');
};
$('pin2Btn').onclick = () => {
  const k = pinOku(2);
  if(k) formaUygula(k); else bilgi('Önce Diğer’den bir prompt sabitle');
};
$('pinDigerBtn').onclick = () => {
  const p = $('pinPanel');
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
  $('pinEklePanel').style.display = 'none';
  if(p.style.display === 'block') pinPanelDoldur();
};
$('pinEkleBtn').onclick = () => {
  const p = $('pinEklePanel');
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
  $('pinPanel').style.display = 'none';
};
function pinPanelDoldur(){
  const s = $('pinSec'); s.innerHTML = '';
  prOnbellek.forEach(k => {
    const o = document.createElement('option');
    o.value = k.ad; o.textContent = k.ad;
    s.appendChild(o);
  });
}
$('pinUygula').onclick = () => { const k = pinSecili(); if(k) formaUygula(k); };
$('pin1Sabitle').onclick = () => {
  const k = pinSecili();
  if(k){ pinYaz(1, k); pinEtiket(); bilgi('1’e sabitlendi: ' + k.ad); }
};
$('pin2Sabitle').onclick = () => {
  const k = pinSecili();
  if(k){ pinYaz(2, k); pinEtiket(); bilgi('2’ye sabitlendi: ' + k.ad); }
};
$('pinEkleKaydet').onclick = async () => {
  const ad = $('pinEkleAd').value.trim(), pr = $('prompt').value;
  if(!ad){ alert('Önce ad yaz.'); return; }
  if(!pr){ alert('Önce yukarıdaki Prompt kutusuna prompt yaz.'); return; }
  const kayit = formdanPreset(ad);
  kayit.prompt = pr;
  await fetch('/api/promptlar', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(kayit)});
  $('pinEkleAd').value = '';
  $('pinEklePanel').style.display = 'none';
  await promptListele();
  formaUygula(kayit);
  uret();
};
/* Geçmiş: kutucuk / alt-alta görünüm + içeride sağ-sol gezinme. */
function gecGorunumUygula(){
  const iz = $('gecIzgara');
  if(!iz) return;
  iz.classList.toggle('akis', gecGorunum === 'akis');
  const b1 = $('gorIzgara'), b2 = $('gorAkis');
  if(b1) b1.classList.toggle('aktif', gecGorunum === 'izgara');
  if(b2) b2.classList.toggle('aktif', gecGorunum === 'akis');
}
function gecGecmisOnizlemeleriniIzle(){
  if(gecOnizlemeObserver) gecOnizlemeObserver.disconnect();
  const images = $('gecIzgara').querySelectorAll('img[data-src]');
  if(window.IntersectionObserver){
    gecOnizlemeObserver = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if(!entry.isIntersecting) return;
        const img = entry.target;
        img.src = img.dataset.src;
        img.removeAttribute('data-src');
        gecOnizlemeObserver.unobserve(img);
      });
    }, {root: $('gecIzgara'), rootMargin: '200px'});
    images.forEach(img => gecOnizlemeObserver.observe(img));
  }else{
    images.forEach(img => { img.src = img.dataset.src; img.removeAttribute('data-src'); });
  }
}
function gecSayfaCiz(){
  const el = $('gecSayfalar');
  if(!el) return;
  el.replaceChildren();
  const sayfaAdet = gecAktifAlbum ?
    Math.max(1, Math.ceil(gecAlbumToplam / 12)) :
    Math.max(1, Math.ceil(gecToplam / 12));
  const seciliSayfa = gecAktifAlbum ? gecAlbumSayfa : gecSayfa;
  const ekle = (yazi, sayfa, secili, aria) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'hayalet kucukbtn' + (secili ? ' aktif' : '');
    b.textContent = yazi; b.setAttribute('aria-label', aria || ('Sayfa ' + sayfa));
    b.disabled = sayfa < 1 || sayfa > sayfaAdet || sayfa === seciliSayfa;
    b.onclick = () => {
      if(gecAktifAlbum) gecmisAlbumAc(gecIdx, null, sayfa);
      else { gecSayfa = sayfa; gecmisYukle(true); }
    };
    el.appendChild(b);
  };
  if(sayfaAdet < 2) return;
  ekle('‹', seciliSayfa - 1, false, 'Önceki sayfa');
  const bas = Math.max(1, Math.min(seciliSayfa - 2, sayfaAdet - 4));
  for(let p = bas; p <= Math.min(sayfaAdet, bas + 4); p++)
    ekle(String(p), p, p === seciliSayfa);
  ekle('›', seciliSayfa + 1, false, 'Sonraki sayfa');
}
async function gecmisYukle(temizle){
  if(temizle) gecSayfa = Math.max(1, gecSayfa);
  const istekNesli = ++gecListeIstekNesli;
  ++gecAlbumIstekNesli;
  ++gecGorselSecimNesli;
  const istekSayfasi = gecSayfa;
  const q = ($('gecArama') && $('gecArama').value || '').trim();
  const favoriler = gecFavoriler;
  const albumeGore = $('historyAlbums').checked;
  try{
    const url = '/api/gecmis?sayfa=' + istekSayfasi + '&adet=12&q=' + encodeURIComponent(q) +
      '&favoriler=' + (favoriler ? '1' : '0') +
      '&albume_gore=' + (albumeGore ? '1' : '0');
    const r = await fetch(url);
    const j = await r.json();
    if(istekNesli !== gecListeIstekNesli || istekSayfasi !== gecSayfa ||
        q !== (($('gecArama') && $('gecArama').value) || '').trim() ||
        favoriler !== gecFavoriler || albumeGore !== $('historyAlbums').checked)
      return null;
    const sayfaAdet = Math.max(1, Math.ceil((j.toplam || 0) / 12));
    if(istekSayfasi > sayfaAdet){ gecSayfa = sayfaAdet; return gecmisYukle(true); }
    const iz = $('gecIzgara');
    ++gecGorselSecimNesli;
    gecAktifAlbum = null; gecAlbumKayitIdx = -1;
    gecListe = j.kayitlar || [];
    gecToplam = j.toplam || 0;
    iz.replaceChildren();
    if(!gecListe.length){
      const bos = document.createElement('div');
      bos.className = 'kucuk gec-bos';
      bos.textContent = q || gecFavoriler ? 'Eşleşen kayıt yok.' : 'Henüz üretim yok.';
      iz.appendChild(bos);
    }
    gecListe.forEach((k, idx) => {
      const oge = document.createElement('div'); oge.className = 'gec-kart' + (k.album ? ' album-kart' : '');
      oge.dataset.kayit = k.kayit || '';
      oge.dataset.dosya = k.dosya || '';
      const bw = +k.width, bh = +k.height;
      /* Oranı CSS değişkeniyle ver; width/height özniteliği ızgarayı şişirip tıklamayı engeller. */
      if(bw > 0 && bh > 0) oge.style.setProperty('--gec-oren', (bw / bh).toFixed(4));
      const btn = document.createElement('button');
      btn.type = 'button'; btn.className = 'gec-kart-ac';
      btn.title = (k.album ? 'Albüm · ' : (k.tur === 'video' ? 'Video · ' : '')) +
        (k.prompt || k.kayit || 'Üretim');
      if(k.tur === 'video'){
        if(k.video_poster_url){
          const im=document.createElement('img');im.dataset.src=k.video_poster_url;
          im.loading='lazy';im.alt='Video kapağı';btn.appendChild(im);
        }else{
          const v = document.createElement('video');
          v.src = k.url; v.preload = 'metadata'; v.muted = true; v.setAttribute('aria-label', 'Video üretimi');
          btn.appendChild(v);
        }
      }else{
        const im = document.createElement('img');
        im.dataset.src = k.url;
        im.loading = 'lazy'; im.alt = k.prompt || 'Üretilen görsel';
        btn.appendChild(im);
      }
      if(k.album){
        const sayi = document.createElement('span');
        sayi.className = 'gec-album-sayi';
        sayi.textContent = k.album_count;
        sayi.setAttribute('aria-label', k.album_count + ' fotoğraf');
        btn.appendChild(sayi);
        const albumEtiket=document.createElement('span');
        albumEtiket.className='gec-album-etiket';
        albumEtiket.textContent=k.prompt||'Üretim albümü';
        btn.appendChild(albumEtiket);
      }
      btn.onclick = () => {
        if(!k.album && !k.album_member &&
            matchMedia('(max-width: 699px)').matches &&
            $('uretimYanKenar').classList.contains('acik')){
          $('uretimYanKapat').click();
        }
        if(k.album) gecmisAlbumAc(idx);
        else if(k.album_member) gecmisAlbumAc(idx, k.dosya);
        else gecmisAcIdx(idx);
      };
      if(k.album || k.tur === 'video'){
        btn.setAttribute('aria-busy', 'true');
        const poster = btn.querySelector('img,video');
        if(poster){
          const yuklendi = () => btn.setAttribute('aria-busy', 'false');
          poster.addEventListener('load', yuklendi, {once: true});
          poster.addEventListener('error', yuklendi, {once: true});
          poster.addEventListener('loadeddata', yuklendi, {once: true});
          if((poster.tagName === 'VIDEO' && poster.readyState >= 2) ||
              (poster.tagName === 'IMG' && poster.complete && poster.naturalWidth > 0)) yuklendi();
        }
      }
      oge.appendChild(btn);
      if(!k.album && k.tur !== 'video')
        oge.appendChild(gecmisFavoriDugmesi(k, async () => {
          if(gecFavoriler){
            gecSayfa = 1;
            const sonuc = await gecmisYukle(true);
            if(!sonuc) gecmisFavoriFiltreGuncelle();
          }else gecmisFavoriFiltreGuncelle();
        }));
      iz.appendChild(oge);
    });
    gecGecmisOnizlemeleriniIzle();
    gecSayfaCiz();
    $('gecDaha').hidden = true;
    return j;
  }catch(e){ return null; }
}
$('gecDaha').onclick = () => { gecSayfa++; gecmisYukle(true); };
$('gecFavoriBtn').onclick = () => {
  gecFavoriler = !gecFavoriler;
  uiAyarKaydet('history_favorites', gecFavoriler);
  $('gecFavoriBtn').setAttribute('aria-pressed', gecFavoriler ? 'true' : 'false');
  $('gecFavoriBtn').textContent = gecFavoriler ? '★' : '☆'; $('gecFavoriBtn').title = gecFavoriler ? 'Favoriler açık' : 'Favoriler';
  gecSayfa = 1; gecmisYukle(true);
};
let gecAramaGecikme = null;
$('gecArama').addEventListener('input', () => {
  if(gecAramaGecikme) clearTimeout(gecAramaGecikme);
  gecAramaGecikme = setTimeout(() => { gecSayfa = 1; gecmisYukle(true); }, 250);
});
$('gecTemizle').onclick = async () => {
  if(!confirm('Geçmişteki TÜM kayıtlar silinsin mi?')) return;
  await fetch('/api/gecmis/temizle', {method: 'POST'});
  gecSayfa = 1; gecmisYukle(true);
};
async function gecmisAcIdx(i, albumIndex){
  if(i < 0 || i >= gecListe.length) return;
  const secimNesli = ++gecGorselSecimNesli;
  const albumNesli = Number.isInteger(albumIndex) ? gecAlbumIstekNesli : 0;
  gecIdx = i;
  let k;
  if(Number.isInteger(albumIndex) && gecAktifAlbum){
    const albumSayfaIndeksi = albumIndex - gecAlbumBaslangic;
    if(albumSayfaIndeksi < 0 || albumSayfaIndeksi >= gecAktifAlbum.length) return;
    gecAlbumKayitIdx = albumIndex;
    k = gecAktifAlbum[albumSayfaIndeksi];
  }else{
    gecAktifAlbum = null; gecAlbumKayitIdx = -1;
    k = gecListe[i];
  }
  let meta = {};
  try{
    const r = await fetch('/gecmis/' + encodeURIComponent(k.kayit));
    if(r.ok) meta = await r.json();
  }catch(e){}
  if(secimNesli !== gecGorselSecimNesli ||
      (Number.isInteger(albumIndex) && albumNesli !== gecAlbumIstekNesli)) return;
  gecmisKaydiGoster(k, meta, i, albumIndex, false);
}
function gecmisKaydiGoster(k, meta, i, albumIndex, medyayiKoru){
  uretimZoom = 1;
  const mount = $('galeri');
  if(!medyayiKoru){
    mount.replaceChildren();
    const media = galeriMedyasiEkle(k.tur === 'video' ? 'video' : 'gorsel', k.url,
                                    meta.prompt || 'Üretilen görsel', k.video_poster_url);
    media.style.transform = 'scale(1)';
  }
  uretimOffsetX = 0; uretimOffsetY = 0; uretimZoom = 1;
  $('uretimGoruntuBos').hidden = true;
  $('uretimGoruntuStage').hidden = false;
  uretimSeciliKayit = k;
  const etiketi = $('uretimGoruntuEtiket');
  const favori = $('uretimGoruntuFavori');
  const albumEtiketi = $('uretimAlbumEtiket');
  const albumAdi = (Number.isInteger(albumIndex) && gecAktifAlbum) || k.album_member ?
    ((gecListe[i] && gecListe[i].prompt) || '') : '';
  const buyutulmus = $('uretimGoruntuStage').classList.contains('tam-ekran');
  etiketi.hidden = buyutulmus || (k.tur === 'video' && !albumAdi);
  favori.hidden = k.tur === 'video';
  favori.disabled = false;
  favori.classList.toggle('secili', !!k.yildiz);
  favori.textContent = k.yildiz ? '★' : '☆';
  favori.title = k.yildiz ? 'Favorilerden çıkar' : 'Favorilere ekle';
  favori.setAttribute('aria-label', favori.title);
  favori.setAttribute('aria-pressed', k.yildiz ? 'true' : 'false');
  albumEtiketi.hidden = !albumAdi;
  albumEtiketi.textContent = albumAdi ? 'Albüm · ' + albumAdi : '';
  favori.onclick = async event => {
    event.stopPropagation();
    favori.disabled = true;
    try{
      await gecmisFavoriDegistir(k);
      if(gecFavoriler && !k.yildiz && !gecAktifAlbum){
        gecSayfa = 1;
        await gecmisYukle(true);
      }else{
        gecmisFavoriFiltreGuncelle();
      }
    }catch(error){
      bilgi('Favori kaydedilemedi: ' + (error.message || error));
    }finally{
      favori.disabled = false;
    }
  };
  $('uretimSeciliBilgi').hidden = false;
  const detay = [];
  if(meta.width && meta.height) detay.push(meta.width + '×' + meta.height);
  if(meta.model) detay.push(({z_image: 'Z-Image', flux2: 'Flux 2 Klein', flux3: 'Flux 3 Klein',
    krea2: 'Krea 2', qwen_image_edit: 'Qwen Image 2.1',
    qwen_image_edit_turbo: 'Qwen Image 2.1 Turbo', illustrious: 'Illustrious', anima: 'Anima Turbo v1.1',
    moody_anima: 'Anima Moody Mix v1.0'})[meta.model] || meta.model);
  if(meta.steps) detay.push(meta.steps + ' step');
  if(meta.seed != null) detay.push('seed ' + meta.seed);
  if(k.tur === 'video' && meta.suresn) detay.push(meta.suresn + ' sn');
  const sureMetni = ms => {
    const sure = Number(ms);
    return !Number.isFinite(sure) || sure < 0 ? '' :
      (sure < 1000 ? sure + ' ms' : (sure / 1000).toFixed(1) + ' sn');
  };
  const olcumler = [];
  if(meta.prompt_ceviri_ms != null) olcumler.push('Türkçe → İngilizce çeviri (' +
    (meta.prompt_ceviri_device || 'yerel') + (meta.prompt_ceviri_model ?
      ' · ' + meta.prompt_ceviri_model : '') + '): ' + sureMetni(meta.prompt_ceviri_ms));
  if(meta.generation_ms != null) olcumler.push('Görsel üretimi: ' + sureMetni(meta.generation_ms));
  if(meta.sure_ms != null) olcumler.push('Toplam işlem: ' + sureMetni(meta.sure_ms));
  const promptGirdi = meta.prompt_tr || meta.prompt_girdi || '';
  const promptCikti = meta.prompt_en || '';
  const promptBilgisi = promptGirdi && promptCikti ?
    '<div class="uretim-detay-promptlar">' +
      '<div class="uretim-detay-prompt-alan"><b>' +
        (meta.prompt_islem === 'prompt_destek' ? 'Prompt Stüdyosu girdisi' : 'Türkçe girilen prompt') +
      '</b><div class="uretim-detay-prompt-metni">' + escHtml(promptGirdi) + '</div></div>' +
      '<div class="uretim-detay-prompt-alan"><b>' +
        (meta.prompt_islem === 'prompt_destek' ? 'Prompt Stüdyosu çıktısı' : 'Görsel modeline gönderilen İngilizce prompt') +
      '</b><div class="uretim-detay-prompt-metni">' + escHtml(promptCikti) + '</div></div>' +
    '</div>' :
    '<div class="uretim-detay-prompt-metni">' + escHtml(meta.prompt || k.prompt || '') + '</div>';
  const gorselKaydi = k.tur !== 'video';
  const tekrarPromptu = meta.prompt_en || meta.prompt || k.prompt || '';
  $('uretimSeciliBilgi').innerHTML =
    '<div class="uretim-detay-ust"><b>' + escHtml(detay.join(' · ') || k.kayit || 'Üretim') + '</b>' +
    '<div class="uretim-detay-aksiyon"><button id="uretimAyniAyar" class="hayalet kucukbtn" type="button">Ayarları yükle</button>' +
    '<button id="uretimSil" class="hayalet kucukbtn" type="button">Sil</button></div></div>' +
    (olcumler.length ? '<div class="uretim-detay-olcum">' + olcumler.map(escHtml).join(' · ') + '</div>' : '') +
    promptBilgisi +
    '<div class="uretim-detay-yeniden">' +
    '<button id="uretimReferansYukle" class="hayalet kucukbtn" type="button"' +
      (gorselKaydi ? '' : ' disabled') + '>Görseli referans olarak yükle</button>' +
    '<button id="uretimTekrar" class="hayalet kucukbtn" type="button"' +
      (gorselKaydi && tekrarPromptu ? '' : ' disabled') +
      '>Yeniden oluştur (aynı promptla)</button></div>' +
    '<div class="uretim-detay-yardim">Her tıklama kuyruğa ayrı iş ekler. Üretim ayarlarındaki mevcut seed kullanılır; -1 ise rastgele kalır.</div>';
  $('uretimAyniAyar').onclick = () => {
    formaUygula(meta); if(meta.seed != null) $('seed').value = meta.seed;
    uretimAyarAc();
  };
  $('uretimReferansYukle').onclick = async () => {
    if(await libAta({url: k.url}, 1)) bilgi('Geçmiş görseli referans olarak yüklendi.');
  };
  $('uretimTekrar').onclick = () => {
    if(!tekrarPromptu || !gorselKaydi) return;
    aktifMenu = 'uretim'; menuUygula();
    uret({prompt: tekrarPromptu, prompt_destek: false, prompt_ceviri: false});
  };
  $('uretimSil').onclick = async () => {
    const r = await fetch('/api/gecmis/sil', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({kayit: k.kayit})});
    if(r.ok){ $('uretimGoruntuStage').hidden = true; $('uretimGoruntuStage').classList.remove('tam-ekran'); document.body.classList.remove('uretim-tam-ekran'); $('uretimSeciliBilgi').hidden = true; $('uretimGoruntuBos').hidden = false; gecmisYukle(true); }
  };
  $('uretimSolTik').onclick = e => { e.stopPropagation(); uretimKaydir(-1); };
  $('uretimSagTik').onclick = e => { e.stopPropagation(); uretimKaydir(1); };
  const seciliKart = Number.isInteger(albumIndex) ?
    albumIndex - gecAlbumBaslangic : i;
  $('gecIzgara').querySelectorAll('.gec-kart').forEach((el, ix) =>
    el.classList.toggle('aktif', ix === seciliKart));
}
function gecmisFavoriDugmeDurumu(dugme, kayit){
  dugme.classList.toggle('secili', !!kayit.yildiz);
  dugme.textContent = kayit.yildiz ? '★' : '☆';
  dugme.title = kayit.yildiz ? 'Favorilerden çıkar' : 'Favorilere ekle';
  dugme.setAttribute('aria-label', dugme.title);
  dugme.setAttribute('aria-pressed', kayit.yildiz ? 'true' : 'false');
}
function gecmisFavoriFiltreGuncelle(){
  const durum = new Map();
  [...gecListe, ...(gecAktifAlbum || [])].forEach(k =>
    durum.set(k.kayit + '|' + k.dosya, k));
  $('gecIzgara').querySelectorAll('.gec-kart').forEach(kart => {
    const k = durum.get(kart.dataset.kayit + '|' + kart.dataset.dosya);
    const star = kart.querySelector('.gec-yildiz');
    if(k && star) gecmisFavoriDugmeDurumu(star, k);
  });
  if(uretimSeciliKayit && $('uretimGoruntuFavori'))
    gecmisFavoriDugmeDurumu($('uretimGoruntuFavori'), uretimSeciliKayit);
}
async function gecmisFavoriDegistir(kayit){
  const kimlik = kayit.kayit + '|' + kayit.dosya;
  if(gecFavoriIstekleri.has(kimlik)) return gecFavoriIstekleri.get(kimlik);
  const promise = (async () => {
    const yeniDurum = !kayit.yildiz;
    const response = await fetch('/api/gecmis/yildiz', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        kayit: kayit.kayit, dosya: kayit.dosya, yildiz: yeniDurum
      })
    });
    const result = await response.json();
    if(!response.ok) throw new Error(result.hata || 'HTTP ' + response.status);
    [...gecListe, ...(gecAktifAlbum || [])].forEach(item => {
      if(item.kayit === kayit.kayit && item.dosya === kayit.dosya)
        item.yildiz = yeniDurum;
    });
    if(uretimSeciliKayit && uretimSeciliKayit.kayit === kayit.kayit &&
        uretimSeciliKayit.dosya === kayit.dosya)
      uretimSeciliKayit.yildiz = yeniDurum;
    gecmisFavoriFiltreGuncelle();
  })();
  gecFavoriIstekleri.set(kimlik, promise);
  try{ await promise; }
  finally{ gecFavoriIstekleri.delete(kimlik); }
}
function gecmisFavoriDugmesi(kayit, yenile){
  const star = document.createElement('button');
  star.type = 'button';
  star.className = 'gec-yildiz';
  gecmisFavoriDugmeDurumu(star, kayit);
  star.onclick = async event => {
    event.stopPropagation();
    if(star.disabled) return;
    star.disabled = true;
    try{
      await gecmisFavoriDegistir(kayit);
      if(typeof yenile === 'function') await yenile();
      gecmisFavoriFiltreGuncelle();
    }catch(error){
      bilgi('Favori kaydedilemedi: ' + (error.message || error));
    }finally{
      star.disabled = false;
    }
  };
  return star;
}
async function gecmisAlbumAc(i, focusFile, pageOverride){
  const album = gecListe[i];
  if(!album || !album.album_id){ await gecmisAcIdx(i); return; }
  const albumNesli = ++gecAlbumIstekNesli;
  const listeNesli = gecListeIstekNesli;
  const gorunumNesli = ++gecGorselSecimNesli;
  const listeImzasi = album.album_id + '|' + album.kayit;
  const albumKimligi = album.album_id;
  try{
    const sayfaBoyu = 12;
    const hedefIndex = Number.isInteger(album.album_index) ? album.album_index : -1;
    const pageCount = Math.max(1, Math.ceil((Number(album.album_count || 0) ||
      (hedefIndex >= 0 ? hedefIndex + 1 : sayfaBoyu)) / sayfaBoyu));
    const hedefSayfa = pageOverride ?? (hedefIndex >= 0 ? Math.floor(hedefIndex / sayfaBoyu) + 1 : 1);
    const sayfa = Math.max(1, Math.min(pageCount, hedefSayfa));
    const response = await fetch('/api/gecmis?album_id=' + encodeURIComponent(albumKimligi) +
      '&sayfa=' + sayfa + '&adet=' + sayfaBoyu);
    const data = await response.json();
    if(!response.ok) throw new Error(data.hata || 'HTTP ' + response.status);
    if(albumNesli !== gecAlbumIstekNesli || listeNesli !== gecListeIstekNesli ||
        gorunumNesli !== gecGorselSecimNesli ||
        !gecListe.some(item => item.album_id + '|' + item.kayit === listeImzasi)) return;
    if(!data.kayitlar?.length) throw new Error('Albümde görüntülenecek fotoğraf bulunamadı.');
    gecAlbumSayfa = sayfa;
    gecAlbumToplam = Number(data.toplam) || Number(album.album_count) || 0;
    gecAlbumBaslangic = (gecAlbumSayfa - 1) * sayfaBoyu;
    gecAktifAlbum = data.kayitlar || [];
    gecIdx = i;
    gecAlbumKayitIdx = -1;
    const localTarget = gecAktifAlbum.findIndex(photo => photo.dosya === focusFile);
    const focusIndex = localTarget >= 0 ? gecAlbumBaslangic + localTarget :
      (Number.isInteger(album.album_index) ? album.album_index : -1);
    const iz = $('gecIzgara');
    iz.replaceChildren();
    const baslik = document.createElement('div');
    baslik.className = 'gec-album-baslik';
    const geri = document.createElement('button');
    geri.className = 'hayalet kucukbtn gec-album-geri';
    geri.type = 'button';
    geri.textContent = '← Albümlere dön';
    geri.onclick = () => {
      gecAktifAlbum = null;
      gecAlbumKayitIdx = -1;
      gecmisYukle(true);
    };
    const ad = document.createElement('strong');
    ad.textContent = album.prompt || 'Albüm';
    const ozet=document.createElement('span');
    ozet.className='gec-album-ozet';
    ozet.textContent=gecAlbumToplam+' fotoğraf · '+sayfa+'/'+pageCount;
    baslik.append(geri, ad, ozet);
    iz.appendChild(baslik);
    const sayfaBaslangic = gecAlbumBaslangic;
    const sayfadakiFotograflar = gecAktifAlbum;
    sayfadakiFotograflar.forEach((foto, pageIndex) => {
      const albumIndex = sayfaBaslangic + pageIndex;
      const kart = document.createElement('div');
      kart.className = 'gec-kart' + (albumIndex === focusIndex ? ' album-hedef' : '');
      kart.dataset.kayit = foto.kayit || '';
      kart.dataset.dosya = foto.dosya || '';
      const bw = +foto.width, bh = +foto.height;
      if(bw > 0 && bh > 0) kart.style.setProperty('--gec-oren', (bw / bh).toFixed(4));
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'gec-kart-ac';
      btn.title = foto.prompt || 'Albüm fotoğrafı';
      if(foto.tur === 'video'){
        if(foto.video_poster_url){
          const image=document.createElement('img');image.dataset.src=foto.video_poster_url;
          image.loading='lazy';image.alt='Video kapağı';btn.appendChild(image);
        }else{
          const video = document.createElement('video');
          video.src = foto.url;
          video.preload = 'metadata';
          video.muted = true;
          btn.appendChild(video);
        }
      }else{
        const image = document.createElement('img');
        image.dataset.src = foto.url;
        image.loading = 'lazy';
        image.alt = foto.prompt || 'Albüm fotoğrafı';
        btn.appendChild(image);
      }
      btn.setAttribute('aria-busy', 'true');
      const poster = btn.querySelector('img,video');
      if(poster){
        const yuklendi = () => btn.setAttribute('aria-busy', 'false');
        poster.addEventListener('load', yuklendi, {once: true});
        poster.addEventListener('error', yuklendi, {once: true});
        poster.addEventListener('loadeddata', yuklendi, {once: true});
        if(poster.complete || poster.readyState >= 2) yuklendi();
      }
      btn.onclick = () => {
        if(matchMedia('(max-width: 699px)').matches && $('uretimYanKenar').classList.contains('acik'))
          $('uretimYanKapat').click();
        gecmisAcIdx(i, albumIndex);
      };
      kart.append(btn, gecmisFavoriDugmesi(foto, () =>
        gecmisAlbumAc(i, null, gecAlbumSayfa)));
      iz.appendChild(kart);
    });
    gecGecmisOnizlemeleriniIzle();
    const pages = $('gecSayfalar');
    pages.replaceChildren();
    const addPageButton = (label, page, disabled, ariaLabel) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'hayalet kucukbtn';
      button.textContent = label;
      button.disabled = disabled;
      button.setAttribute('aria-label', ariaLabel);
      button.onclick = () => {
        gecmisAlbumAc(i, null, page);
      };
      pages.appendChild(button);
    };
    if(pageCount > 1){
      addPageButton('‹', gecAlbumSayfa - 1, gecAlbumSayfa === 1,
        'Albümdeki önceki sayfa');
      for(let page = 1; page <= pageCount; page++){
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'hayalet kucukbtn' +
          (page === gecAlbumSayfa ? ' aktif' : '');
        button.textContent = String(page);
        button.disabled = page === gecAlbumSayfa;
        button.setAttribute('aria-label', 'Albüm sayfası ' + page);
        button.onclick = () => {
          gecmisAlbumAc(i, null, page);
        };
        pages.appendChild(button);
      }
      addPageButton('›', gecAlbumSayfa + 1, gecAlbumSayfa === pageCount,
        'Albümdeki sonraki sayfa');
    }
    $('gecDaha').hidden = true;
    if(focusIndex >= 0){
      const selected = iz.querySelector('.album-hedef');
      if(selected) selected.scrollIntoView({block: 'nearest'});
    }
    return true;
  }catch(e){
    if(albumNesli === gecAlbumIstekNesli && listeNesli === gecListeIstekNesli &&
        gorunumNesli === gecGorselSecimNesli) bilgi('Albüm açılamadı: ' + (e.message || e));
  }
}
async function uretimKaydir(delta){
  if(gecAktifAlbum){
    if(gecAlbumKayitIdx < 0){
      await gecmisAcIdx(gecIdx, gecAlbumBaslangic);
      return;
    }
    const albumIdx = gecAlbumKayitIdx + delta;
    if(albumIdx >= gecAlbumBaslangic &&
        albumIdx < gecAlbumBaslangic + gecAktifAlbum.length){
      await gecmisAcIdx(gecIdx, albumIdx);
      return;
    }
    const albumSayfaAdet = Math.max(1, Math.ceil(gecAlbumToplam / 12));
    const sayfa = gecAlbumSayfa + (delta < 0 ? -1 : 1);
    if(sayfa < 1 || sayfa > albumSayfaAdet){
      gecAktifAlbum = null; gecAlbumKayitIdx = -1;
      await uretimKaydir(delta);
      return;
    }
    if(await gecmisAlbumAc(gecIdx, null, sayfa)){
      const hedef = delta < 0 ? gecAlbumBaslangic + gecAktifAlbum.length - 1 : gecAlbumBaslangic;
      await gecmisAcIdx(gecIdx, hedef);
    }
    return;
  }
  const ix = gecIdx + delta;
  gecAktifAlbum = null; gecAlbumKayitIdx = -1;
  if(ix >= 0 && ix < gecListe.length){
    if(gecListe[ix].album){
      if(await gecmisAlbumAc(ix)) await gecmisAcIdx(ix, gecAlbumBaslangic);
    }
    else await gecmisAcIdx(ix);
    return;
  }
  const sayfaAdet = Math.max(1, Math.ceil(gecToplam / 12));
  const yeni = gecSayfa + (delta < 0 ? -1 : 1);
  if(yeni < 1 || yeni > sayfaAdet) return;
  gecSayfa = yeni;
  const veri = await gecmisYukle(true);
  if(veri && gecListe.length){
    const yeniIdx = delta < 0 ? gecListe.length - 1 : 0;
    if(gecListe[yeniIdx].album){
      if(await gecmisAlbumAc(yeniIdx)) await gecmisAcIdx(yeniIdx, gecAlbumBaslangic);
    }
    else await gecmisAcIdx(yeniIdx);
  }
}
$('uretimGoruntuStage').addEventListener('click', e => {
  if(Date.now() - uretimPinchZamani < 350 || e.target.closest('video,button,.uretim-navigasyon,.uretim-goruntu-etiket')) return;
  const rect = $('uretimGoruntuStage').getBoundingClientRect();
  const x = e.clientX - rect.left;
  if(x < rect.width * 0.32) uretimKaydir(-1);
  else if(x > rect.width * 0.68) uretimKaydir(1);
  else {
    const stage = $('uretimGoruntuStage');
    const opened = !stage.classList.contains('tam-ekran');
    stage.classList.toggle('tam-ekran', opened);
    document.body.classList.toggle('uretim-tam-ekran', opened);
    $('uretimGoruntuEtiket').hidden = opened || !uretimSeciliKayit || uretimSeciliKayit.tur === 'video';
    uretimZoom = 1; uretimOffsetX = 0; uretimOffsetY = 0;
    uretimPinchMesafe = 0; uretimPinchDurum = null; uretimPanDurum = null;
    uretimGoruntuZoomUygula();
  }
});
$('uretimGoruntuStage').addEventListener('touchstart', e => {
  const stage = $('uretimGoruntuStage');
  if(!stage.classList.contains('tam-ekran')) return;
  if(e.touches.length === 2){
    const rect = stage.getBoundingClientRect();
    const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
    const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
    uretimPinchMesafe = Math.max(1, Math.hypot(e.touches[0].clientX-e.touches[1].clientX, e.touches[0].clientY-e.touches[1].clientY));
    uretimPinchDurum = {
      scale: uretimZoom,
      focalX: (midX - rect.left - rect.width / 2 - uretimOffsetX) / uretimZoom,
      focalY: (midY - rect.top - rect.height / 2 - uretimOffsetY) / uretimZoom
    };
    uretimPanDurum = null;
    uretimPinchZamani = Date.now();
  }else if(e.touches.length === 1 && uretimZoom > 1){
    uretimPanDurum = {
      x: e.touches[0].clientX, y: e.touches[0].clientY,
      offsetX: uretimOffsetX, offsetY: uretimOffsetY
    };
  }else if(e.touches.length === 1){
    uretimPanDurum = null;
  }
}, {passive: false});
$('uretimGoruntuStage').addEventListener('touchmove', e => {
  const stage = $('uretimGoruntuStage');
  if(!stage.classList.contains('tam-ekran')) return;
  const rect = stage.getBoundingClientRect();
  if(e.touches.length === 2 && uretimPinchMesafe && uretimPinchDurum){
    e.preventDefault();
    const d = Math.max(1, Math.hypot(e.touches[0].clientX-e.touches[1].clientX, e.touches[0].clientY-e.touches[1].clientY));
    const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
    const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
    uretimZoom = Math.max(1, Math.min(5, uretimPinchDurum.scale * d / uretimPinchMesafe));
    uretimOffsetX = midX - rect.left - rect.width / 2 - uretimPinchDurum.focalX * uretimZoom;
    uretimOffsetY = midY - rect.top - rect.height / 2 - uretimPinchDurum.focalY * uretimZoom;
    uretimGoruntuZoomUygula();
    uretimPinchZamani = Date.now();
  }else if(e.touches.length === 1 && uretimPanDurum && uretimZoom > 1){
    e.preventDefault();
    uretimOffsetX = uretimPanDurum.offsetX + e.touches[0].clientX - uretimPanDurum.x;
    uretimOffsetY = uretimPanDurum.offsetY + e.touches[0].clientY - uretimPanDurum.y;
    uretimGoruntuZoomUygula();
    uretimPinchZamani = Date.now();
  }
}, {passive: false});
$('uretimGoruntuStage').addEventListener('touchend', e => {
  if(!$('uretimGoruntuStage').classList.contains('tam-ekran')) return;
  if(e.touches.length < 2){
    uretimPinchMesafe = 0; uretimPinchDurum = null;
  }
  if(e.touches.length === 1 && uretimZoom > 1){
    uretimPanDurum = {
      x: e.touches[0].clientX, y: e.touches[0].clientY,
      offsetX: uretimOffsetX, offsetY: uretimOffsetY
    };
  }else if(!e.touches.length) uretimPanDurum = null;
}, {passive: true});
$('uretimGoruntuStage').addEventListener('touchcancel', e => {
  if(!$('uretimGoruntuStage').classList.contains('tam-ekran')) return;
  uretimPinchMesafe = 0; uretimPinchDurum = null; uretimPanDurum = null;
}, {passive: true});
function uretimGoruntuZoomUygula(){
  const stage = $('uretimGoruntuStage');
  const im = $('uretimGoruntuMount').querySelector('.uretim-goruntu');
  if(!im) return;
  const maxX = Math.max(0, (im.offsetWidth * uretimZoom - stage.clientWidth) / 2);
  const maxY = Math.max(0, (im.offsetHeight * uretimZoom - stage.clientHeight) / 2);
  uretimOffsetX = Math.max(-maxX, Math.min(maxX, uretimOffsetX));
  uretimOffsetY = Math.max(-maxY, Math.min(maxY, uretimOffsetY));
  im.style.transform = 'translate3d(' + uretimOffsetX + 'px,' + uretimOffsetY + 'px,0) scale(' + uretimZoom + ')';
}
function uretimGoruntuOlc(){
  const stage = $('uretimGoruntuStage');
  if(!stage || stage.hidden) return;
  const rect = stage.getBoundingClientRect();
  const edge = Math.max(1, Math.floor(Math.min(rect.width, rect.height)));
  stage.style.setProperty('--uretim-goruntu-kenar', edge + 'px');
}
if(window.ResizeObserver){
  const uretimOlcuObserver = new ResizeObserver(uretimGoruntuOlc);
  uretimOlcuObserver.observe($('uretimGoruntuStage'));
}
document.addEventListener('keydown', e => {
  if(aktifMenu !== 'uretim' || $('uretimGoruntuStage').hidden) return;
  if(e.target && e.target.closest && e.target.closest('input,textarea,select,button,[contenteditable="true"]')) return;
  if(e.key === 'ArrowLeft') uretimKaydir(-1);
  if(e.key === 'ArrowRight') uretimKaydir(1);
  if(e.key === 'Escape'){
    $('uretimGoruntuStage').classList.remove('tam-ekran');
    $('uretimGoruntuEtiket').hidden = !uretimSeciliKayit || uretimSeciliKayit.tur === 'video';
    document.body.classList.remove('uretim-tam-ekran');
    uretimZoom = 1; uretimOffsetX = 0; uretimOffsetY = 0;
    uretimPinchMesafe = 0; uretimPinchDurum = null; uretimPanDurum = null;
    uretimGoruntuZoomUygula();
  }
});
$('gorIzgara').onclick = () => {
  gecGorunum = 'izgara';
  uiAyarKaydet('history_view', gecGorunum);
  gecGorunumUygula();
};
$('gorAkis').onclick = () => {
  gecGorunum = 'akis';
  uiAyarKaydet('history_view', gecGorunum);
  gecGorunumUygula();
};
/* Ayarlar */
const uiAyarAlanlari = {
  ps_creativity: 'psYaraticilik',
  ps_language: 'psDil',
  ps_prompt_profile: 'psPromptProfile',
  ps_bbox_mode: 'psFlux3BboxMode',
  monitor_auto_refresh: 'izlemeOtomatik',
  log_source: 'logKaynak',
  log_lines: 'logSatir',
  history_albums: 'historyAlbums',
  continuous_generation: 'continuousGeneration',
  auto_open_result: 'autoOpenResult',
  cinematic_mode: 'cinematicMode'
};
let sinemaObserver = null, sinemaZamanlayici = null;
const sinemaDinlenenGorseller = new WeakSet();
function sinemaTemasiniSifirla(){
  const root = document.documentElement;
  root.removeAttribute('data-cinematic-mode');
  root.removeAttribute('data-cinematic-tone');
  ['--brand', '--brand2', '--cinematic-glow', '--cinematic-background',
    '--cinematic-dalga1', '--cinematic-dalga2', '--cinematic-dalga3'].forEach(key =>
    root.style.removeProperty(key));
}
/* Gorseldeki baskin renkleri ton farkina gore ayirarak dondurur.
   Beyaz/krem/siyah gibi notr icerikler de toplanir (beyaz-siyah gorsellerde
   palet bos kalip mavi varsayilana dusmesin). Donus: {palet, doygunluk}.
   doygunluk < 0.18 ise gorsel notr (beyaz-krem-siyah) sayilir. */
function sinemaGorselPaleti(img){
  if(!img.complete || !img.naturalWidth || img.naturalWidth < 32) return null;
  const canvas = document.createElement('canvas');
  canvas.width = 48; canvas.height = 48;
  const context = canvas.getContext('2d', {willReadFrequently: true});
  if(!context) return null;
  try{
    context.drawImage(img, 0, 0, canvas.width, canvas.height);
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const bins = new Map();
    let toplamDoygunluk = 0, sayac = 0;
    for(let i = 0; i < data.length; i += 4){
      if(data[i + 3] < 180) continue;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const high = Math.max(r, g, b), low = Math.min(r, g, b);
      const saturation = high ? (high - low) / high : 0;
      toplamDoygunluk += saturation; sayac++;
      const key = [r >> 4, g >> 4, b >> 4].join(',');
      let bin = bins.get(key);
      if(!bin){ bin = {score: 0, count: 0, r: 0, g: 0, b: 0}; bins.set(key, bin); }
      /* Yogunluk = piksel sayisi; doygun tonlar hafif one cikar ama
         notr tonlar (beyaz, krem, siyah) asla elenmez. */
      bin.score += 1 + saturation * 1.2;
      bin.count++;
      bin.r += r; bin.g += g; bin.b += b;
    }
    if(!sayac) return null;
    const adaylar = [];
    bins.forEach(bin => { if(bin.count) adaylar.push(bin); });
    adaylar.sort((a, b) => b.score - a.score);
    const palet = [];
    for(const bin of adaylar){
      const rgb = [bin.r / bin.count, bin.g / bin.count, bin.b / bin.count];
      const ton = sinemaRgbHsl(rgb)[0];
      const uzak = palet.every(kaynak => {
        const fark = Math.abs(sinemaRgbHsl(kaynak)[0] - ton);
        return Math.min(fark, 360 - fark) > 38;
      });
      if(!uzak) continue;
      palet.push(rgb);
      if(palet.length === 5) break;
    }
    return {palet, doygunluk: toplamDoygunluk / sayac};
  }catch(error){
    if(error && error.name === 'SecurityError') return null;
    throw error;
  }
}
/* Paletten uc dalga rengi: varsa ayrinti tonlari, yoksa ilk tonden
   saga/sola acilan tonlar. Notr (beyaz-krem-siyah) gorsellerde krem/siyah
   tonlari yogun kullanilir; dalga zemini bozmaz. */
function sinemaDalgaRenkleri(palet, mode, notr){
  const kaynak = palet.length ? palet : [[52, 96, 158]];
  const ilk = sinemaRgbHsl(kaynak[0]);
  const adim = [0, 34, -30];
  const isiklik = notr ? [22, 26, 18] : (mode === 'soft' ? [19, 23, 16] : [26, 32, 23]);
  const tavanSat = notr ? 0.14 : (mode === 'soft' ? 0.48 : 0.72);
  return adim.map((kaydirma, i) => {
    const hsl = sinemaRgbHsl(kaynak[Math.min(i, kaynak.length - 1)]);
    const ton = i < kaynak.length ? hsl[0] : (ilk[0] + kaydirma + 360) % 360;
    const sat = notr ? Math.min(tavanSat, hsl[1] * 0.5) :
      Math.max(0.16, Math.min(tavanSat, hsl[1] * (mode === 'soft' ? 0.7 : 0.9)));
    return 'hsl(' + Math.round(ton) + ' ' + Math.round(sat * 100) + '% ' + isiklik[i] + '%)';
  });
}
function sinemaRgbHsl(rgb){
  let r = rgb[0] / 255, g = rgb[1] / 255, b = rgb[2] / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const delta = max - min;
  let h = 0, s = 0, l = (max + min) / 2;
  if(delta){
    s = delta / (1 - Math.abs(2 * l - 1));
    if(max === r) h = ((g - b) / delta) % 6;
    else if(max === g) h = (b - r) / delta + 2;
    else h = (r - g) / delta + 4;
    h = (h * 60 + 360) % 360;
  }
  return [Math.round(h), s, l];
}
function sinemaRenkTemasiUygula(){
  const mode = $('cinematicMode').value;
  if(mode === 'off'){ sinemaTemasiniSifirla(); return; }
  const candidates = Array.from(document.querySelectorAll('img')).map(img => {
    const rect = img.getBoundingClientRect();
    const style = getComputedStyle(img);
    return {img, area: rect.width * rect.height,
      visible: rect.width >= 48 && rect.height >= 48 && rect.bottom > 0 &&
        rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth &&
        style.display !== 'none' && style.visibility !== 'hidden'};
  }).filter(item => item.visible && item.area > 0)
    .sort((a, b) => b.area - a.area);
  let palet = [], doygunluk = 0.5;
  for(const candidate of candidates){
    const sonuc = sinemaGorselPaleti(candidate.img);
    if(sonuc && sonuc.palet.length){ palet = sonuc.palet; doygunluk = sonuc.doygunluk; break; }
  }
  const notr = doygunluk < 0.18;
  if(!palet.length) palet = [notr ? [236, 230, 218] : (mode === 'soft' ? [48, 76, 124] : [58, 112, 196])];
  const rgb = palet[0];
  const [hue, sourceSaturation] = sinemaRgbHsl(rgb);
  /* Notr (beyaz-krem-siyah) gorsellerde tema YOGUN olmali: krem isik,
     koyu siyah zemin, belirgin vinyet. Renkli gorsellerde eski davranis. */
  const saturation = notr ?
    Math.max(0.10, Math.min(0.22, sourceSaturation * 0.8)) :
    (mode === 'soft' ?
      Math.max(0.28, Math.min(0.52, sourceSaturation * 0.62)) :
      Math.max(0.55, Math.min(0.88, sourceSaturation * 1.12)));
  const lightness = notr ? 88 : (mode === 'soft' ? 52 : 56);
  const brand = 'hsl(' + hue + ' ' + Math.round(saturation * 100) + '% ' + lightness + '%)';
  const darker = 'hsl(' + hue + ' ' + Math.round(saturation * 100) + '% ' +
    Math.max(24, lightness - 13) + '%)';
  const glow = notr ?
    'hsl(' + hue + ' ' + Math.round(saturation * 100) + '% 92%)' :
    'hsl(' + hue + ' ' + Math.round(saturation * 70) + '% 17%)';
  const background = notr ?
    'hsl(' + hue + ' ' + Math.round(saturation * 100) + '% 6%)' :
    'hsl(' + hue + ' ' + Math.round(Math.max(0.18,
      Math.min(mode === 'soft' ? 0.6 : 0.76, sourceSaturation * (mode === 'soft' ? 0.68 : 0.78))) * 100) + '% ' +
    (mode === 'soft' ? '11%' : '14%') + ')';
  const dalgalar = sinemaDalgaRenkleri(palet, mode, notr);
  const root = document.documentElement;
  root.dataset.cinematicMode = mode;
  if(notr) root.dataset.cinematicTone = 'notr';
  else root.removeAttribute('data-cinematic-tone');
  root.style.setProperty('--brand', brand);
  root.style.setProperty('--brand2', darker);
  root.style.setProperty('--cinematic-glow', glow);
  root.style.setProperty('--cinematic-background', background);
  root.style.setProperty('--cinematic-dalga1', dalgalar[0]);
  root.style.setProperty('--cinematic-dalga2', dalgalar[1]);
  root.style.setProperty('--cinematic-dalga3', dalgalar[2]);
}
function sinemaRenkTemasiGuncelle(){
  if(sinemaZamanlayici) clearTimeout(sinemaZamanlayici);
  sinemaZamanlayici = setTimeout(() => {
    sinemaZamanlayici = null;
    sinemaRenkTemasiUygula();
  }, 80);
}
function sinemaGorseliIzle(img){
  if(sinemaDinlenenGorseller.has(img)) return;
  sinemaDinlenenGorseller.add(img);
  img.addEventListener('load', sinemaRenkTemasiGuncelle);
}
function sinemaTemasiIzlemeyiBaslat(){
  document.querySelectorAll('img').forEach(sinemaGorseliIzle);
  if(window.MutationObserver && document.body){
    sinemaObserver = new MutationObserver(records => {
      records.forEach(record => {
        if(record.type === 'attributes' && record.target.tagName === 'IMG')
          sinemaGorseliIzle(record.target);
        record.addedNodes.forEach(node => {
          if(node.nodeType !== Node.ELEMENT_NODE) return;
          if(node.tagName === 'IMG') sinemaGorseliIzle(node);
          node.querySelectorAll('img').forEach(sinemaGorseliIzle);
        });
      });
      sinemaRenkTemasiGuncelle();
    });
    sinemaObserver.observe(document.body, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['src']
    });
  }
  sinemaRenkTemasiGuncelle();
}
let uiAyarDurum = {};
let uiAyarYamasi = {};
let uiAyarZamanlayici = null;
let uiAyarKaydediyor = false;
let uiAyarKayitBekliyor = false;
function uiAyarOku(key){
  if(key === 'history_view') return gecGorunum;
  if(key === 'history_favorites') return gecFavoriler;
  if(key === 'history_width'){
    const handle = $('uretimYanBoyut');
    return +(handle ? handle.getAttribute('aria-valuenow') : 280);
  }
  if(key === 'active_menu') return aktifMenu;
  const el = $(uiAyarAlanlari[key]);
  if(!el) return undefined;
  if(el.type === 'checkbox') return el.checked;
  if(el.type === 'number') return Number(el.value);
  return el.value;
}
function uiAyarKaydet(key, value){
  if(value === undefined || (typeof value === 'number' && !Number.isFinite(value))) return;
  uiAyarDurum[key] = value;
  uiAyarYamasi[key] = value;
  uiAyarKayitBekliyor = true;
  if(uiAyarZamanlayici) clearTimeout(uiAyarZamanlayici);
  uiAyarZamanlayici = setTimeout(uiAyarKaydetSimdi, 350);
}
async function uiAyarKaydetSimdi(){
  uiAyarZamanlayici = null;
  if(!BEN || uiAyarKaydediyor || !uiAyarKayitBekliyor) return;
  uiAyarKaydediyor = true;
  uiAyarKayitBekliyor = false;
  const yama = uiAyarYamasi;
  uiAyarYamasi = {};
  try{
    const r = await ayarlarGuncelle({ui: yama});
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
  }catch(e){
    bilgi('Arayüz ayarı kaydedilemedi: ' + (e.message || e));
  }finally{
    uiAyarKaydediyor = false;
    if(uiAyarKayitBekliyor && BEN)
      uiAyarZamanlayici = setTimeout(uiAyarKaydetSimdi, 100);
  }
}
function uiAyarlariniUygula(ayarlar){
  gecGorunum = 'izgara';
  gecFavoriler = false;
  aktifMenu = 'uretim';
  Object.keys(uiAyarAlanlari).forEach(key => {
    const el = $(uiAyarAlanlari[key]);
    if(!el) return;
    if(el.type === 'checkbox') el.checked = key === 'auto_open_result';
    else if(el.type === 'number')
      el.value = key === 'ps_creativity' ? '0.7' :
        (key === 'log_lines' ? '200' : '');
    else if(key === 'ps_language') el.value = 'en';
    else if(key === 'ps_prompt_profile') el.value = 'auto';
    else if(key === 'log_source') el.value = 'llama';
    else if(key === 'cinematic_mode') el.value = 'off';
  });
  $('gecFavoriBtn').setAttribute('aria-pressed', 'false');
  $('gecFavoriBtn').textContent = '☆';
  $('gecFavoriBtn').title = 'Favoriler';
  if(uretimGecmisGenislikUygula) uretimGecmisGenislikUygula(280);
  Object.keys(uiAyarAlanlari).forEach(key => {
    if(!Object.prototype.hasOwnProperty.call(ayarlar, key)) return;
    const el = $(uiAyarAlanlari[key]), value = ayarlar[key];
    if(!el) return;
    if(el.type === 'checkbox' && typeof value === 'boolean') el.checked = value;
    else if(el.type === 'number' && Number.isFinite(Number(value))) el.value = value;
    else if(el.tagName === 'SELECT' &&
      Array.from(el.options).some(option => option.value === String(value)))
      el.value = String(value);
  });
  if(ayarlar.history_view === 'izgara' || ayarlar.history_view === 'akis')
    gecGorunum = ayarlar.history_view;
  if(typeof ayarlar.history_favorites === 'boolean'){
    gecFavoriler = ayarlar.history_favorites;
    $('gecFavoriBtn').setAttribute('aria-pressed', gecFavoriler ? 'true' : 'false');
    $('gecFavoriBtn').textContent = gecFavoriler ? '★' : '☆';
    $('gecFavoriBtn').title = gecFavoriler ? 'Favoriler açık' : 'Favoriler';
  }
  if(Number.isFinite(Number(ayarlar.history_width)) &&
      uretimGecmisGenislikUygula)
    uretimGecmisGenislikUygula(Number(ayarlar.history_width));
  if(typeof ayarlar.active_menu === 'string') aktifMenu = ayarlar.active_menu;
  gecGorunumUygula();
  psModGuncelle();
  menuUygula();
  if($('izlemeOtomatik').onchange) $('izlemeOtomatik').onchange();
  sinemaRenkTemasiGuncelle();
}
Object.keys(uiAyarAlanlari).forEach(key => {
  const el = $(uiAyarAlanlari[key]);
  if(!el) return;
  const kaydet = () => uiAyarKaydet(key, uiAyarOku(key));
  el.addEventListener('change', kaydet);
  if(el.type === 'number') el.addEventListener('input', kaydet);
});
$('historyAlbums').addEventListener('change', () => {
  gecSayfa = 1;
  gecmisYukle(true);
});
$('continuousGeneration').addEventListener('change', () => {
  if(!$('continuousGeneration').checked){
    uretimKesintisizAktif = false; uretimKesintisizIstek = null;
  }
});
$('cinematicMode').addEventListener('change', sinemaRenkTemasiGuncelle);
document.querySelectorAll('#menuNav button').forEach(btn =>
  btn.addEventListener('click', () => uiAyarKaydet('active_menu', aktifMenu)));
document.querySelectorAll('[data-ayar-hedef]').forEach(button=>button.addEventListener('click',()=>{
  const target=$(button.dataset.ayarHedef);if(target) target.scrollIntoView({behavior:'smooth',block:'start'});
}));
function formUygulaKayitli(f){
  if(!f || typeof f !== 'object') return;
  const koy = (id, v) => { if(v !== undefined && v !== null && $(id)) $(id).value = v; };
  koy('prompt', f.prompt); koy('negatif', f.negatif);
  if($('runpodImageModel') && typeof f.image_model === 'string' &&
      Array.from($('runpodImageModel').options).some(option =>
        option.value === f.image_model && !option.disabled))
    $('runpodImageModel').value = f.image_model;
  if($('flux3BboxMode') && typeof f.flux3_bbox_mode === 'boolean')
    $('flux3BboxMode').checked = f.flux3_bbox_mode;
  if(Array.isArray(f.loras)) runpodLoraSaved=f.loras.filter(item=>item&&typeof item.id==='string');
  koy('w', f.width); koy('h', f.height); koy('maxside', f.max_side);
  koy('cfg', f.cfg); koy('sampler', f.sampler);
  if(f.max_olcek !== undefined && $('maxolcek')) $('maxolcek').checked = !!f.max_olcek;
  if($('animaModel')){
    const seciliAnima = f.moody_anima_model === true ||
      (f.moody_anima_model === undefined && f.model === 'moody_anima') ?
        'moody_anima' :
        (f.anima_model !== undefined ? (f.anima_model ? 'anima' : '') :
          (f.model === 'anima' ? 'anima' : ''));
    animaModelSecimiUygula(seciliAnima);
  }
  koy('vmax', f.video_max_side); koy('steps', f.steps); koy('seed', f.seed);
  koy('vsure', f.suresn); koy('vfps', f.fps);
  koy('videoProfile', f.video_profile === 'fast' ? 'quality' : f.video_profile);
  if($('videoModel') && ['wan22','video'].includes(f.video_model_instance))
    $('videoModel').value = f.video_model_instance;
  if(f.workflow && $('wf')){ $('wf').value = f.workflow === 'video' ? 'video' : 'otomatik'; try{ $('wf').onchange({target: $('wf')}); }catch(e){} }
  if(f.width != null && f.height != null && $('preset')){
    const hedef = f.width + 'x' + f.height;
    const varMi = Array.from($('preset').options).some(o => o.value === hedef);
    $('preset').value = varMi ? hedef : 'ozel';
  }
  if(f.aspect_ratio === null) kilitOran = null;
  else if(Number.isFinite(Number(f.aspect_ratio)) && Number(f.aspect_ratio) > 0)
    kilitOran = Number(f.aspect_ratio);
  else if(f.width > 0 && f.height > 0)
    kilitOran = Number(f.width) / Number(f.height);
  try{ cozunurlukGorunumu(); oranKilitGuncelle(); uretimModGuncelle(); menuUygula(); }catch(e){}
}
function formPaketle(){
  const v = formVerisi();
  return {prompt: v.prompt, negatif: v.negative, model: v.model,
    image_model: v.image_model, flux3_bbox_mode: v.flux3_bbox_mode,
    width: v.width, height: v.height,
    anima_model: $('animaModel').checked,
    moody_anima_model: $('moodyAnimaModel').checked,
    max_side: v.max_side, max_olcek: v.max_olcek, video_max_side: v.video_max_side,
    steps: v.steps, cfg: v.cfg, sampler: v.sampler, seed: v.seed,
    workflow: v.workflow, suresn: v.suresn, fps: v.fps,
    video_profile: v.video_profile, video_model_instance: v.video_model_instance, loras: v.loras,
    aspect_ratio: kilitOran};
}
async function formSimdiKaydet(){
  if(formKayitSayac){ clearTimeout(formKayitSayac); formKayitSayac = null; }
  try{
    const r = await ayarlarGuncelle({form: formPaketle()});
    if(!r.ok) throw new Error('HTTP ' + r.status);
  }catch(e){ bilgi('Üretim ayarı kaydedilemedi: ' + (e.message || e)); }
}
let formKayitSayac = null;
function formKaydet(){
  if(formKayitSayac) clearTimeout(formKayitSayac);
  formKayitSayac = setTimeout(formSimdiKaydet, 800);
}
let ayarlarYazmaKuyrugu = Promise.resolve();
function ayarlarGuncelle(govde){
  const nesil = oturumNesli;
  const govdeMetni = JSON.stringify(govde);
  const gonder = () => {
    if(nesil !== oturumNesli || !BEN) throw new Error('Oturum değişti');
    return fetch('/api/ayarlar', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: govdeMetni});
  };
  const istek = ayarlarYazmaKuyrugu.then(gonder, gonder);
  ayarlarYazmaKuyrugu = istek.then(() => undefined, () => undefined);
  return istek;
}
async function ayarYukle(){
  try{
    const r = await fetch('/api/ayarlar'); const j = await r.json();
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    const ayarlar = j.ayarlar || {};
    const oncekiFavoriSuze = gecFavoriler;
    const oncekiAlbumeGore = $('historyAlbums').checked;
    uiAyarDurum = Object.assign({}, ayarlar.ui || {});
    uiAyarlariniUygula(uiAyarDurum);
    if(gecFavoriler !== oncekiFavoriSuze ||
        $('historyAlbums').checked !== oncekiAlbumeGore){
      gecSayfa = 1;
      gecmisYukle(true);
    }
    if(aktifMenu === 'studyo'){
      psGecmisYukle(); psModelDurumuYukle();
    }else if(aktifMenu === 'model'){
      sohbetKayitYukle();
    }else if(aktifMenu === 'izleme'){
      izlemeYukle(); logYukle();
    }
    $('limitInput').value = (j.ayarlar && j.ayarlar.gecmis_limiti) || 0;
    if(j.ayarlar && j.ayarlar.llm_ctx){
      sohbetBaglamBoyutu = j.ayarlar.llm_ctx;
      if($('llmCtx')) $('llmCtx').value = j.ayarlar.llm_ctx;
      sohbetBaglamGuncelle(null);
    }
    promptDestekYaraticilik = +(j.ayarlar && j.ayarlar.llm_prompt_destek_yaraticilik || 0.7);
    promptDestekUzunluk = (j.ayarlar && j.ayarlar.llm_prompt_destek_uzunluk) || 'orta';
    $('promptDestekYaraticilik').value = promptDestekYaraticilik;
    $('promptDestekUzunluk').value = promptDestekUzunluk;
    uretimVarsayilan = {
      steps: +(j.ayarlar && j.ayarlar.uretim_varsayilan_steps || 9),
      seed: +(j.ayarlar && j.ayarlar.uretim_varsayilan_seed != null ? j.ayarlar.uretim_varsayilan_seed : -1),
      max_side: +(j.ayarlar && j.ayarlar.uretim_varsayilan_max_side || 1536)
    };
    $('uretimVarsayilanSteps').value = uretimVarsayilan.steps;
    $('uretimVarsayilanSeed').value = uretimVarsayilan.seed;
    $('uretimVarsayilanMaxSide').value = uretimVarsayilan.max_side;
    if($('psDefaultSteps')) $('psDefaultSteps').value = uretimVarsayilan.steps;
    if($('psDefaultSeed')) $('psDefaultSeed').value = uretimVarsayilan.seed;
    if($('psDefaultSide')) $('psDefaultSide').value = uretimVarsayilan.max_side;
    $('uretimPromptDestek').checked = !!j.ayarlar.uretim_prompt_destek;
    $('uretimPromptDuzenle').checked = !!j.ayarlar.uretim_prompt_duzenle;
    $('uretimPromptCeviri').checked = !!j.ayarlar.uretim_prompt_ceviri;
    if(j.ayarlar) formUygulaKayitli(j.ayarlar.form);
    if(RUNPOD_WEBUI) await runpodLoraYukle();
  }catch(e){ bilgi('Kullanıcı ayarları yüklenemedi: ' + (e.message || e)); }
  try{
    const r2 = await fetch('/api/kasa/durum'); const k = await r2.json();
    if(k && k.bekleme_dk) $('beklemeInput').value = k.bekleme_dk;
  }catch(e){}
}
$('limitKaydet').onclick = async () => {
  try{
    const r = await ayarlarGuncelle({gecmis_limiti: +$('limitInput').value});
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    gecSayfa = 1; gecmisYukle(true);
    bilgi('Kayıt sınırı kaydedildi.');
  }catch(e){ bilgi('Kayıt sınırı kaydedilemedi: ' + (e.message || e)); }
};
$('promptDestekKaydet').onclick = async () => {
  const bilgiEl = $('promptDestekBilgi');
  try{
    const body = {llm_prompt_destek_yaraticilik: +$('promptDestekYaraticilik').value,
      llm_prompt_destek_uzunluk: $('promptDestekUzunluk').value};
    const r = await ayarlarGuncelle(body);
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || r.status);
    promptDestekYaraticilik = +$('promptDestekYaraticilik').value;
    promptDestekUzunluk = $('promptDestekUzunluk').value;
    bilgiEl.textContent = 'Prompt desteği varsayılanları kaydedildi.';
  }catch(e){ bilgiEl.textContent = 'HATA: ' + e.message; }
};
$('uretimVarsayilanKaydet').onclick = async () => {
  const bilgiEl = $('uretimVarsayilanBilgi');
  try{
    const body = {uretim_varsayilan_steps: +$('uretimVarsayilanSteps').value,
      uretim_varsayilan_seed: +$('uretimVarsayilanSeed').value,
      uretim_varsayilan_max_side: +$('uretimVarsayilanMaxSide').value};
    const r = await ayarlarGuncelle(body);
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || r.status);
    uretimVarsayilan = {steps: body.uretim_varsayilan_steps, seed: body.uretim_varsayilan_seed, max_side: body.uretim_varsayilan_max_side};
    bilgiEl.textContent = 'Hızlı üretim varsayılanları kaydedildi.';
  }catch(e){ bilgiEl.textContent = 'HATA: ' + e.message; }
};
$('psDefaultsSave').onclick = async () => {
  const status=$('psDefaultsStatus'), button=$('psDefaultsSave');button.disabled=true;
  try{
    const body={uretim_varsayilan_steps:+$('psDefaultSteps').value,
      uretim_varsayilan_seed:+$('psDefaultSeed').value,
      uretim_varsayilan_max_side:+$('psDefaultSide').value};
    const r=await ayarlarGuncelle(body), j=await r.json().catch(()=>({}));
    if(!r.ok) throw new Error(j.hata||'HTTP '+r.status);
    uretimVarsayilan={steps:body.uretim_varsayilan_steps,seed:body.uretim_varsayilan_seed,max_side:body.uretim_varsayilan_max_side};
    $('uretimVarsayilanSteps').value=body.uretim_varsayilan_steps;$('uretimVarsayilanSeed').value=body.uretim_varsayilan_seed;$('uretimVarsayilanMaxSide').value=body.uretim_varsayilan_max_side;
    status.textContent='Kaydedildi.';
  }catch(error){status.textContent='Hata: '+error.message;}
  finally{button.disabled=false;}
};
async function uretimPromptSecenekleriniKaydet(degisiklik){
  if(degisiklik === 'ceviri' && $('uretimPromptCeviri').checked)
    $('uretimPromptDestek').checked = false;
  if(degisiklik === 'destek' && $('uretimPromptDestek').checked)
    $('uretimPromptCeviri').checked = false;
  try{
    const r = await ayarlarGuncelle({
        uretim_prompt_destek: $('uretimPromptDestek').checked,
        uretim_prompt_ceviri: $('uretimPromptCeviri').checked,
        uretim_prompt_duzenle: $('uretimPromptDuzenle').checked
      });
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || r.status);
    $('promptDestekBilgi').textContent = 'Prompt Stüdyosu desteği ' +
      ($('uretimPromptDestek').checked ? 'açık' : 'kapalı') + '.';
    $('uretimPromptCeviriBilgi').textContent = $('uretimPromptCeviri').checked ?
      'Çeviri yerel modelle yapılır; Türkçe prompt alanı değiştirilmez.' :
      'Türkçe prompt çevirisi kapalı.';
  }catch(e){
    const bilgi = degisiklik === 'ceviri' ? $('uretimPromptCeviriBilgi') : $('promptDestekBilgi');
    bilgi.textContent = 'Ayar kaydedilemedi: ' + e.message;
  }
}
let promptSecenekKayitKuyrugu = Promise.resolve();
function promptSecenekKaydetSirali(degisiklik){
  const istek = promptSecenekKayitKuyrugu.then(
    () => uretimPromptSecenekleriniKaydet(degisiklik),
    () => uretimPromptSecenekleriniKaydet(degisiklik));
  promptSecenekKayitKuyrugu = istek.then(() => undefined, () => undefined);
  return istek;
}
$('uretimPromptDestek').onchange = () => promptSecenekKaydetSirali('destek');
$('uretimPromptCeviri').onchange = () => promptSecenekKaydetSirali('ceviri');
$('uretimPromptDuzenle').onchange = () => promptSecenekKaydetSirali('duzenle');
const ayarDegisiklikZamanlayicilari = {};
const ayarDegisiklikIslemleri = {};
function ayarDegisiklikIsleminiBaslat(dugmeId){
  const dugme = $(dugmeId);
  const calistir = () => {
    if(!dugme || typeof dugme.onclick !== 'function') return Promise.resolve();
    return Promise.resolve(dugme.onclick.call(dugme)).catch(e => {
      bilgi('Ayar kaydedilemedi: ' + (e.message || e));
    });
  };
  const onceki = ayarDegisiklikIslemleri[dugmeId];
  const islem = onceki ? onceki.then(calistir, calistir) : calistir();
  ayarDegisiklikIslemleri[dugmeId] = islem;
  islem.then(() => {
    if(ayarDegisiklikIslemleri[dugmeId] === islem)
      delete ayarDegisiklikIslemleri[dugmeId];
  }, () => {
    if(ayarDegisiklikIslemleri[dugmeId] === islem)
      delete ayarDegisiklikIslemleri[dugmeId];
  });
  return islem;
}
function ayariDegisinceKaydet(alanlar, dugmeId){
  const calistir = () => {
    delete ayarDegisiklikZamanlayicilari[dugmeId];
    ayarDegisiklikIsleminiBaslat(dugmeId);
  };
  alanlar.forEach(id => {
    const el = $(id);
    if(!el) return;
    el.addEventListener('change', () => {
      if(ayarDegisiklikZamanlayicilari[dugmeId])
        clearTimeout(ayarDegisiklikZamanlayicilari[dugmeId]);
      ayarDegisiklikZamanlayicilari[dugmeId] =
        setTimeout(calistir, 350);
    });
    if(el.tagName === 'TEXTAREA')
      el.addEventListener('input', () => {
        if(ayarDegisiklikZamanlayicilari[dugmeId])
          clearTimeout(ayarDegisiklikZamanlayicilari[dugmeId]);
        ayarDegisiklikZamanlayicilari[dugmeId] =
          setTimeout(calistir, 700);
      });
  });
}
async function ayarDegisiklikleriniKaydet(){
  const bekleyen = [];
  Object.keys(ayarDegisiklikZamanlayicilari).forEach(dugmeId => {
    clearTimeout(ayarDegisiklikZamanlayicilari[dugmeId]);
    bekleyen.push(ayarDegisiklikIsleminiBaslat(dugmeId));
    delete ayarDegisiklikZamanlayicilari[dugmeId];
  });
  await Promise.all(bekleyen);
  await Promise.all(Object.values(ayarDegisiklikIslemleri));
}
ayariDegisinceKaydet(['limitInput'], 'limitKaydet');
ayariDegisinceKaydet(['promptDestekYaraticilik','promptDestekUzunluk'],
  'promptDestekKaydet');
ayariDegisinceKaydet(['uretimVarsayilanSteps','uretimVarsayilanSeed',
  'uretimVarsayilanMaxSide'], 'uretimVarsayilanKaydet');
ayariDegisinceKaydet(['llmCtx'], 'llmCtxKaydet');
ayariDegisinceKaydet(['llmSistem','llmSistemSohbet','llmModFikir',
  'llmModGorsel','llmModBirlestir','llmModCeviri','llmModSohbet',
  'llmStreaming','llmLowBandwidth'], 'llmSistemKaydet');
/* Form kaliciligi: her degisimde (gecikmeli) sunucuya yazilir. */
['prompt','negatif','w','h','maxside','maxolcek','vmax','vsure','vfps','videoProfile','videoModel','steps','cfg','seed','wf','preset','sampler'].forEach(id => {
  const el = $(id);
  if(!el) return;
  el.addEventListener('input', formKaydet);
  el.addEventListener('change', formKaydet);
});
/* Prompt Stüdyosu: fikir/görsel/çeviri -> llama-server ile prompt üretimi. */
let psB64_1 = null, psB64_2 = null, psMime1 = null, psMime2 = null, psURL_1 = null, psURL_2 = null, psSonuc = null, psSuruyor = false, psAktifIs = null, psAktifProfil = 'auto', psDuzenlemeBaglam = '';
const llmIsTakipleri = {};
/* Mod secimi YOK: referans sayisina gore sistem promptu otomatik secilir.
   0 referans = fikir, 1 = gorsel duzenleme, 2 = birlestirme. */
function psReferansSayisi(){ return (psB64_1 ? 1 : 0) + (psB64_2 ? 1 : 0); }
function psOtomatikMod(){
  const n = psReferansSayisi();
  return n === 2 ? 'birlestir' : (n === 1 ? 'gorsel' : 'fikir');
}
const PS_PROFIL_ADLARI = {auto:'Otomatik', general:'Genel', z_image:'Z-Image', flux3:'Flux 3 Klein', krea2:'Krea 2', illustrious:'Illustrious', minimaxh3:'MiniMax H3', wan22:'Wan 2.2'};
function psProfilIcin(mod){
  const secili = $('psPromptProfile')?.value || 'auto';
  if(secili !== 'auto') return secili;
  if(mod === 'video') return RUNPOD_WEBUI && typeof runpodVideoModelSecili === 'function' && runpodVideoModelSecili() === 'wan22' ? 'wan22' : 'minimaxh3';
  if(RUNPOD_WEBUI){
    const model = typeof runpodModelSecili === 'function' ? runpodModelSecili() : null;
    if(!model) return psReferansSayisi() ? 'general' : 'z_image';
    if(psReferansSayisi() && (model === 'krea2' || model === 'illustrious')) return 'general';
    return ['z_image','flux3','krea2','illustrious'].includes(model) ? model : 'general';
  }
  return otomatikModel() === 'z_image' && !psReferansSayisi() ? 'z_image' : 'general';
}
function psModGuncelle(){
  let m = psOtomatikMod();
  if(videoMu() || ['minimaxh3','wan22'].includes($('psPromptProfile')?.value)) m = 'video';
  const bilgi = $('psModBilgi');
  if(bilgi){
    bilgi.textContent = m === 'video' ? 'Video · hareket, kamera ve zaman akışı promptu üretilir' :
      (m === 'birlestir' ? 'İki referans · birleştirme promptu üretilir' :
        (m === 'gorsel' ? 'Bir referans · düzenleme promptu üretilir' :
          'Referans yok · fikirden prompt üretilir'));
  }
  $('psMetin').placeholder = m === 'video' ?
    'Video sahnesini ve hareketini anlat... İstersen en fazla iki referans görsel ekle.' : (m === 'gorsel' ?
    'Görselde ne değişiklik istiyorsun? Örn. gökyüzünü mavi yap...' :
    (m === 'birlestir' ? 'İki görseli nasıl birleştirelim? (isteğe bağlı yönlendirme)...' :
      'Ne keşfedilsin? Fikrini yaz...'));
  const efektif = psProfilIcin(m), bilgiMetin = $('psPromptProfileInfo');
  const aciklamalar = {
    auto: 'Otomatik seçim mevcut üretim modeline göre yapılır; üretim modelini değiştirmez.',
    general: 'Uygulamanın genel prompt kuralları kullanılır; modelin üretim ayarı değişmez.',
    z_image: 'Z-Image için verilen sahne, özne ve yerleşim ayrıntılarını koruyan prompt.',
    flux3: 'Flux 3 için metinden görsel, referanslı düzenleme ve isteğe bağlı bbox tablosu.',
    krea2: 'Krea 2 için karakterleri ve aralarındaki konumu ayrı ayrı kuran prompt.',
    illustrious: 'Illustrious için özne ve sahne bilgisini kısa, modelin etiket stiline uygun prompta çevirir.',
    minimaxh3: 'MiniMax H3 için kesintisiz video akışı, referans rolleri ve ses bölümleri.',
    wan22: 'Wan 2.2 için tek planlı, sessiz ve hareket odaklı video promptu.'
  };
  if(bilgiMetin){
    const temel = aciklamalar[$('psPromptProfile')?.value || 'auto'] || aciklamalar.auto;
    bilgiMetin.textContent = ($('psPromptProfile')?.value === 'auto' ?
      'Şu an: ' + (PS_PROFIL_ADLARI[efektif] || 'Genel') + ' · ' : '') + temel;
  }
  if($('psFlux3BboxWrap')) $('psFlux3BboxWrap').hidden = efektif !== 'flux3';
  $('psRefSlot1').hidden = !psB64_1;
  $('psRefSlot2').hidden = !psB64_2;
  $('psGorselOnizleme').hidden = !psB64_1 && !psB64_2;
}
$('psPromptProfile').addEventListener('change', () => {
  uiAyarKaydet('ps_prompt_profile', $('psPromptProfile').value);
  psModGuncelle();
});
$('psFlux3BboxMode').addEventListener('change', () => {
  uiAyarKaydet('ps_bbox_mode', $('psFlux3BboxMode').checked);
  psModGuncelle();
});
function psTemizle(slot){
  if(slot === 1){
    if(psURL_1){ URL.revokeObjectURL(psURL_1); psURL_1 = null; }
    psB64_1 = null; psMime1 = null; $('psGorsel1').value = '';
    $('psPreview1').replaceChildren();
  } else {
    if(psURL_2){ URL.revokeObjectURL(psURL_2); psURL_2 = null; }
    psB64_2 = null; psMime2 = null; $('psGorsel2').value = '';
    $('psPreview2').replaceChildren();
  }
  psModGuncelle();
}
function psOnizlemeCiz(slot, src){
  const kutu = $(slot === 1 ? 'psPreview1' : 'psPreview2');
  if(!kutu) return;
  kutu.replaceChildren();
  const im = document.createElement('img');
  im.src = src;
  im.alt = slot + '. referans önizleme';
  const kaldir = document.createElement('button');
  kaldir.type = 'button';
  kaldir.textContent = '×';
  kaldir.setAttribute('aria-label', slot + '. referansı kaldır');
  kaldir.onclick = () => psTemizle(slot);
  kutu.append(im, kaldir);
}
function psDosyaOku(input, slot){
  const f = input.files && input.files[0];
  if(!f){ psTemizle(slot); return; }
  const istekNesli = oturumNesli;
  const url = URL.createObjectURL(f);
  const im = new Image();
  im.onerror = () => {
    URL.revokeObjectURL(url);
    if(istekNesli !== oturumNesli) return;
    psTemizle(slot); $('psDurum').textContent = 'HATA: görsel açılamadı.';
  };
  im.onload = () => {
    if(istekNesli !== oturumNesli){ URL.revokeObjectURL(url); return; }
    const rd = new FileReader();
    rd.onload = () => {
      if(istekNesli !== oturumNesli){ URL.revokeObjectURL(url); return; }
      const dataUrl = String(rd.result), b64 = dataUrl.split(',')[1];
      const mime = (dataUrl.match(/^data:([^;,]+)/) || [])[1] || f.type || 'image/jpeg';
      if(slot === 1){
        if(psURL_1) URL.revokeObjectURL(psURL_1);
        psURL_1 = url; psB64_1 = b64; psMime1 = mime;
      } else {
        if(psURL_2) URL.revokeObjectURL(psURL_2);
        psURL_2 = url; psB64_2 = b64; psMime2 = mime;
      }
      psOnizlemeCiz(slot, url);
      psModGuncelle();
    };
    rd.readAsDataURL(f);
  };
  im.src = url;
}
$('psGorsel1').onchange = e => psDosyaOku(e.target, 1);
$('psGorsel2').onchange = e => psDosyaOku(e.target, 2);
psModGuncelle();
$('psNegatif').addEventListener('input', () => {
  if(psSonuc) psSonuc.negative = $('psNegatif').value.slice(0, 2000);
});
function psSonucuGoster(s){
  psSonuc = s;
  $('psPozitif').value = s.positive || '';
  if($('uretimPromptDuzenle').checked){
    const onceki = $('prompt').value.trim();
    psDuzenlemeBaglam = [psDuzenlemeBaglam, onceki, s.positive || ''].filter(Boolean).slice(-6).join('\n');
    $('prompt').value = s.positive || '';
    formKaydet();
  }
  $('psPozitifTr').value = s.positive_tr || '';
  $('psNegatif').value = s.negative || '';
  $('psNegatifDetay').open = !!String(s.negative || '').trim();
  $('psAciklama').textContent = s.caption_tr || '';
  $('psEtiketler').textContent = (s.tags || []).join(' · ');
  const sinir = s.sure_ms ? (' · ' + (s.sure_ms / 1000).toFixed(1) + ' sn') : '';
  $('psDurum').textContent = (s.uyari ? ('UYARI: ' + s.uyari + ' · ') : 'Tamamlandı') + sinir;
  try{
    const p = [];
    if(s.sure_ms) p.push('üretim ' + (s.sure_ms / 1000).toFixed(1) + ' sn');
    if(s.yukleme_ms) p.push('yükleme ' + (s.yukleme_ms / 1000).toFixed(1) + ' sn');
    if(s.token) p.push(s.token + ' token');
    if(s.tok_s) p.push((+s.tok_s).toFixed(1) + ' tok/s');
    if(s.prompt_token) p.push(s.prompt_token + ' prompt token');
    if(s.deneme) p.push(s.deneme + ' deneme');
    if(s.ctx) p.push('ctx ' + s.ctx);
    p.push('sistem ' + (PS_PROFIL_ADLARI[psAktifProfil] || 'Genel'));
    p.push('yaratıcılık ' + ($('psYaraticilik').value || ''));
    p.push('dil ' + ($('psDil').value || ''));
    p.push('mod ' + psOtomatikMod());
    $('psBilgi').textContent = p.join(' · ');
  }catch(e){}
  psCanliCiz(s);
  $('psUygula').disabled = false; $('psKopyala').disabled = false; $('psKaydet').disabled = false;
  $('psHizliUret').disabled = false;
  $('psDurdur').disabled = true;
  $('psPromptProfile').disabled = false; $('psFlux3BboxMode').disabled = false;
  try{ psModelDurumuYukle(); }catch(e){}
  try{ psGecmisYukle(); }catch(e){}
}
function escHtml(s){
  return String(s == null ? '' : s)
    .replace(/&/g, '&' + 'amp;')
    .replace(/</g, '&' + 'lt;')
    .replace(/>/g, '&' + 'gt;')
    .replace(/"/g, '&' + 'quot;')
    .replace(/'/g, '&#' + '39;');
}
/* Hafif markdown -> guvenli HTML. Once kacinir, sonra yalniz bizim etiketler eklenir:
   # basliklar, **kalin**, *italik*, `kod`, ```blok```, [baglanti](https://...). */
function markdownHtml(metin){
  let s = escHtml(metin);
  const kodBloklari = [];
  s = s.replace(/```([\s\S]*?)(?:```|$)/g, (m, hamKod) => {
    let kod = hamKod, dil = '';
    const satir = kod.match(/^([^\r\n]*)\r?\n/);
    if(satir){
      const etiket = satir[1].trim().toLowerCase();
      const diller = ['text','txt','plain','plaintext','console','terminal','shell','sh','bash','zsh',
        'cmd','powershell','ps','ps1','json','javascript','js','typescript','ts','python','py',
        'html','css','sql','yaml','yml','xml','markdown','md','diff','java','c','cpp','csharp',
        'cs','go','rust','rs','toml','ini','ruby','rb','php','swift','kotlin','kt','lua','r',
        'dockerfile','dotenv','http'];
      if(diller.includes(etiket)){ dil = etiket; kod = kod.slice(satir[0].length); }
      else kod = kod.replace(/^\r?\n/, '');
    }else kod = kod.replace(/^\r?\n/, '');
    const blok = '<div class="md-kod-kutu">' +
      (dil ? '<div class="md-kod-baslik">' + dil + '</div>' : '') +
      '<pre class="md-kod"><code>' + kod + '</code></pre>' +
      '<div class="md-kod-alt"><button type="button" class="md-kod-kopyala" ' +
      'aria-label="Kodu kopyala" title="Kopyala">⧉</button></div></div>';
    const indis = kodBloklari.push(blok) - 1;
    return '\u0000MDKOD' + indis + '\u0000';
  });
  s = s.replace(/^### (.*)$/gm, '<div class="md-baslik md-b3">$1</div>');
  s = s.replace(/^## (.*)$/gm, '<div class="md-baslik md-b2">$1</div>');
  s = s.replace(/^# (.*)$/gm, '<div class="md-baslik md-b1">$1</div>');
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*\*([^*\n]+)\*\*\*/g, '<strong><em>$1</em></strong>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[\s(>])\*([^*\n]+)\*(?=[\s).,!?;:<]|$)/g, '$1<em>$2</em>');
  s = s.replace(/(^|[\s>])_([^_\n]+)_(?=[\s).,!?;:<]|$)/g, '$1<em>$2</em>');
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)<>]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s.replace(/\u0000MDKOD(\d+)\u0000/g, (m, indis) => kodBloklari[Number(indis)] || '');
}
function boyutYaz(bayt){
  if(bayt == null) return '—';
  if(bayt < 1024) return bayt + ' B';
  if(bayt < 1048576) return (bayt / 1024).toFixed(1) + ' KB';
  return (bayt / 1048576).toFixed(2) + ' MB';
}
function psCanliCiz(o, hedef){
  const el = $(hedef || 'psCanli');
  if(!el) return;
  if(!o){ el.textContent = ''; return; }
  const p = [];
  if(o.faz === 'prompt') p.push('prompt işleniyor' + (o.prompt_ilerleme != null ? ' %' + o.prompt_ilerleme : ''));
  else if(o.faz === 'uretim') p.push('üretim');
  else if(o.faz) p.push(o.faz);
  if(o.token != null) p.push(o.token + ' token');
  if(o.tok_s != null){
    p.push((o.tok_s.toFixed ? o.tok_s.toFixed(1) : o.tok_s) + ' tok/s');
  }
  if(o.gecen_sn != null) p.push(o.gecen_sn + ' sn');
  el.textContent = p.join(' · ');
}
async function psModelDurumuYukle(){
  try{
    const r = await fetch('/api/durum'); const j = await r.json(); const l = j.llm || {};
    const p = [l.canli ? 'canlı' : 'kapalı'];
    if(l.saglik) p.push(l.saglik);
    if(l.pid) p.push('pid ' + l.pid);
    if(l.ctx) p.push('ctx ' + l.ctx + ' · ngl ' + l.ngl);
    if(l.yukleme_ms) p.push('son yükleme ' + (l.yukleme_ms / 1000).toFixed(1) + ' sn');
    p.push('boş VRAM ' + (j.vram_bos_mb != null ? j.vram_bos_mb + ' MB' : '—'));
    $('psModelDurum').textContent = p.join(' · ');
    $('psHazirla').disabled = !!l.uretim_suruyor || l.saglik === 'ok';
  }catch(e){ $('psModelDurum').textContent = 'Okunamadı'; }
}
$('psHazirla').onclick = async () => {
  $('psHazirla').disabled = true;
  $('psDurum').textContent = 'Model hazırlama kuyruğa ekleniyor...';
  try{
    const r = await fetch('/api/llm/hazirla', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: '{}'});
    const j = await r.json().catch(() => ({}));
    if(r.ok && j.is_id){
      $('psDurum').textContent = 'Model hazırlama kuyruğunda' + (j.sira ? ' · sıra ' + j.sira : '');
      llmIsTakipEt(j.is_id, 'llm_preload');
    }else{
      $('psHazirla').disabled = false;
      $('psDurum').textContent = 'HATA: ' + (j.hata || r.status);
    }
  }catch(e){
    $('psHazirla').disabled = false;
    $('psDurum').textContent = 'HATA: sunucuya ulaşılamadı';
  }
};
$('psDurdur').onclick = async () => {
  if(!psAktifIs) return;
  $('psDurdur').disabled = true;
  try{
    await fetch('/api/is/iptal', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({is_id: psAktifIs})});
  }catch(e){}
};
function llmIsAnahtar(tur){ return 'llm_is_' + tur + '_' + (BEN ? BEN.kullanici : ''); }
function llmIsTakipEt(is_id, tur, sakla=true){
  if(!is_id || llmIsTakipleri[is_id]) return;
  if(sakla){ try{ localStorage.setItem(llmIsAnahtar(tur), is_id); }catch(e){} }
  const takip = {busy: false, timer: null, sayac: 0};
  llmIsTakipleri[is_id] = takip;
  const yokla = async () => {
    if(takip.busy) return;
    takip.busy = true;
    try{
      const r = await fetch('/api/is/' + encodeURIComponent(is_id));
      if(llmIsTakipleri[is_id] !== takip) return;
      if(r.status === 404){ throw new Error('İş kaydı bulunamadı'); }
      if(!r.ok) return;
      const j = await r.json(), il = j.ilerleme || {};
      if(llmIsTakipleri[is_id] !== takip) return;

      const sira = j.sira ? ' · sıra ' + j.sira : '';
      const gecen = j.gecen ? ' · ' + Math.round(j.gecen) + ' sn' : '';
      const mesaj = j.durum === 'kuyrukta' ? 'LLM kuyruğunda' + sira :
        j.durum === 'calisiyor' ? ((il.calisan || 'LLM çalışıyor') + gecen) : '';
      if(tur === 'llm_prompt' && is_id === psAktifIs && mesaj) $('psDurum').textContent = mesaj;
      if(tur === 'llm_preload' && mesaj) $('psDurum').textContent = mesaj;
      if(tur === 'llm_preload' && mesaj && $('llmApplyInfo')) $('llmApplyInfo').textContent=mesaj;
      if(tur === 'llm_chat' && j.durum === 'calisiyor' && sohbetAktifEl){
        const icerikEl = sohbetAktifEl.querySelector('.icerik');
        if(icerikEl && j.sonuc && j.sonuc.parca && icerikEl.dataset.ham !== j.sonuc.parca){
          icerikEl.dataset.ham = j.sonuc.parca;
          icerikEl.innerHTML = markdownHtml(j.sonuc.parca);
          if(sohbetOtomatikScroll){
            $('sohbetIcerikAlani').scrollTop = $('sohbetIcerikAlani').scrollHeight;
          }
        }
        sohbetDusunmeGuncelle(j.sonuc && j.sonuc.dusunme);
        sohbetDurumSatiri(sohbetAsamaMetni(j), j.llm_canli);
        sohbetBaglamGuncelle(j);
        takip.sayac++;
        if(takip.sayac % 10 === 0){ durum(); kuyrukYenile(); if(aktifMenu === 'izleme') izlemeYukle(); }
        return;
      }
      if(tur === 'llm_chat' && j.durum !== 'bitti'){
        const asamaMetni = sohbetAsamaMetni(j);
        if(asamaMetni) sohbetDurumSatiri(asamaMetni, j.llm_canli);
        sohbetBaglamGuncelle(j);
      }
      if(j.llm_canli){
        if(tur === 'llm_prompt' || tur === 'llm_preload') psCanliCiz(j.llm_canli, 'psCanli');
        if(tur === 'llm_chat') psCanliCiz(j.llm_canli, 'sohbetCanli');
      }
      takip.sayac++;
      if(takip.sayac % 3 === 0){ durum(); kuyrukYenile(); if(aktifMenu === 'izleme') izlemeYukle(); }
      if(j.durum === 'bitti' || j.durum === 'hata' || j.durum === 'durduruldu'){
        clearInterval(takip.timer);
        delete llmIsTakipleri[is_id];
        try{ if(localStorage.getItem(llmIsAnahtar(tur)) === is_id) localStorage.removeItem(llmIsAnahtar(tur)); }catch(e){}
        if(tur === 'llm_prompt' && is_id === psAktifIs){
          psSuruyor = false; $('psUret').disabled = false; $('psDurdur').disabled = true;
          if(j.durum === 'bitti' && j.sonuc){
            psSonucuGoster(j.sonuc);
          }
          else{
            $('psPromptProfile').disabled = false; $('psFlux3BboxMode').disabled = false;
            psCanliCiz(null);
            $('psDurum').textContent = j.durum === 'durduruldu' ? 'İş durduruldu.' : 'HATA: ' + (j.hata || 'prompt üretimi başarısız');
          }
          psAktifIs = null;
        }else if(tur === 'llm_chat'){
          sohbetBitir(j);
        }else if(tur === 'llm_preload'){
          $('psHazirla').disabled = j.durum === 'bitti';
          $('psDurum').textContent = j.durum === 'bitti' ? 'Model hazır.' : (j.durum === 'durduruldu' ? 'Hazırlık durduruldu.' : 'HATA: ' + (j.hata || 'model hazırlanamadı'));
          if($('llmApplyInfo')) $('llmApplyInfo').textContent=$('psDurum').textContent;
          if($('llmModelYenidenYukle')) $('llmModelYenidenYukle').disabled=false;
          if(j.durum==='bitti') llmSistemYukle();
          psCanliCiz(null);
          psModelDurumuYukle();
        }
        kuyrukYenile();
      }
    }catch(e){
      if(llmIsTakipleri[is_id] !== takip) return;
      clearInterval(takip.timer);
      delete llmIsTakipleri[is_id];
      try{ if(localStorage.getItem(llmIsAnahtar(tur)) === is_id) localStorage.removeItem(llmIsAnahtar(tur)); }catch(ignore){}
      if(tur === 'llm_prompt' && is_id === psAktifIs){
        psSuruyor = false; psAktifIs = null; $('psUret').disabled = false; $('psDurdur').disabled = true;
        $('psPromptProfile').disabled = false; $('psFlux3BboxMode').disabled = false;
        psCanliCiz(null); $('psDurum').textContent = 'HATA: ' + e.message;
      }
      if(tur === 'llm_chat'){ sohbetBitir({durum: 'hata', hata: e.message}); }
      if(tur === 'llm_preload'){ psCanliCiz(null); $('psHazirla').disabled = false; $('psDurum').textContent = 'HATA: ' + e.message; }
    }finally{ takip.busy = false; }
  };
  yokla();
  takip.timer = setInterval(yokla, tur === 'llm_chat' ? 300 : 1500);
}
async function llmIsleriniBagla(){
  if(!BEN) return;
  try{
    const r = await fetch('/api/isler'), j = await r.json();
    const aktif = (j.isler || []).filter(k => (k.durum === 'kuyrukta' || k.durum === 'calisiyor') && (k.tur === 'llm_prompt' || k.tur === 'llm_chat' || k.tur === 'llm_preload'));
    for(const tur of ['llm_prompt','llm_chat','llm_preload']){
      let id = null;
      try{ id = localStorage.getItem(llmIsAnahtar(tur)); }catch(e){}
      if(!id){
        const bulunan = aktif.filter(k => k.tur === tur);
        if(bulunan.length) id = bulunan[bulunan.length - 1].is_id;
      }
      if(id){
        if(tur === 'llm_prompt'){ psAktifIs = id; psSuruyor = true; $('psUret').disabled = true; $('psDurdur').disabled = false; $('psPromptProfile').disabled = true; $('psFlux3BboxMode').disabled = true; }
        else if(tur === 'llm_chat'){ sohbetAktifIs = id; sohbetCalisma(true); }
        else { $('psHazirla').disabled = true; $('psDurum').textContent = 'Model hazırlanıyor...'; }
        llmIsTakipEt(id, tur);
      }
    }
  }catch(e){}
}
$('psUret').onclick = async () => {
  if(psSuruyor) return;
  const duzenleAktif = !!$('uretimPromptDuzenle').checked;
  let m = duzenleAktif ? 'duzenle' : psOtomatikMod();
  const seciliProfil = $('psPromptProfile').value || 'auto';
  if(!duzenleAktif && (videoMu() || ['minimaxh3','wan22'].includes(seciliProfil))) m = 'video';
  const profile = duzenleAktif ? 'general' : psProfilIcin(m);
  if(m === 'video' && !['minimaxh3','wan22','general'].includes(profile)){
    $('psDurum').textContent = 'Video promptu için seçili video modelinin profilini veya Genel profilini seç.';
    return;
  }
  if((m === 'gorsel') && !psB64_1){ $('psDurum').textContent = 'HATA: 1. görseli seç.'; return; }
  if((m === 'gorsel') && !$('psMetin').value.trim()){ $('psDurum').textContent = 'HATA: görselde ne değişiklik yapmak istediğinizi yazın.'; return; }
  if((m === 'birlestir') && (!psB64_1 || !psB64_2)){ $('psDurum').textContent = 'HATA: iki görseli de seç.'; return; }
  if(duzenleAktif && !$('prompt').value.trim()){ $('psDurum').textContent = 'HATA: düzenlenecek üretim promptu boş.'; return; }
  if((m === 'fikir' || m === 'ceviri' || m === 'video') && !$('psMetin').value.trim()){ $('psDurum').textContent = m === 'video' ? 'HATA: video isteğini yaz.' : 'HATA: metin yaz.'; return; }
   psSuruyor = true; $('psUret').disabled = true; $('psDurdur').disabled = true;
   $('psPromptProfile').disabled = true; $('psFlux3BboxMode').disabled = true;
   psAktifProfil = profile;
   psCanliCiz(null);
   $('psDurum').textContent = 'LLM kuyruğuna ekleniyor...';
  const gorseller = [];
  if(!duzenleAktif && psB64_1) gorseller.push({mime: psMime1 || 'image/jpeg', veri: psB64_1});
  if(!duzenleAktif && psB64_2) gorseller.push({mime: psMime2 || 'image/jpeg', veri: psB64_2});
  try{
    const r = await fetch('/api/prompt-uret', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mod: m, metin: duzenleAktif ? ('Mevcut promptu kullanıcının isteğine göre düzenle.\n\nMevcut prompt:\n'+$('prompt').value+'\n\nDüzenleme isteği:\n'+$('psMetin').value+'\n\nÖnceki düzenleme bağlamı:\n'+psDuzenlemeBaglam) : $('psMetin').value, dil: $('psDil').value,
        yaraticilik: +$('psYaraticilik').value,
        gorseller, model_prompt_profile: profile,
        llm_model: RUNPOD_WEBUI && $('psLlmModel') ? $('psLlmModel').value : undefined,
        bbox_mode: profile === 'flux3' && $('psFlux3BboxMode').checked})});
    const j = await r.json().catch(() => ({}));
     if(r.ok && j.is_id){
       psAktifIs = j.is_id;
       $('psDurdur').disabled = false;
       $('psDurum').textContent = 'LLM kuyruğunda' + (j.sira ? ' · sıra ' + j.sira : '');
       llmIsTakipEt(j.is_id, 'llm_prompt');
     }else { psSuruyor = false; $('psUret').disabled = false; $('psDurdur').disabled = true; $('psPromptProfile').disabled = false; $('psFlux3BboxMode').disabled = false; $('psDurum').textContent = 'HATA: ' + (j.hata || ('' + r.status)); }
  }catch(e){
     psSuruyor = false; $('psUret').disabled = false;
     $('psPromptProfile').disabled = false; $('psFlux3BboxMode').disabled = false;
     $('psDurum').textContent = 'HATA: sunucuya ulaşılamadı (' + (e && e.message ? e.message : '') + ')';
  }
};
$('psUygula').onclick = () => {
  if(!psSonuc) return;
  $('prompt').value = psSonuc.positive || '';
  $('negatif').value = psSonuc.negative || '';
  $('uretimNegatifPanel').open = !!String(psSonuc.negative || '').trim();
  formKaydet();
  aktifMenu = 'uretim'; menuUygula();
  bilgi('Prompt kutusuna uygulandı');
  window.scrollTo({top: 0, behavior: 'smooth'});
};
$('psHizliUret').onclick = () => {
  if(!psSonuc || !psSonuc.positive) return;
  fotoB64 = psB64_1 || null; fotoMime = psB64_1 ? (psMime1 || 'image/jpeg') : null;
  fotoB64_2 = psB64_2 || null; fotoMime2 = psB64_2 ? (psMime2 || 'image/jpeg') : null;
  $('foto').value = ''; $('foto2').value = '';
  if(fotoB64){ $('oniz').src = 'data:' + (fotoMime || 'image/jpeg') + ';base64,' + fotoB64; $('oniz').style.display = 'block'; $('fotoKaldir').style.display = 'block'; }
  else { $('oniz').removeAttribute('src'); $('oniz').style.display = 'none'; $('fotoKaldir').style.display = 'none'; }
  $('foto2Blok').style.display = fotoB64 ? 'block' : 'none';
  if(fotoB64_2){ $('oniz2').src = 'data:' + (fotoMime2 || 'image/jpeg') + ';base64,' + fotoB64_2; $('oniz2').style.display = 'block'; $('foto2Kaldir').style.display = 'block'; }
  else { $('oniz2').removeAttribute('src'); $('oniz2').style.display = 'none'; $('foto2Kaldir').style.display = 'none'; }
  uretimOnizlemeGuncelle();
  $('prompt').value = psSonuc.positive;
  $('negatif').value = psSonuc.negative || '';
  $('uretimNegatifPanel').open = !!String(psSonuc.negative || '').trim();
  const varsayilanAdim = RUNPOD_WEBUI && uretimVarsayilan.steps === 9
    ? (RUNPOD_IMAGE_DEFAULTS[otomatikModel()]?.steps || 9)
    : uretimVarsayilan.steps;
  $('steps').value = varsayilanAdim;
  $('seed').value = uretimVarsayilan.seed;
  $('maxside').value = uretimVarsayilan.max_side;
  $('maxolcek').checked = !!fotoB64;
  $('wf').value = 'otomatik'; $('wf').onchange({target: $('wf')});
  uretimModGuncelle(); cozunurlukGorunumu();
  aktifMenu = 'uretim'; menuUygula();
  formKaydet();
  uret();
};
$('psGorselEkleBtn').onclick = () => {
  const menu = $('psGorselMenu');
  menu.hidden = !menu.hidden;
  $('psGorselEkleBtn').setAttribute('aria-expanded', menu.hidden ? 'false' : 'true');
};
$('psGorselSec1').onclick = () => {
  $('psGorselMenu').hidden = true;
  $('psGorselEkleBtn').setAttribute('aria-expanded', 'false');
  const slot = psB64_1 ? (psB64_2 ? 1 : 2) : 1;
  $(slot === 1 ? 'psGorsel1' : 'psGorsel2').click();
};
$('psGorselSec2').onclick = async () => {
  $('psGorselMenu').hidden = true;
  $('psGorselEkleBtn').setAttribute('aria-expanded', 'false');
  const slot = psB64_1 ? (psB64_2 ? 1 : 2) : 1;
  await seciciAc(slot, 'studyo');
};
$('psAyarBtn').onclick = () => {
  const panel = $('psAyarPanel');
  panel.hidden = !panel.hidden;
  $('psAyarBtn').setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
};
$('prompt').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){
    e.preventDefault();
    uret();
  }
});
document.addEventListener('click', e => {
  const menu = $('psGorselMenu');
  if(menu && !menu.hidden && !menu.contains(e.target) && e.target !== $('psGorselEkleBtn')) menu.hidden = true;
});
$('psKopyala').onclick = async () => {
  if(!$('psPozitif').value && !$('psPozitifTr').value) return;
  const negative = $('psNegatif').value.trim();
  const metin = 'EN: ' + ($('psPozitif').value || '') + '\nTR: ' + ($('psPozitifTr').value || '') +
    (negative ? '\nNEGATIVE: ' + negative : '');
  try{ await navigator.clipboard.writeText(metin); bilgi(negative ? 'Kopyalandı (EN+TR+negative)' : 'Kopyalandı (EN+TR)'); }
  catch(e){ bilgi('Kopyalama reddedildi'); }
};
$('psKaydet').onclick = async () => {
  if(!psSonuc) return;
  const ad = prompt('Kayıt adı:');
  if(!ad) return;
  const kayit = formdanPreset(String(ad).trim());
  kayit.prompt = psSonuc.positive || '';
  kayit.negative = psSonuc.negative || '';
  try{
    await fetch('/api/promptlar', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(kayit)});
    await promptListele();
    bilgi('Promptlar kayıtlarına eklendi');
  }catch(e){ bilgi('Kayıt başarısız'); }
};
/* Model sohbeti: serbest soru-cevap, konuşma yalnız sayfada durur ve kaydedilir. */
let sohbetGecmis = [], sohbetSuruyor = false, sohbetAktifIs = null, sohbetId = null,
    sohbetAktifEl = null, izlemeTimer = null, sohbetFotolar = [];
let sohbetOtomatikScroll = true;

$('sohbetIcerikAlani').addEventListener('scroll', () => {
  const el = $('sohbetIcerikAlani');
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  sohbetOtomatikScroll = atBottom;
});
$('sohbetListe').addEventListener('click', async event => {
  const dugme = event.target.closest('.md-kod-kopyala');
  if(!dugme) return;
  event.preventDefault(); event.stopPropagation();
  const kod = dugme.closest('.md-kod-kutu')?.querySelector('pre code')?.textContent || '';
  try{
    await navigator.clipboard.writeText(kod);
    dugme.textContent = '✓';
    dugme.setAttribute('aria-label', 'Kopyalandı');
    dugme.title = 'Kopyalandı';
    setTimeout(() => {
      if(!dugme.isConnected) return;
      dugme.textContent = '⧉';
      dugme.setAttribute('aria-label', 'Kodu kopyala');
      dugme.title = 'Kopyala';
    }, 1200);
  }catch(e){ bilgi('Metin kopyalanamadı'); }
});
let sohbetBaglamBoyutu = 8000, sohbetBaglamTahmin = 0, sohbetBaglamOlcum = null;
function benzersizId(){
  return (Math.random().toString(36).slice(2) + Date.now().toString(36));
}
function dusunmeDetayi(metin){
  const d = document.createElement('details');
  d.className = 'dusunme-kutu';
  const s = document.createElement('summary');
  s.textContent = 'Düşünme';
  const pre = document.createElement('pre');
  pre.textContent = String(metin || '');
  d.appendChild(s); d.appendChild(pre);
  return d;
}
function sohbetAkisAlani(hedef){
  if(!hedef) return null;
  let akis = hedef.querySelector('.sohbet-akis');
  if(akis) return akis;
  akis = document.createElement('div');
  akis.className = 'sohbet-akis';
  akis.setAttribute('aria-label', 'Yanıt ilerlemesi');
  const adimlar = document.createElement('ol');
  adimlar.className = 'sohbet-akis-adimlar';
  akis.appendChild(adimlar);
  const icerik = hedef.querySelector('.icerik');
  hedef.insertBefore(akis, icerik || null);
  return akis;
}
function sohbetAkisAdimiEkle(adimlar, metin, hata=false){
  const adim = document.createElement('li');
  adim.className = 'sohbet-akis-adim ' + (hata ? 'hata' : 'aktif');
  adim.dataset.asama = metin;
  const isaret = document.createElement('span');
  isaret.className = 'sohbet-akis-isaret';
  isaret.setAttribute('aria-hidden', 'true');
  isaret.textContent = hata ? '!' : '…';
  const yazi = document.createElement('span');
  yazi.className = 'sohbet-akis-metin';
  yazi.textContent = metin;
  yazi.setAttribute('role', 'status');
  yazi.setAttribute('aria-live', 'polite');
  const detay = document.createElement('span');
  detay.className = 'sohbet-akis-detay';
  const canli = document.createElement('span');
  canli.className = 'sohbet-akis-canli';
  canli.id = 'sohbetCanli';
  canli.setAttribute('aria-hidden', 'true');
  adim.append(isaret, yazi, detay, canli);
  adimlar.appendChild(adim);
  return adim;
}
function sohbetEkle(rol, metin, thinking, kimlik, resimler = [], kaydet = true){
  const kayit = {role: rol, content: metin || '', thinking: thinking || null, resimler};
  if(kaydet) sohbetGecmis.push(kayit);
  const d = document.createElement('div');
  d.className = 'mesaj ' + (rol === 'user' ? 'user' : 'asistan');
  if(kimlik) d.dataset.id = kimlik;
  const baslik = document.createElement('div');
  baslik.className = 'baslik';
  baslik.textContent = rol === 'user' ? 'Sen' : 'Asistan';
  const icerik = document.createElement('div');
  icerik.className = 'icerik';
  icerik.dataset.ham = metin || '';
  icerik.innerHTML = markdownHtml(metin || '');
  d.appendChild(baslik); d.appendChild(icerik);
  if(rol === 'user' && resimler.length){
    const galeri = document.createElement('div');
    galeri.className = 'sohbet-galeri';
    resimler.forEach((url, i) => {
      const btn = document.createElement('button'), im = document.createElement('img');
      btn.type = 'button'; im.src = url; im.alt = 'Gönderilen görsel ' + (i + 1);
      btn.onclick = () => lbAc(url);
      btn.append(im); galeri.append(btn);
    });
    d.append(galeri);
  }
  if(rol === 'assistant' && kaydet){
    const secenekler = document.createElement('div');
    secenekler.className = 'sohbet-yanit-islemleri';
    secenekler.append(sohbetDugme('↻ Yeniden üret', () => {
      const i = sohbetGecmis.indexOf(kayit);
      if(i < 1 || sohbetSuruyor) return;
      const dal = i !== sohbetGecmis.length - 1;
      sohbetBaslat(sohbetGecmis.slice(0, i), kayit.ekstra, dal ? null : sohbetId, i);
    }), sohbetDugme('Kopyala', async () => {
      try{ await navigator.clipboard.writeText(metin); bilgi('Yanıt kopyalandı'); }
      catch(e){ bilgi('Yanıt kopyalanamadı'); }
    }));
    d.append(secenekler);
  }
  if(rol !== 'user' && thinking){
    d.appendChild(dusunmeDetayi(thinking));
  }
  $('sohbetListe').appendChild(d);
  sohbetBosDurumGuncelle();
  if(sohbetOtomatikScroll){
    $('sohbetIcerikAlani').scrollTop = $('sohbetIcerikAlani').scrollHeight;
  }
  return d;
}
/* Kuyruk, model hazirligi, dusunme ve yanit adimlari mesaj icinde birikir. */
function sohbetDurumSatiri(metin, canli, hedef=sohbetAktifEl){
  if(!hedef){ if(metin) bilgi(metin); return; }
  if(sohbetIptal && sohbetSuruyor) metin = 'Durduruluyor…';
  const akis = sohbetAkisAlani(hedef);
  if(metin == null){ akis?.remove(); return; }
  const adimlar = akis.querySelector('.sohbet-akis-adimlar');
  const bolunen = String(metin).split(' · ');
  const ad = bolunen.shift();
  let adim = adimlar.lastElementChild;
  if(!adim || adim.dataset.asama !== ad){
    if(adim){
      adim.classList.remove('aktif'); adim.classList.add('tamamlandi');
      adim.querySelector('.sohbet-akis-isaret').textContent = '✓';
      const eskiCanli = adim.querySelector('.sohbet-akis-canli');
      eskiCanli?.removeAttribute('id');
      if(eskiCanli) eskiCanli.textContent = '';
    }
    adim = sohbetAkisAdimiEkle(adimlar, ad, /hata|alınamadı|başarısız/i.test(ad));
  }
  adim.querySelector('.sohbet-akis-detay').textContent = bolunen.join(' · ');
  psCanliCiz(canli || null, 'sohbetCanli');
  if(sohbetOtomatikScroll){
    const alan = $('sohbetIcerikAlani'); alan.scrollTop = alan.scrollHeight;
  }
}
function sohbetAsamaMetni(j){
  if(!j) return null;
  if(j.durum === 'kuyrukta') return 'Kuyrukta bekliyor' + (j.sira ? ' · sıra ' + j.sira : '');
  if(j.durum !== 'calisiyor') return null;
  const il = j.ilerleme || {};
  const sn = j.gecen ? Math.round(j.gecen) : 0;
  if(il.asama === 'dusunme' || il.asama === 'yanit'){
    return il.asama === 'dusunme' ? 'Düşünüyor' : 'Yanıt yazılıyor';
  }
  if(il.asama === 'yukleme') return 'Model yükleniyor · ' + sn + ' sn';
  if(il.asama === 'bellek') return 'Bellek hazırlanıyor · ' + sn + ' sn';
  return 'Başlatılıyor' + (sn ? ' · ' + sn + ' sn' : '');
}
function sohbetDusunmeGuncelle(metin, hedef=sohbetAktifEl){
  if(!hedef) return;
  const varMi = !!(metin && String(metin).trim());
  const akis = sohbetAkisAlani(hedef);
  let panel = akis.querySelector('.sohbet-dusunme-canli');
  if(!varMi){ panel?.remove(); return; }
  if(!panel){
    panel = dusunmeDetayi('');
    panel.classList.add('sohbet-dusunme-canli');
    panel.open = true;
    akis.appendChild(panel);
  }
  const pre = panel.querySelector('pre');
  if(pre.textContent !== metin) pre.textContent = metin;
  pre.scrollTop = pre.scrollHeight;
  if(sohbetOtomatikScroll){
    const alan = $('sohbetIcerikAlani'); alan.scrollTop = alan.scrollHeight;
  }
}
/* Baglam gostergesi: olculen prompt tokeni, yoksa tahmini deger. */
function sohbetBaglamTahmini(){
  let karakter = 0, gorsel = 0;
  sohbetGecmis.forEach(m => {
    gorsel += (m.resimler || []).length;
    if(typeof m.content === 'string') karakter += m.content.length;
    else if(Array.isArray(m.content)) m.content.forEach(p => {
      if(p.type === 'text') karakter += (p.text || '').length;
      else if(p.type === 'image_url') gorsel++;
    });
  });
  const girdi = $('sohbetGirdi');
  if(girdi) karakter += girdi.value.length;
  return Math.round(karakter / 4) + gorsel * 320 + 48;
}
function sohbetBaglamGuncelle(j){
  const btn = $('sohbetBaglam');
  if(!btn) return;
  if(j && j.durum === 'calisiyor' && j.llm_canli && j.llm_canli.prompt_token != null)
    sohbetBaglamOlcum = j.llm_canli.prompt_token;
  const kullanilan = sohbetBaglamOlcum != null ? sohbetBaglamOlcum : sohbetBaglamTahmin;
  const yuzde = Math.max(0, Math.min(100, Math.round(100 * kullanilan / Math.max(1, sohbetBaglamBoyutu))));
  const C = 97.39;
  const yay = $('sohbetBaglamYay');
  if(yay){
    yay.style.strokeDasharray = (C * yuzde / 100).toFixed(1) + ' ' + C;
    yay.classList.toggle('dolu', yuzde > 85);
  }
  const yazi = $('sohbetBaglamYazi');
  if(yazi) yazi.textContent = sohbetBaglamBoyutu ? yuzde + '%' : '—';
  btn.hidden = false;
  btn.dataset.kaynak = sohbetBaglamOlcum != null ? 'olculen' : (sohbetBaglamTahmin ? 'tahmini' : 'bos');
}
$('sohbetBaglam').onclick = () => {
  const olculen = sohbetBaglamOlcum != null;
  const kullanilan = olculen ? sohbetBaglamOlcum : sohbetBaglamTahmin;
  const kaynak = olculen ? 'ölçülen (llama-server prompt token sayısı)'
    : (sohbetBaglamTahmin ? 'tahmini (metin uzunluğundan, kabaca 4 karakter/token)' : 'henüz ölçüm yok');
  kaplaAc('<h3 style="margin-top:0">Bağlam kullanımı</h3>' +
    '<div class="kucuk">Kullanılan: ' + kullanilan + ' token · Bağlam: ' + sohbetBaglamBoyutu + ' token · Doluluk: %' +
    Math.max(0, Math.min(100, Math.round(100 * kullanilan / Math.max(1, sohbetBaglamBoyutu)))) + '</div>' +
    '<div class="kucuk">Kaynak: ' + kaynak + '</div>' +
    '<div class="kucuk">Bağlam boyutunu Ayarlar bölümünden değiştirebilirsin; değer bir sonraki model başlatmasında uygulanır.</div>');
};
/* Mobil sohbet menusu: ac/kapat + kararti. */
function sohbetMenyuAyarla(ac){
  const kenar = $('sohbetYanKenar'), btn = $('sohbetMenuBtn'), kararti = $('sohbetYanKararti');
  if(!kenar) return;
  kenar.classList.toggle('acik', !!ac);
  if(btn) btn.setAttribute('aria-expanded', ac ? 'true' : 'false');
  if(kararti) kararti.hidden = !ac;
}
$('sohbetMenuBtn').onclick = () =>
  sohbetMenyuAyarla(!$('sohbetYanKenar').classList.contains('acik'));
$('sohbetYanKapat').onclick = () => sohbetMenyuAyarla(false);
$('sohbetYanKararti').onclick = () => sohbetMenyuAyarla(false);
function sohbetDugme(metin, islem){
  const b = document.createElement('button');
  b.type = 'button'; b.textContent = metin; b.onclick = islem;
  return b;
}
function sohbetFotoCiz(){
  const alan = $('sohbetFotoOnizleme');
  alan.replaceChildren();
  alan.hidden = !sohbetFotolar.length;
  sohbetFotolar.forEach((url, i) => {
    const kutu = document.createElement('div'), im = document.createElement('img');
    im.src = url; im.alt = 'Ek ' + (i + 1);
    const b = sohbetDugme('×', () => { sohbetFotolar.splice(i, 1); sohbetFotoCiz(); });
    b.className = 'sohbet-foto-kaldir';
    b.setAttribute('aria-label', 'Görsel ' + (i + 1) + ' kaldır');
    kutu.append(im, b); alan.append(kutu);
  });
}
function sohbetFotoEkle(url){
  if(!/^data:image\/[a-z0-9.+-]+;base64,/i.test(url)) return bilgi('Görsel okunamadı');
  if(sohbetFotolar.length >= 6) return bilgi('Bir mesaja en fazla 6 görsel eklenebilir');
  if(url.length > 8 * 1024 * 1024) return bilgi('Görsel 6 MB sınırını aşıyor');
  sohbetFotolar.push(url); sohbetFotoCiz();
}
function sohbetFotoTemizle(){
  sohbetFotolar = []; $('sohbetFoto').value = ''; sohbetFotoCiz();
}
function sohbetYeni(){
  sohbetSecimIstek++;
  sohbetGecmis = []; sohbetId = null; sohbetAktifIs = null; sohbetAktifEl = null;
  sohbetFotoTemizle();
  $('sohbetListe').replaceChildren(); $('sohbetGirdi').value = '';
  $('sohbetGirdi').style.height = '';
  sohbetCalisma(false);
  sohbetDurumSatiri(null);
  sohbetDusunmeGuncelle(null);
  sohbetBosDurumGuncelle();
  sohbetMenyuAyarla(false);
  sohbetKayitlariAktiflestir();
  $('sohbetGirdi').focus();
}
function sohbetBosDurumGuncelle(){
  const bos = $('sohbetBosDurum');
  if(bos) bos.hidden = $('sohbetListe').childElementCount > 0;
}
document.querySelectorAll('.sohbet-hizli').forEach(b => b.onclick = () => {
  $('sohbetGirdi').value = b.getAttribute('data-hizli') || b.textContent;
  $('sohbetGirdi').focus();
});
let sohbetDeneme = null, sohbetIptal = false, sohbetHataEl = null, sohbetEkOkuma = 0;
function sohbetCalisma(aktif){
  sohbetSuruyor = aktif;
  const b = $('sohbetGonder');
  b.textContent = aktif ? '■' : '↑';
  b.title = aktif ? 'Üretimi durdur' : 'Gönder';
  b.setAttribute('aria-label', b.title);
  b.disabled = !aktif && sohbetEkOkuma > 0;
  $('sohbetAna').classList.toggle('uretiyor', aktif);
  $('sohbetYeniSohbet').disabled = aktif;
  if(!aktif){ sohbetIptal = false; sohbetDeneme = null; }
}
function sohbetMesajlariniHazirla(gecmis){
  return gecmis.slice(-40).map(m => ({role: m.role, content: m.resimler?.length ? [
    {type: 'text', text: m.content || ''},
    ...m.resimler.map(url => ({type: 'image_url', image_url: {url}}))
  ] : m.content || ''}));
}
function sohbetGonderMetin(metin){
  if((!metin && !sohbetFotolar.length) || sohbetSuruyor || sohbetEkOkuma) return;
  sohbetEkle('user', metin, null, benzersizId(), sohbetFotolar);
  $('sohbetGirdi').value = '';
  $('sohbetGirdi').style.height = '';
  sohbetFotoTemizle();
  sohbetBaslat(sohbetGecmis);
}
async function sohbetDurdur(){
  if(!sohbetSuruyor || sohbetIptal) return;
  sohbetIptal = true;
  sohbetDurumSatiri('Durduruluyor…');
  if(sohbetAktifIs) await sohbetIptalGonder(sohbetAktifIs);
}
async function sohbetIptalGonder(id){
  try{
    const r = await fetch('/api/is/iptal', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({is_id: id})});
    if(!r.ok) throw new Error();
  }catch(e){
    if(sohbetAktifIs === id){ sohbetIptal = false; bilgi('Durdurma isteği gönderilemedi'); }
  }
}
function sohbetBitir(j){
  const deneme = sohbetDeneme;
  const bekleyen = sohbetAktifEl;
  const akis = bekleyen?.querySelector('.sohbet-akis') || null;
  sohbetAktifEl = null; sohbetAktifIs = null;
  sohbetCalisma(false);
  sohbetDusunmeGuncelle(null);
  if(j.durum === 'bitti' && j.sonuc){
    const sonuc = j.sonuc;
    bekleyen?.remove();
    if(deneme && deneme.kes !== null){
      sohbetGecmis = [];
      $('sohbetListe').replaceChildren();
      deneme.gecmis.forEach(m => {
        sohbetEkle(m.role, m.content, m.thinking, benzersizId(), m.resimler);
        sohbetGecmis.at(-1).ekstra = m.ekstra;
      });
    }
    const yanitMesaji = sohbetEkle('assistant', sonuc.yanit || '', null);
    sohbetGecmis.at(-1).thinking = sonuc.reasoning_content || null;
    if(akis){
      const dusunme = akis.querySelector('.sohbet-dusunme-canli');
      if(dusunme) dusunme.open = false;
      yanitMesaji.insertBefore(akis, yanitMesaji.querySelector('.icerik'));
      const sure = sonuc.sure_ms ? ' · ' + (sonuc.sure_ms / 1000).toFixed(1) + ' sn' : '';
      sohbetDurumSatiri('Yanıt tamamlandı' + sure, null, yanitMesaji);
      const sonAdim = yanitMesaji.querySelector('.sohbet-akis-adim:last-child');
      if(sonAdim){
        sonAdim.classList.remove('aktif'); sonAdim.classList.add('tamamlandi');
        sonAdim.querySelector('.sohbet-akis-isaret').textContent = '✓';
      }
    }
    sohbetGecmis.at(-1).ekstra = deneme?.ekstra;
    sohbetId = sonuc.sohbet_id || sohbetId;
    if(sonuc.prompt_token != null){
      sohbetBaglamOlcum = sonuc.prompt_token; sohbetBaglamGuncelle(null);
    }
    sohbetKayitYukle();
  }else{
    const mesaj = j.durum === 'durduruldu' ? 'Üretim durduruldu' : 'Yanıt alınamadı: ' + (j.hata || 'Bilinmeyen hata');
    sohbetDurumSatiri(mesaj, null, bekleyen);
    if(bekleyen){
      bekleyen.classList.remove('yaziliyor');
      bekleyen.classList.add('sohbet-hata');
      bekleyen.querySelector('.baslik').textContent = mesaj;
      sohbetHataEl = bekleyen;
      if(deneme) bekleyen.append(sohbetDugme('↻ Tekrar dene', () =>
        sohbetBaslat(deneme.gecmis, deneme.ekstra, deneme.id, deneme.kes)));
    }
  }
}
async function sohbetBaslat(gecmis, ekstra, id = sohbetId, kes = null){
  if(sohbetSuruyor) return;
  const nesil = oturumNesli;
  sohbetHataEl?.remove(); sohbetHataEl = null;
  sohbetDeneme = {gecmis: gecmis.slice(), ekstra, id, kes};
  sohbetCalisma(true);
  sohbetBaglamTahmin = sohbetBaglamTahmini();
  sohbetBaglamOlcum = null;
  sohbetBaglamGuncelle(null);
  sohbetAktifEl = sohbetEkle('assistant', '', null, benzersizId(), [], false);
  sohbetAktifEl.classList.add('yaziliyor');
  sohbetDurumSatiri('Yanıt hazırlanıyor');
  sohbetDusunmeGuncelle(null);
  try{
    const r = await fetch('/api/sohbet', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mesajlar: sohbetMesajlariniHazirla(gecmis), yaraticilik: 0.7,
        llm_model: RUNPOD_WEBUI && $('sohbetLlmModel') ? $('sohbetLlmModel').value : undefined,
        sohbet_id: id, ...ekstra})});
    const j = await r.json();
    if(nesil !== oturumNesli) return;
    if(!r.ok || !j.is_id) throw new Error(j.hata || ('HTTP ' + r.status));
    sohbetAktifIs = j.is_id;
    if(sohbetIptal) void sohbetIptalGonder(j.is_id);
    else sohbetDurumSatiri(j.sira ? 'Kuyrukta bekliyor · sıra ' + j.sira : 'Yanıt bekleniyor');
    llmIsTakipEt(j.is_id, 'llm_chat');
  }catch(e){
    if(nesil === oturumNesli) sohbetBitir({durum: 'hata', hata: e.message});
  }
}
/* Mevcut sohbetin son konusulan konusunu uzun bir fotoğraf üretim promptuna cevirir. */
const SOHBET_PROMPT_TALIMATI =
  'Bu sohbette konuşulan son konuyu, fotoğraf üretimi için kullanılacak ayrıntılı ve ' +
  'uzun bir üretim promptuna dönüştür. Konunun özünü ve kullanıcının niyetini koru; ' +
  'sohbette geçen ayrıntıları prompta taşı. Prompt İngilizce, tek paragraf ve üretmeye ' +
  'hazır olsun; sahne, kompozisyon, kamera, ışık, renk paleti, malzeme/doku, stil ve ' +
  'ruh hali ayrıntılarını içersin. Promptun bittiği yerde ayrı bir satırda yalnızca ' +
  '"Aspect ratio: Genişlik:Yükseklik" biçiminde, bu sahne için önerdiğin en uygun ' +
  'en-boy oranını yaz (örn. Aspect ratio: 16:9). Başka açıklama ekleme.';
async function sohbetPromptCevir(){
  if(sohbetSuruyor) return;
  if(sohbetEkOkuma) return;
  if(!sohbetGecmis.length){ bilgi('Önce sohbette bir konu konuş'); return; }
  sohbetBaslat(sohbetGecmis, {ek_talimat: SOHBET_PROMPT_TALIMATI});
}

$('sohbetYeniSohbet').onclick = () => { if(!sohbetSuruyor) sohbetYeni(); };
$('sohbetGonder').onclick = () => sohbetSuruyor ? sohbetDurdur() : sohbetGonderMetin($('sohbetGirdi').value.trim());
$('sohbetGirdi').addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){
    e.preventDefault();
    sohbetGonderMetin($('sohbetGirdi').value.trim());
  }
});
$('sohbetGirdi').addEventListener('input', e => {
  e.target.style.height = 'auto';
  e.target.style.height = Math.min(e.target.scrollHeight, 144) + 'px';
});
function sohbetEkleMenuGoster(goster){
  const m = $('sohbetEkleMenu');
  if(!m) return;
  m.hidden = !goster;
  $('sohbetFotoEkle').setAttribute('aria-expanded', goster ? 'true' : 'false');
}
$('sohbetFotoEkle').onclick = () =>
  sohbetEkleMenuGoster($('sohbetEkleMenu').hidden);
$('sohbetEkleDosya').onclick = () => {
  sohbetEkleMenuGoster(false);
  $('sohbetFoto').click();
};
$('sohbetEkleKutuphane').onclick = () => {
  sohbetEkleMenuGoster(false);
  seciciAc(1, 'sohbet');
};
$('sohbetEklePromptCevir').onclick = () => {
  sohbetEkleMenuGoster(false);
  sohbetPromptCevir();
};
/* Çalışma alanı kenar çubukları mobilde overlay, masaüstünde daraltılabilir. */
function calismaMenuBagla(alan, dugme, kenar, kapat, kararti){
  const root = $(alan), btn = $(dugme), side = $(kenar), close = $(kapat), shade = $(kararti);
  if(!root || !btn || !side) return;
  const mobil = () => matchMedia('(max-width: 699px)').matches;
  const setAcik = acik => {
    if(mobil()){
      root.classList.remove('menuler-kapali');
      side.classList.toggle('acik', !!acik);
      if(shade) shade.hidden = !acik;
    }else{
      side.classList.remove('acik');
      root.classList.toggle('menuler-kapali', !acik);
      if(shade) shade.hidden = true;
    }
    btn.setAttribute('aria-expanded', acik ? 'true' : 'false');
  };
  btn.onclick = () => setAcik(btn.getAttribute('aria-expanded') !== 'true');
  if(close) close.onclick = () => setAcik(false);
  if(shade) shade.onclick = () => setAcik(false);
  const media = matchMedia('(max-width: 699px)');
  if(media.addEventListener) media.addEventListener('change', () => setAcik(!mobil()));
  setAcik(!mobil());
}
calismaMenuBagla('wsUretim', 'uretimMenuBtn', 'uretimYanKenar', 'uretimYanKapat', 'uretimKararti');
calismaMenuBagla('wsStudyo', 'studyoMenuBtn', 'studyoYanKenar', 'studyoYanKapat', 'studyoKararti');

function uretimGecmisGenisliginiAyarla(){
  const root = $('wsUretim'), side = $('uretimYanKenar'), handle = $('uretimYanBoyut');
  if(!root || !side || !handle) return;
  const minWidth = 220, preferredMax = 420;
  let width = 280, surukleniyor = false;
  const sinirla = value => Math.round(Math.max(minWidth, Math.min(Math.min(preferredMax, root.clientWidth - 280), value)));
  const uygula = value => {
    width = sinirla(value);
    root.style.setProperty('--uretim-gecmis-genislik', width + 'px');
    handle.setAttribute('aria-valuenow', String(width));
    handle.setAttribute('aria-valuemax', String(Math.min(preferredMax, root.clientWidth - 280)));
  };
  uretimGecmisGenislikUygula = uygula;
  handle.addEventListener('pointerdown', e => {
    if(matchMedia('(max-width: 699px)').matches) return;
    surukleniyor = true;
    handle.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  handle.addEventListener('pointermove', e => {
    if(!surukleniyor) return;
    uygula(e.clientX - side.getBoundingClientRect().left);
  });
  const suruklemeyiBitir = () => {
    if(!surukleniyor) return;
    surukleniyor = false;
    uiAyarKaydet('history_width', width);
  };
  handle.addEventListener('pointerup', suruklemeyiBitir);
  handle.addEventListener('pointercancel', suruklemeyiBitir);
  handle.addEventListener('keydown', e => {
    const min = minWidth, max = Math.min(preferredMax, root.clientWidth - 280);
    let next = width;
    if(e.key === 'ArrowLeft') next -= 16;
    else if(e.key === 'ArrowRight') next += 16;
    else if(e.key === 'Home') next = min;
    else if(e.key === 'End') next = max;
    else return;
    e.preventDefault();
    uygula(next);
    uiAyarKaydet('history_width', width);
  });
  window.addEventListener('resize', () => uygula(width));
  uygula(width);
}
uretimGecmisGenisliginiAyarla();

function uretimArayuzunuKur(){
  const advanced = $('uretimAyarIcerik');
  ['runpodModelWrap','kartRef','videoKart','kartCoz','kartOrn','uretimDestekPanel','uretimNegatifPanel'].forEach(id => {
    const el = $(id); if(el) advanced.appendChild(el);
  });
  $('uretimDestekPanel').hidden = false;
  ['pinBar','pinPanel','pinEklePanel'].forEach(id => { const el = $(id); if(el) advanced.appendChild(el); });
  $('kartPrompt').classList.add('uretim-composer-kart');
  $('uretimComposerMount').appendChild($('kartPrompt'));
  $('uretimIlerlemeMount').appendChild($('kartIs'));
  const durumGozlemci = new MutationObserver(() => {
    const aktif = !!$('ilerleme').textContent.trim() || $('progBlok').style.display !== 'none';
    $('kartIs').classList.toggle('uretim-ilerleme-aktif', $('progBlok').style.display !== 'none');
    $('kartIs').classList.toggle('uretim-durum-goster', aktif);
  });
  durumGozlemci.observe($('kartIs'), {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['style']});
  $('kartIs').classList.remove('uretim-durum-goster');
  $('kartIs').classList.remove('uretim-ilerleme-aktif');
  $('uretimPreview1').append($('oniz'), $('fotoKaldir'));
  $('uretimPreview2').append($('oniz2'), $('foto2Kaldir'));
  $('uretimGoruntuMount').appendChild($('galeri'));
  $('uretimSahne').appendChild($('uretimGorselOnizleme'));
  $('kartSonuc').hidden = true;
  ['foto','foto2'].forEach(id => {
    const input = $(id); if(input){ input.style.display = 'none'; $('uretimGorselMenu').appendChild(input); }
  });
  uretimOnizlemeGuncelle();
}
function uretimOnizlemeGuncelle(){
  $('uretimRefSlot1').hidden = !fotoB64;
  $('uretimRefSlot2').hidden = !fotoB64_2;
  $('uretimGorselOnizleme').hidden = !(fotoB64 || fotoB64_2);
}
function uretimAyarAc(acik=true){
  $('uretimAyarlarPanel').hidden = !acik;
  $('uretimAyarKararti').hidden = !acik;
}
function uretimBosSlotBul(){
  if(!fotoB64) return 1;
  if(!fotoB64_2) return 2;
  return 0;
}
function uretimGorselMenuAc(acik){
  $('uretimGorselMenu').hidden = !acik;
  $('uretimGorselBtn').setAttribute('aria-expanded', acik ? 'true' : 'false');
}
uretimArayuzunuKur();
$('uretimGorselBtn').onclick = () => uretimGorselMenuAc($('uretimGorselMenu').hidden);
$('uretimDosyaSec').onclick = () => {
  uretimGorselMenuAc(false);
  const slot = uretimBosSlotBul();
  if(!slot){ bilgi('En fazla iki referans görsel ekleyebilirsin. Önce birini kaldır.'); return; }
  $('foto' + (slot === 2 ? '2' : '')).click();
};
$('uretimKutuphaneSec').onclick = () => {
  uretimGorselMenuAc(false);
  const slot = uretimBosSlotBul();
  if(!slot){ bilgi('En fazla iki referans görsel ekleyebilirsin. Önce birini kaldır.'); return; }
  seciciAc(slot);
};
$('uretimAyarBtn').onclick = () => uretimAyarAc($('uretimAyarlarPanel').hidden);
$('uretimAyarKapat').onclick = () => uretimAyarAc(false);
$('uretimAyarKararti').onclick = () => uretimAyarAc(false);
function animaModelSecimiDegisti(aktif, diger){
  if(aktif.checked) diger.checked = false;
  uretimModGuncelle();
  formKaydet();
}
$('animaModel').addEventListener('change', () =>
  animaModelSecimiDegisti($('animaModel'), $('moodyAnimaModel')));
$('moodyAnimaModel').addEventListener('change', () =>
  animaModelSecimiDegisti($('moodyAnimaModel'), $('animaModel')));
$('uretimGorselMod').onclick = () => { $('wf').value = 'otomatik'; $('wf').onchange({target: $('wf')}); };
$('uretimVideoMod').onclick = () => { $('wf').value = 'video'; $('wf').onchange({target: $('wf')}); uretimAyarAc(true); };
document.addEventListener('click', e => {
  const menu = $('uretimGorselMenu');
  if(menu && !menu.hidden && !menu.contains(e.target) && e.target !== $('uretimGorselBtn'))
    uretimGorselMenuAc(false);
});
document.addEventListener('keydown', e => {
  if(e.key === 'Escape' && !$('uretimGorselMenu').hidden){
    uretimGorselMenuAc(false);
    $('uretimGorselBtn').focus();
  }
});
document.addEventListener('click', e => {
  const m = $('sohbetEkleMenu');
  if(m && !m.hidden && !m.contains(e.target) && e.target !== $('sohbetFotoEkle'))
    sohbetEkleMenuGoster(false);
});
async function sohbetKutuphanedenSecildi(k){
  const istekNesli = oturumNesli;
  try{
    const r = await fetch(k.url);
    if(istekNesli !== oturumNesli) return;
    if(!r.ok) throw new Error('Görsel yüklenemedi');
    const blob = await r.blob();
    if(istekNesli !== oturumNesli) return;
    const dataUrl = await new Promise((coz, reddet) => {
      const rd = new FileReader();
      rd.onload = () => coz(String(rd.result));
      rd.onerror = () => reddet(new Error('görsel okunamadı'));
      rd.readAsDataURL(blob);
    });
    if(istekNesli !== oturumNesli) return;
    sohbetFotoEkle(dataUrl);
    $('kapla').classList.remove('acik');
    bilgi('Görsel sohbete eklendi');
  }catch(e){
    if(istekNesli === oturumNesli) bilgi('Görsel yüklenemedi');
  }
}
async function psKutuphanedenSecildi(k){
  const istekNesli = oturumNesli;
  try{
    const r = await fetch(k.url);
    if(istekNesli !== oturumNesli) return;
    if(!r.ok) throw new Error('Görsel yüklenemedi');
    const blob = await r.blob();
    if(istekNesli !== oturumNesli) return;
    const dataUrl = await new Promise((coz, reddet) => {
      const rd = new FileReader();
      rd.onload = () => coz(String(rd.result));
      rd.onerror = () => reddet(new Error('görsel okunamadı'));
      rd.readAsDataURL(blob);
    });
    if(istekNesli !== oturumNesli) return;
    const slot = psB64_1 ? (psB64_2 ? 1 : 2) : 1;
    psGorselVeriUygula(slot, dataUrl);
    $('kapla').classList.remove('acik');
    bilgi('Görsel stüdyoya eklendi');
  }catch(e){
    if(istekNesli === oturumNesli) bilgi('Görsel yüklenemedi');
  }
}
/* Panodan gorsel yapistirma: yalniz odaigidaki prompt alanina uygulanir. */
const YAPISTIR_ALANLARI = ['sohbetGirdi', 'prompt', 'psMetin'];
document.addEventListener('paste', e => {
  const alan = document.activeElement;
  if(!alan || !YAPISTIR_ALANLARI.includes(alan.id)) return;
  const dt = e.clipboardData;
  if(!dt) return;
  if((dt.getData('text/plain') || '').trim()) return;
  if(alan.id === 'sohbetGirdi' && Array.from(dt.files).some(f => f.type.startsWith('image/'))){
    e.preventDefault(); void sohbetDosyalariEkle(dt.files); return;
  }
  const dosya = Array.from(dt.files || []).find(f => f.type && f.type.startsWith('image/'));
  if(!dosya) return;
  e.preventDefault();
  const istekNesli = oturumNesli;
  const rd = new FileReader();
  rd.onload = () => {
    if(istekNesli === oturumNesli)
      yapistirilanGorselUygula(alan.id, String(rd.result));
  };
  rd.readAsDataURL(dosya);
});
function yapistirilanGorselUygula(hedefId, dataUrl){
  if(hedefId === 'sohbetGirdi'){
    sohbetFotoEkle(dataUrl);
    bilgi('Panodaki görsel sohbete eklendi');
  }else if(hedefId === 'prompt'){
    gorselSlotUygula(fotoB64 ? 2 : 1, dataUrl, 'panodan');
  }else if(hedefId === 'psMetin'){
    psGorselVeriUygula(psB64_1 ? 2 : 1, dataUrl);
    bilgi('Panodaki görsel stüdyoya eklendi');
  }
}
function psGorselVeriUygula(slot, dataUrl){
  const data = String(dataUrl), b64 = data.split(',')[1];
  const mime = (data.match(/^data:([^;,]+)/) || [])[1] || 'image/jpeg';
  if(!b64) return;
  if(slot === 1){
    if(psURL_1) URL.revokeObjectURL(psURL_1);
    psURL_1 = null; psB64_1 = b64; psMime1 = mime;
    psOnizlemeCiz(1, dataUrl);
  }else{
    if(psURL_2) URL.revokeObjectURL(psURL_2);
    psURL_2 = null; psB64_2 = b64; psMime2 = mime;
    psOnizlemeCiz(2, dataUrl);
  }
  psModGuncelle();
}
async function sohbetDosyalariEkle(dosyalar){
  const nesil = oturumNesli, secim = sohbetSecimIstek;
  if(sohbetSuruyor) return;
  const secilen = Array.from(dosyalar).filter(f => f.type.startsWith('image/'));
  if(!secilen.length) return;
  if(secilen.length + sohbetFotolar.length > 6) bilgi('Bir mesaja en fazla 6 görsel eklenebilir');
  sohbetEkOkuma++;
  $('sohbetGonder').disabled = true;
  try{
    const sonuclar = await Promise.all(secilen.slice(0, 6).map(async f => {
      if(f.size > 6 * 1024 * 1024){ bilgi('Görsel 6 MB sınırını aşıyor'); return null; }
      try{
        return await new Promise((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve(String(r.result));
          r.onerror = reject; r.readAsDataURL(f);
        });
      }catch(e){ bilgi('Görsel okunamadı'); return null; }
    }));
    if(nesil === oturumNesli && secim === sohbetSecimIstek && !sohbetSuruyor)
      sonuclar.filter(Boolean).forEach(sohbetFotoEkle);
  }finally{
    sohbetEkOkuma--;
    $('sohbetGonder').disabled = !sohbetSuruyor && sohbetEkOkuma > 0;
  }
}
$('sohbetFoto').onchange = e => {
  void sohbetDosyalariEkle(e.target.files);
  e.target.value = '';
};
const sohbetBirak = $('sohbetGirdiKutusu');
sohbetBirak.addEventListener('dragover', e => {
  if(e.dataTransfer.types.includes('Files')){ e.preventDefault(); sohbetBirak.classList.add('surukle'); }
});
sohbetBirak.addEventListener('dragleave', e => {
  if(!sohbetBirak.contains(e.relatedTarget)) sohbetBirak.classList.remove('surukle');
});
sohbetBirak.addEventListener('drop', e => {
  if(!e.dataTransfer.files.length) return;
  e.preventDefault(); sohbetBirak.classList.remove('surukle');
  void sohbetDosyalariEkle(e.dataTransfer.files);
});
let sohbetKayitlari = [], sohbetListeIstek = 0, sohbetSecimIstek = 0;
$('sohbetArama').addEventListener('input', sohbetKayitCiz);
function sohbetKayitlariAktiflestir(){
  document.querySelectorAll('.sohbet-kayit').forEach(el =>
    el.classList.toggle('aktif', el.dataset.id === sohbetId));
}
function sohbetBaslik(k){
  if(k.baslik) return String(k.baslik).replace(/\s+/g, ' ').trim();
  const ilk = (k.mesajlar || []).find(m => m.role === 'user');
  if(!ilk) return 'Yeni sohbet';
  if(typeof ilk.content === 'string') return ilk.content.trim().slice(0, 60) || 'Görselli sohbet';
  const metin = Array.isArray(ilk.content) ? ilk.content.find(p => p.type === 'text') : null;
  return (metin && metin.text || 'Görselli sohbet').trim().slice(0, 60);
}
async function sohbetKayitYukle(){
  const hedef = $('sohbetKayitListe');
  if(!hedef) return;
  const nesil = oturumNesli, istek = ++sohbetListeIstek;
  try{
    const r = await fetch('/api/sohbetler', {method: 'POST'});
    if(!r.ok) throw new Error('Geçmiş yüklenemedi');
    const j = await r.json();
    if(nesil !== oturumNesli || istek !== sohbetListeIstek) return;
    sohbetKayitlari = j.sohbetler || [];
    sohbetKayitCiz();
  }catch(e){
    if(nesil !== oturumNesli || istek !== sohbetListeIstek) return;
    hedef.replaceChildren();
    const hata = document.createElement('div');
    hata.className = 'sohbet-kayit-bos';
    hata.textContent = 'Sohbet geçmişi yüklenemedi';
    hedef.appendChild(hata);
  }
}
function sohbetKayitCiz(){
  const hedef = $('sohbetKayitListe');
  const arama = ($('sohbetArama') && $('sohbetArama').value || '').trim().toLocaleLowerCase('tr');
  let liste = sohbetKayitlari;
  if(arama) liste = liste.filter(k => sohbetBaslik(k).toLocaleLowerCase('tr').includes(arama));
  hedef.replaceChildren();
  if(!liste.length){
    const bos = document.createElement('div');
    bos.className = 'sohbet-kayit-bos';
    bos.textContent = arama ? 'Aramayla eşleşen sohbet yok' : 'Henüz sohbet yok';
    hedef.appendChild(bos);
    return;
  }
  liste.forEach(k => {
    const oge = document.createElement('div');
    oge.className = 'sohbet-kayit' + (k.id === sohbetId ? ' aktif' : '');
    oge.dataset.id = k.id;
    const ac = document.createElement('button');
    ac.type = 'button';
    ac.className = 'sohbet-kayit-ac';
    ac.textContent = sohbetBaslik(k);
    ac.title = ac.textContent;
    ac.onclick = () => sohbetSec(k.id);
    const sil = document.createElement('button');
    sil.type = 'button';
    sil.className = 'sohbet-kayit-sil';
    sil.textContent = '×';
    sil.title = 'Sohbeti sil';
    sil.setAttribute('aria-label', 'Sohbeti sil: ' + ac.textContent);
    sil.onclick = async () => {
      if(!confirm('Bu sohbet silinsin mi?')) return;
      try{
        const rr = await fetch('/api/sohbet/sil', {method: 'POST',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: k.id})});
        const jj = await rr.json().catch(() => ({}));
        if(!rr.ok || !jj.silindi) throw new Error(jj.hata || 'Sohbet silinemedi');
        if(sohbetId === k.id) sohbetYeni();
        await sohbetKayitYukle();
      }catch(e){ sohbetDurumSatiri(e.message || 'Sohbet silinemedi'); }
    };
    oge.append(ac, sil);
    hedef.appendChild(oge);
  });
}
async function sohbetSec(id){
  if(sohbetSuruyor) return;
  const nesil = oturumNesli, istek = ++sohbetSecimIstek;
  try{
    const r = await fetch('/api/sohbetler?id=' + encodeURIComponent(id), {method: 'POST'});
    if(!r.ok) throw new Error('Sohbet yüklenemedi');
    const k = await r.json();
    if(nesil !== oturumNesli || istek !== sohbetSecimIstek || sohbetSuruyor) return;
    sohbetGecmis = [];
    sohbetId = k.id;
    sohbetFotoTemizle();
    $('sohbetGirdi').value = '';
    $('sohbetListe').replaceChildren();
    (k.mesajlar || []).forEach(m => {
      let metin = typeof m.content === 'string' ? m.content : '';
      let resimler = m.resim ? [m.resim] : [];
      if(Array.isArray(m.content)){
        const textPart = m.content.find(p => p.type === 'text');
        metin = textPart && textPart.text || '';
        resimler = m.content.filter(p => p.type === 'image_url').map(p => p.image_url?.url).filter(Boolean);
      }
      sohbetEkle(m.role === 'user' ? 'user' : 'assistant', metin, m.thinking, benzersizId(), resimler);
    });
    $('sohbetGonder').disabled = sohbetEkOkuma > 0;
    sohbetBosDurumGuncelle();
    sohbetDurumSatiri(null);
    sohbetMenyuAyarla(false);
    sohbetKayitlariAktiflestir();
  }catch(e){
    if(nesil === oturumNesli && istek === sohbetSecimIstek && !sohbetSuruyor) sohbetDurumSatiri(e.message || 'Sohbet yüklenemedi');
  }
}
/* Prompt geçmişi: üretilen her prompt otomatik düşer. */
function psGecmisiUygula(k){
  $('prompt').value = k.positive || '';
  $('negatif').value = k.negative || '';
  $('uretimNegatifPanel').open = !!String(k.negative || '').trim();
  formKaydet();
  aktifMenu = 'uretim'; menuUygula();
  window.scrollTo({top: 0, behavior: 'smooth'});
  bilgi('Prompt kutusuna uygulandı');
}
function psGecmisDetay(k){
  const satirlar = [
    ['Tarih', k.tarih], ['Mod', k.mod], ['Dil', k.dil],
    ['Sistem promptu', PS_PROFIL_ADLARI[k.model_prompt_profile || 'auto'] || 'Genel'],
    ['BBox', k.bbox_mode ? 'Açık' : null],
    ['Yaratıcılık', k.yaraticilik], ['Uzunluk', k.uzunluk],
    ['Görsel', k.gorsel_sayisi], ['Süre', k.sure_ms ? (k.sure_ms / 1000).toFixed(1) + ' sn' : null],
    ['Yükleme', k.yukleme_ms ? (k.yukleme_ms / 1000).toFixed(1) + ' sn' : null],
    ['Token', k.token], ['Hız', k.tok_s ? (+k.tok_s).toFixed(1) + ' tok/s' : null],
    ['Prompt token', k.prompt_token], ['Deneme', k.deneme], ['ctx', k.ctx]
  ].filter(s => s[1] != null && s[1] !== '');
  const html = '<h3 style="margin-top:0">' + escHtml(k.mod || 'Prompt') + '</h3>' +
    '<div class="kucuk">' + satirlar.map(s => escHtml(s[0]) + ': ' + escHtml(s[1])).join(' · ') + '</div>' +
    (k.metin ? '<div class="kucuk">Girdi: ' + escHtml(k.metin) + '</div>' : '') +
    '<div class="satir"><label>Prompt (EN)</label><pre>' + escHtml(k.positive) + '</pre></div>' +
    '<div class="satir"><label>Prompt (TR)</label><pre>' + escHtml(k.positive_tr) + '</pre></div>' +
    (k.negative ? '<div class="satir"><label>Negatif</label><pre>' + escHtml(k.negative) + '</pre></div>' : '') +
    (k.caption_tr ? '<div class="kucuk">' + escHtml(k.caption_tr) + '</div>' : '') +
    ((k.tags || []).length ? '<div class="kucuk">' + escHtml((k.tags || []).join(' · ')) + '</div>' : '') +
    '<div class="satir"><div><button id="psGecUygula">Prompt kutusuna uygula</button></div>' +
    '<div><button id="psGecSil" class="tehlike kucukbtn">Kaydı sil</button></div></div>';
  kaplaAc(html);
  const pUygula = document.getElementById('psGecUygula');
  const pSil = document.getElementById('psGecSil');
  if(pUygula) pUygula.onclick = () => { $('kapla').classList.remove('acik'); psGecmisiUygula(k); };
  if(pSil) pSil.onclick = () => { $('kapla').classList.remove('acik'); psGecmisSil(k); };
}
async function psGecmisSil(k){
  if(!confirm('Bu prompt kaydı silinsin mi?')) return;
  try{
    await fetch('/api/prompt-gecmisi/sil', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: k.id})});
    psGecmisYukle();
  }catch(e){ bilgi('Silinemedi'); }
}
async function psGecmisYukle(){
  try{
    const q = ($('psGecmisArama') && $('psGecmisArama').value || '').trim();
    const r = await fetch('/api/prompt-gecmisi?limit=200&q=' + encodeURIComponent(q));
    const j = await r.json();
    psGecmisTum = j.kayitlar || [];
    psGecmisTum.sort((a, b) => (b.yildiz ? 1 : 0) - (a.yildiz ? 1 : 0));
    const eslesen = psGecmisFavoriler ? psGecmisTum.filter(k => k.yildiz) : psGecmisTum;
    const perPage = 12, pageCount = Math.max(1, Math.ceil(eslesen.length / perPage));
    psGecmisSayfa = Math.min(psGecmisSayfa, pageCount);
    const liste = eslesen.slice((psGecmisSayfa - 1) * perPage, psGecmisSayfa * perPage);
    const hedef = $('psGecmisListe');
    hedef.innerHTML = '';
    psGecmisSayfaCiz(pageCount);
    if(!liste.length){
      const bos = document.createElement('div');
      bos.className = 'kucuk';
      bos.textContent = psGecmisFavoriler ? 'Favori prompt yok.' : (q || mod ? 'Eşleşen kayıt yok.' : 'Henüz kayıt yok.');
      hedef.appendChild(bos);
      return;
    }
    liste.forEach(k => {
      const oge = document.createElement('div');
      oge.className = 'ps-gecmis-oge' + (k.yildiz ? ' yildizli' : '');
      const yildiz = document.createElement('button');
      yildiz.type = 'button';
      yildiz.className = 'ps-yildiz';
      yildiz.textContent = k.yildiz ? '★' : '☆';
      yildiz.title = k.yildiz ? 'Yıldızı kaldır' : 'Yıldızla';
      yildiz.setAttribute('aria-label', (k.yildiz ? 'Yıldızı kaldır: ' : 'Yıldızla: ') + (k.positive || '').slice(0, 40));
      yildiz.onclick = async e => {
        e.stopPropagation();
        try{
          const r = await fetch('/api/prompt-gecmisi/yildiz', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: k.id, yildiz: !k.yildiz})});
          if(!r.ok) throw new Error('yildizlanamadi');
          psGecmisYukle();
        }catch(err){ bilgi('Yıldız kaydedilemedi'); }
      };
      const metinBlok = document.createElement('div');
      metinBlok.className = 'ps-gecmis-metin';
      const ust = document.createElement('div');
      ust.className = 'kucuk';
      const p = [];
      if(k.tarih) p.push(k.tarih.replace('T', ' '));
      if(k.mod) p.push(k.mod);
      if(k.token) p.push(k.token + ' token');
      if(k.deneme > 1) p.push(k.deneme + ' deneme');
      ust.textContent = p.join(' · ');
      const satir = document.createElement('div');
      satir.className = 'ps-gecmis-satir';
      satir.textContent = (k.positive || '').slice(0, 140) || k.caption_tr || '';
      metinBlok.appendChild(ust);
      metinBlok.appendChild(satir);
      oge.appendChild(yildiz);
      oge.appendChild(metinBlok);
      oge.title = 'Ayrıntı, uygula ve sil için tıkla';
      oge.onclick = () => psGecmisDetay(k);
      hedef.appendChild(oge);
    });
  }catch(e){}
}
function psGecmisSayfaCiz(pageCount){
  const el = $('psGecmisSayfalar');
  if(!el) return;
  el.replaceChildren();
  if(pageCount < 2) return;
  const add = (text, page, current, label) => {
    const b = document.createElement('button'); b.type = 'button';
    b.className = 'hayalet kucukbtn' + (current ? ' aktif' : '');
    b.textContent = text; b.disabled = page < 1 || page > pageCount || page === psGecmisSayfa;
    b.setAttribute('aria-label', label || ('Sayfa ' + page));
    b.onclick = () => { psGecmisSayfa = page; psGecmisYukle(); };
    el.appendChild(b);
  };
  add('‹', psGecmisSayfa - 1, false, 'Önceki sayfa');
  const start = Math.max(1, Math.min(psGecmisSayfa - 2, pageCount - 4));
  for(let p = start; p <= Math.min(pageCount, start + 4); p++) add(String(p), p, p === psGecmisSayfa);
  add('›', psGecmisSayfa + 1, false, 'Sonraki sayfa');
}
$('psGecmisYenile').onclick = psGecmisYukle;
$('psGecmisFavoriBtn').onclick = () => {
  psGecmisFavoriler = !psGecmisFavoriler;
  $('psGecmisFavoriBtn').setAttribute('aria-pressed', psGecmisFavoriler ? 'true' : 'false');
  $('psGecmisFavoriBtn').textContent = psGecmisFavoriler ? '★ Favoriler açık' : '☆ Favoriler';
  psGecmisSayfa = 1; psGecmisYukle();
};
let psAramaZaman = null;
$('psGecmisArama').oninput = () => {
  if(psAramaZaman) clearTimeout(psAramaZaman);
  psAramaZaman = setTimeout(() => { psGecmisSayfa = 1; psGecmisYukle(); }, 250);
};
$('psGecmisTemizle').onclick = async () => {
  if(!confirm('Tüm prompt geçmişi silinsin mi?')) return;
  try{
    await fetch('/api/prompt-gecmisi/temizle', {method: 'POST'});
    psGecmisSayfa = 1;
    psGecmisYukle();
    bilgi('Prompt geçmişi temizlendi');
  }catch(e){ bilgi('Temizlenemedi'); }
};
/* İzleme: GPU/RAM + LLM + ComfyUI. */
let izlemeYukleniyor = false;
const izlemeSayi = new Intl.NumberFormat('tr-TR');
function izlemeMb(deger){
  return deger != null && Number.isFinite(Number(deger))
    ? izlemeSayi.format(Math.round(Number(deger))) + ' MB' : '—';
}
function izlemeOran(barId, dolguId, oran){
  const bar = $(barId), dolgu = $(dolguId);
  const yuzde = oran != null && Number.isFinite(Number(oran))
    ? Math.max(0, Math.min(100, Number(oran))) : null;
  dolgu.style.width = yuzde == null ? '0%' : yuzde + '%';
  if(yuzde == null) bar.removeAttribute('aria-valuenow');
  else bar.setAttribute('aria-valuenow', String(Math.round(yuzde)));
  bar.dataset.tone = yuzde == null ? 'unknown' : yuzde >= 90 ? 'high' : yuzde >= 70 ? 'medium' : 'good';
  return yuzde;
}
function izlemeRozet(id, yazi, ton){
  const el = $(id);
  el.textContent = yazi;
  el.dataset.tone = ton || 'neutral';
}
function izlemeLlmSatir(ad, deger){
  const satir = document.createElement('div');
  const baslik = document.createElement('dt');
  const bilgi = document.createElement('dd');
  baslik.textContent = ad;
  bilgi.textContent = deger == null || deger === '' ? '—' : String(deger);
  satir.append(baslik, bilgi);
  return satir;
}
async function izlemeYukle(){
  if(izlemeYukleniyor) return;
  izlemeYukleniyor = true;
  $('izlemeYenile').disabled = true;
  $('izlemeDurum').textContent = 'Sistem verileri alınıyor…';
  $('izlemeDurum').dataset.state = 'loading';
  try{
    const r = await fetch('/api/sistem');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const ram = j.ram || {};
    const ramToplam = Number(ram.toplam_mb), ramBos = Number(ram.bos_mb);
    const ramVar = ram.toplam_mb != null && ram.bos_mb != null &&
      Number.isFinite(ramToplam) && Number.isFinite(ramBos) && ramToplam > 0;
    const ramKullanilan = ramVar ? Math.max(0, ramToplam - ramBos) : null;
    const ramOran = ramVar ? (ram.yuzde != null ? Number(ram.yuzde) : ramKullanilan / ramToplam * 100) : null;
    $('izlemeRamDeger').textContent = ramVar ? izlemeMb(ramKullanilan) : '—';
    $('izlemeRamDetay').textContent = ramVar
      ? izlemeMb(ramBos) + ' kullanılabilir / ' + izlemeMb(ramToplam) + ' toplam'
      : 'Sistem bellek bilgisi alınamadı';
    const ramYuzde = izlemeOran('izlemeRamBar', 'izlemeRamDolgu', ramOran);
    $('izlemeRamBar').setAttribute('aria-valuetext', ramYuzde == null ? 'Veri yok' : '%' + Math.round(ramYuzde) + ' kullanılıyor');
    izlemeRozet('izlemeRamRozet', ramYuzde == null ? 'VERİ YOK' : '%' + Math.round(ramYuzde) + ' kullanılıyor',
      ramYuzde == null ? 'neutral' : ramYuzde >= 90 ? 'high' : ramYuzde >= 70 ? 'medium' : 'good');

    const cihazlar = Array.isArray(j.comfy_vram) ? j.comfy_vram : [];
    const toplamVram = cihazlar.reduce((toplam, cihaz) => toplam + (Number(cihaz.toplam_mb) || 0), 0);
    const bosVram = cihazlar.reduce((toplam, cihaz) => toplam + (Number(cihaz.bos_mb) || 0), 0);
    const vramVar = !!j.comfy_acik && cihazlar.length > 0 && toplamVram > 0;
    const vramKullanilan = vramVar ? Math.max(0, toplamVram - bosVram) : null;
    const vramOran = vramVar ? vramKullanilan / toplamVram * 100 : null;
    $('izlemeVramDeger').textContent = vramVar ? izlemeMb(vramKullanilan) : '—';
    $('izlemeVramDetay').textContent = vramVar
      ? izlemeMb(bosVram) + ' boş / ' + izlemeMb(toplamVram) + ' toplam'
      : j.comfy_acik ? 'ComfyUI GPU belleği bilgisi yok' : 'ComfyUI bağlantısı yok';
    const vramYuzde = izlemeOran('izlemeVramBar', 'izlemeVramDolgu', vramOran);
    $('izlemeVramBar').setAttribute('aria-valuetext', vramYuzde == null ? 'Veri yok' : '%' + Math.round(vramYuzde) + ' kullanılıyor');
    izlemeRozet('izlemeVramRozet', vramYuzde == null ? 'VERİ YOK' : '%' + Math.round(vramYuzde) + ' kullanılıyor',
      vramYuzde == null ? 'neutral' : vramYuzde >= 90 ? 'high' : vramYuzde >= 70 ? 'medium' : 'good');

    const bekleyen = Number.isFinite(Number(j.kuyruk_bekleyen)) ? Number(j.kuyruk_bekleyen) : 0;
    const aktifIs = !!j.aktif_is_var;
    $('izlemeKuyrukDeger').textContent = aktifIs ? 'İşlem sürüyor' : 'Boşta';
    $('izlemeKuyrukDetay').textContent = aktifIs
      ? (j.llm_is ? 'LLM isteği çalışıyor' : 'Görsel üretimi çalışıyor')
      : 'Şu anda çalışan iş yok';
    $('izlemeKuyrukBekleyen').textContent = izlemeSayi.format(bekleyen);
    izlemeRozet('izlemeKuyrukRozet', aktifIs ? 'AKTİF' : 'HAZIR', aktifIs ? 'medium' : 'good');

    const l = j.llm || {};
    $('izlemeLlmDeger').textContent = l.canli ? 'Çalışıyor' : 'Durduruldu';
    $('izlemeLlmDetay').textContent = l.pid ? 'Süreç kimliği · ' + l.pid : 'Etkin llama-server süreci yok';
    $('izlemeLlmSaglik').textContent = l.saglik || (l.canli ? 'Kontrol ediliyor' : 'Kapalı');
    izlemeRozet('izlemeLlmRozet', l.canli ? 'ÇALIŞIYOR' : 'DURDURULDU', l.canli ? (l.saglik === 'ok' ? 'good' : 'medium') : 'neutral');

    const olcum = l.olcum || {};
    const canliOlcum = olcum.aktif ? [
      olcum.faz || 'Üretim sürüyor',
      olcum.token != null ? izlemeSayi.format(olcum.token) + ' token' : null,
      olcum.tok_s != null ? Number(olcum.tok_s).toFixed(1) + ' token/sn' : null
    ].filter(Boolean).join(' · ') : 'Etkin üretim ölçümü yok';
    const llmSatirlar = [
      ['Süreç kimliği', l.pid],
      ['GPU katmanı', l.ngl != null ? l.ngl + ' katman' : null],
      ['Bağlam boyutu', l.calisan_ctx || l.ctx ? izlemeSayi.format(l.calisan_ctx || l.ctx) + ' token' : null],
      ['Süreç belleği', l.ram_byt != null ? izlemeMb(l.ram_byt / 1048576) : null],
      ['Son model yüklemesi', l.yukleme_ms != null ? (Number(l.yukleme_ms) / 1000).toFixed(1) + ' sn' : null],
      ['Son kullanım', l.son_kullanim_sn != null ? l.son_kullanim_sn + ' sn önce' : null],
      ['İstek durumu', j.llm_is ? (j.llm_is.durum + ' · ' + Math.round(j.llm_is.gecen_sn) + ' sn') : (l.uretim_suruyor ? 'LLM üretimi sürüyor' : 'Boşta')],
      ['Canlı üretim ölçümü', canliOlcum]
    ];
    const detaylar = $('izlemeLlmDetaylari');
    detaylar.replaceChildren(...llmSatirlar.map(([ad, deger]) => izlemeLlmSatir(ad, deger)));
    $('izlemeKomut').textContent = l.komut || 'Başlatma komutu bilgisi yok.';

    const cihazListe = $('izlemeCihazlar');
    cihazListe.replaceChildren();
    if(!j.comfy_acik){
      const bos = document.createElement('div');
      bos.className = 'izleme-bos';
      bos.textContent = 'ComfyUI’ye ulaşılamıyor. GPU verileri ComfyUI bağlantısı kurulduğunda görünür.';
      cihazListe.append(bos);
    }else if(!cihazlar.length){
      const bos = document.createElement('div');
      bos.className = 'izleme-bos';
      bos.textContent = 'ComfyUI bağlı, ancak GPU bellek bilgisi bildirilmedi.';
      cihazListe.append(bos);
    }else{
      cihazlar.forEach((cihaz, sira) => {
        const toplam = Number(cihaz.toplam_mb), bos = Number(cihaz.bos_mb);
        const oran = toplam > 0 && Number.isFinite(bos) ? Math.max(0, Math.min(100, (toplam - bos) / toplam * 100)) : null;
        const kart = document.createElement('article');
        kart.className = 'izleme-cihaz';
        const ust = document.createElement('div');
        ust.className = 'izleme-cihaz-ust';
        const ad = document.createElement('strong');
        ad.textContent = cihaz.ad || ('GPU ' + (sira + 1));
        const oranMetni = document.createElement('span');
        oranMetni.textContent = oran == null ? '—' : '%' + Math.round(oran) + ' kullanım';
        ust.append(ad, oranMetni);
        const bar = document.createElement('div');
        bar.className = 'izleme-bar';
        bar.setAttribute('role', 'progressbar');
        bar.setAttribute('aria-label', (cihaz.ad || 'GPU') + ' bellek kullanımı');
        bar.setAttribute('aria-valuemin', '0');
        bar.setAttribute('aria-valuemax', '100');
        if(oran == null) bar.setAttribute('aria-valuetext', 'Bellek verisi yok');
        else bar.setAttribute('aria-valuenow', String(Math.round(oran)));
        bar.dataset.tone = oran == null ? 'unknown' : oran >= 90 ? 'high' : oran >= 70 ? 'medium' : 'good';
        const dolgu = document.createElement('span');
        dolgu.style.width = oran == null ? '0%' : oran + '%';
        bar.append(dolgu);
        const bellek = document.createElement('div');
        bellek.className = 'izleme-cihaz-bellek';
        bellek.textContent = izlemeMb(Number.isFinite(bos) ? bos : null) + ' boş · ' + izlemeMb(Number.isFinite(toplam) ? toplam : null) + ' toplam';
        kart.append(ust, bar, bellek);
        cihazListe.append(kart);
      });
    }

    $('izlemeDurum').textContent = 'Sistem verileri güncel';
    $('izlemeDurum').dataset.state = 'good';
    $('izlemeSonGuncelleme').textContent = 'Son güncelleme · ' + new Date().toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  }catch(e){
    $('izlemeDurum').textContent = 'Güncellenemedi: ' + (e && e.message ? e.message : e);
    $('izlemeDurum').dataset.state = 'error';
  }finally{
    izlemeYukleniyor = false;
    $('izlemeYenile').disabled = false;
  }
}
let podKurulumEtkin = null;
let podKurulumMesgul = false;
let podKurulumSonDurum = '';
function podKurulumDugme(baslik, veri) {
  const dugme = document.createElement('button');
  dugme.className = 'izleme-yenile';
  dugme.textContent = baslik;
  dugme.onclick = async () => {
    const hata = $('podKurulumHata');
    hata.textContent = '';
    dugme.disabled = true;
    try {
      const yanit = await fetch('/api/pod-kurulum', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(veri)
      });
      if (!yanit.ok) throw new Error((await yanit.json()).hata || 'İşlem tamamlanamadı.');
      await podKurulumYukle();
    } catch (e) {
      hata.textContent = e.message;
    } finally {
      dugme.disabled = false;
    }
  };
  return dugme;
}
async function podKurulumYukle() {
  if (podKurulumEtkin === false || podKurulumMesgul || !BEN || aktifMenu !== 'izleme') return;
  podKurulumMesgul = true;
  const panel = $('podKurulum');
  const hata = $('podKurulumHata');
  try {
    const yanit = await fetch('/api/pod-kurulum', {cache: 'no-store'});
    if (yanit.status === 404) { podKurulumEtkin = false; return; }
    if (!yanit.ok) { panel.hidden = true; return; }
    const bilgi = await yanit.json();
    podKurulumEtkin = true;
    panel.hidden = false;
    const sonDurum = JSON.stringify(bilgi);
    if (podKurulumSonDurum === sonDurum) return;
    podKurulumSonDurum = sonDurum;
    const durumlar = {idle: 'Kurulu değil', queued: 'Sırada', running: 'Hazırlanıyor', paused: 'Duraklatıldı',
      ready: 'Hazır', installed: 'Dosyalar hazır', cancelled: 'İptal edildi',
      error: 'Kurulum tamamlanamadı'};
    const kuyruk = bilgi.sirada;
    const bekleyen = kuyruk.map(id => bilgi.gruplar.find(grup => grup.id === id));
    const gruplar = [...bilgi.gruplar.filter(grup => grup.id === bilgi.aktif),
      ...bekleyen, ...bilgi.gruplar.filter(grup => grup.id !== bilgi.aktif && !kuyruk.includes(grup.id))];
    const satirlar = gruplar.map(grup => {
      const satir = document.createElement('p');
      const index = kuyruk.indexOf(grup.id);
      satir.append((index >= 0 ? (index + 1) + '. ' : '') + grup.ad + ' · ' +
        durumlar[grup.durum] + (grup.ayrinti ? ' · ' + grup.ayrinti : '') + ' ');
      if (bilgi.aktif === grup.id && bilgi.ilerleme) {
        const il = bilgi.ilerleme;
        if (il.file) satir.append(document.createTextNode(
          il.file + (Number.isFinite(il.rate_mib_s) ? ' · ' + il.rate_mib_s.toFixed(1) + ' MiB/sn' : '') + ' '));
      }
      if (index >= 0) {
        for (const [yon, etiket, uygun] of [['yukari', 'Yukarı al', index > 0],
          ['asagi', 'Aşağı al', index < kuyruk.length - 1]]) {
          const dugme = podKurulumDugme(etiket, {grup: grup.id, islem: 'sira', yon});
          dugme.setAttribute('aria-label', grup.ad + ' · ' + etiket);
          dugme.disabled = !uygun;
          satir.append(dugme);
        }
        satir.append(podKurulumDugme('Duraklat', {grup: grup.id, islem: 'duraklat'}));
        satir.append(podKurulumDugme('Sıradan çıkar', {grup: grup.id, islem: 'iptal'}));
      } else if (grup.durum === 'running') {
        satir.append(podKurulumDugme('Duraklat', {grup: grup.id, islem: 'duraklat'}));
        satir.append(podKurulumDugme('İptal et', {grup: grup.id, islem: 'iptal'}));
      } else if (grup.durum === 'paused') {
        satir.append(podKurulumDugme('Devam et', {grup: grup.id, islem: 'devam'}));
        satir.append(podKurulumDugme('İptal et', {grup: grup.id, islem: 'iptal'}));
      } else if (grup.durum !== 'ready' && grup.durum !== 'installed') {
        satir.append(podKurulumDugme(grup.durum === 'error' || grup.durum === 'cancelled'
          ? 'Tekrar dene' : 'Hazırla', {grup: grup.id, islem: 'hazirla'}));
        if (grup.durum === 'error' || grup.durum === 'cancelled')
          satir.append(podKurulumDugme('Kısmi indirmeyi sil', {grup: grup.id, islem: 'kaldir'}));
      } else if (grup.durum === 'installed' && !grup.calisma_hazir) {
        satir.append(podKurulumDugme('Çalışma ortamını başlat',
                                     {grup: grup.id, islem: 'hazirla'}));
        satir.append(podKurulumDugme('Modeli kaldır', {grup: grup.id, islem: 'kaldir'}));
      } else {
        satir.append(podKurulumDugme('Modeli kaldır', {grup: grup.id, islem: 'kaldir'}));
      }
      return satir;
    });
    $('podKurulumGruplar').replaceChildren(...satirlar);
  } catch (e) {
    if (podKurulumEtkin) hata.textContent = e.message;
  } finally {
    podKurulumMesgul = false;
  }
}
setInterval(podKurulumYukle, 2500);
$('izlemeYenile').onclick = () => { izlemeYukle(); podKurulumYukle(); };
$('izlemeBellekBosalt').onclick = async () => {
  const btn = $('izlemeBellekBosalt');
  if(btn.disabled) return;
  btn.disabled = true;
  btn.textContent = 'Bellek boşaltılıyor…';
  $('izlemeDurum').textContent = 'ComfyUI ve llama-server belleği boşaltılıyor; kuyruklar izleniyor…';
  $('izlemeDurum').dataset.state = 'loading';
  try{
    const r = await fetch('/api/sistem/bosalti', {method: 'POST'});
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || ('HTTP ' + r.status));
    const p = ['ComfyUI GPU belleği boşaltıldı'];
    if(j.llm_kapandi) p.push('llama-server kapatıldı');
    bilgi(p.join(' · ') + ' · ' + izlemeMb(j.vram_bos_mb) + ' boş');
    await izlemeYukle();
  }catch(e){
    $('izlemeDurum').textContent = 'Bellek boşaltılamadı: ' + (e && e.message ? e.message : e);
    $('izlemeDurum').dataset.state = 'error';
  }finally{
    btn.disabled = false;
    btn.textContent = 'Belleği boşalt';
  }
};
$('izlemeOtomatik').onchange = () => {
  if(izlemeTimer){ clearInterval(izlemeTimer); izlemeTimer = null; }
  if($('izlemeOtomatik').checked){ izlemeYukle(); izlemeTimer = setInterval(() => {
    if(BEN && aktifMenu === 'izleme' && !document.hidden) izlemeYukle();
  }, 5000); }
};
/* Loglar: llama / comfy / webui, sondan N satır. */
async function logYukle(){
  $('logYenile').disabled = true;
  $('logSatirSayisi').textContent = 'Loglar alınıyor…';
  try{
    const kaynak = $('logKaynak').value;
    const satir = Math.max(10, Math.min(500, Number($('logSatir').value) || 200));
    const r = await fetch('/api/log?kaynak=' + encodeURIComponent(kaynak) + '&satir=' + satir);
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const satirlar = Array.isArray(j.satirlar) ? j.satirlar : [];
    $('logCikti').textContent = satirlar.join('\n') || 'Bu kaynakta gösterilecek log satırı yok.';
    $('logCikti').scrollTop = $('logCikti').scrollHeight;
    $('logSatirSayisi').textContent = izlemeSayi.format(satirlar.length) + ' satır gösteriliyor';
    $('logKaynakEtiketi').textContent = $('logKaynak').selectedOptions[0].textContent;
    $('logKopyala').disabled = satirlar.length === 0;
  }catch(e){
    $('logCikti').textContent = 'Loglar alınamadı: ' + (e && e.message ? e.message : e);
    $('logCikti').scrollTop = $('logCikti').scrollHeight;
    $('logSatirSayisi').textContent = 'Yükleme başarısız';
    $('logKopyala').disabled = true;
  }finally{
    $('logYenile').disabled = false;
  }
}
$('logYenile').onclick = logYukle;
$('logKaynak').onchange = logYukle;
$('logSatir').addEventListener('change', logYukle);
$('logKopyala').onclick = async () => {
  try{ await navigator.clipboard.writeText($('logCikti').textContent); bilgi('Log kopyalandı'); }
  catch(e){ bilgi('Kopyalama reddedildi'); }
};
/* Ayarlar: sistem promptu. */
async function llmSistemYukle(){
  try{
    const r = await fetch('/api/ayarlar'); const j = await r.json();
    if(!r.ok) throw new Error(j.hata || 'HTTP ' + r.status);
    const a = (j && j.ayarlar) || {};
    $('runpodLlmSettings').hidden=!RUNPOD_WEBUI;
    $('llmAdvancedActions').hidden=!RUNPOD_WEBUI;
    $('llmLegacyCtx').hidden=RUNPOD_WEBUI;
    if(RUNPOD_WEBUI){
      $('llmModel').value=a.llm_model||'auto';
      try{
        const state=await (await fetch('/api/durum')).json();
        runpodLlmListesiGuncelle(state,a.llm_model);
        runpodLlmTuningDraft=(a.llm_model_tuning&&typeof a.llm_model_tuning==='object')?
          JSON.parse(JSON.stringify(a.llm_model_tuning)):{};
        Object.keys(runpodLlmTuningDraft).forEach(id=>{
          if(!runpodLlmOptions.some(item=>item.id===id)) delete runpodLlmTuningDraft[id];
        });
        const tuneId=runpodLlmOptions.some(item=>item.id===a.llm_model)?a.llm_model:
          (runpodLlmOptions[0]?.id||'');
        if(!Object.keys(runpodLlmTuningDraft).length&&tuneId){
          const item=runpodLlmOptions.find(value=>value.id===tuneId);
          const legacy=llmTuningVarsayilan(item);
          const isQwen=item?.tuning_family==='qwen';
          if(!isQwen){
            legacy.gpu_layers=a.llm_gpu_layers??legacy.gpu_layers;
            legacy.context=a.llm_ctx??legacy.context;
            legacy.threads=a.llm_threads??legacy.threads;
            legacy.batch=a.llm_batch??legacy.batch;
            legacy.top_p=a.llm_top_p??legacy.top_p;
            legacy.top_k=a.llm_top_k??legacy.top_k;
            legacy.repeat_penalty=a.llm_repeat_penalty??legacy.repeat_penalty;
            legacy.temperature=a.llm_temperature??legacy.temperature;
            legacy.use_mtp=a.llm_use_mtp??legacy.use_mtp;
            legacy.multimodal=a.llm_multimodal??legacy.multimodal;
          }
          runpodLlmTuningDraft[tuneId]=legacy;
        }
        $('llmTuneModel').value=tuneId||$('llmTuneModel').value;
        runpodLlmTuningReady=true;
        llmTuningSeciliModeliYukle();
        const model=state.llm||{};
        const label={llama:'Gemma 4 E4B',llm_12b:'Gemma 4 12B',llm_26b:'Gemma 4 26B A4B'}[model.calisan_model||model.model]||model.calisan_model||model.model||'Model seçilmedi';
        $('llmRuntimeInfo').textContent=model.canli?`Çalışıyor · ${label} · GPU katmanı ${model.ngl} · bağlam ${model.calisan_ctx||model.ctx}`:'Kapalı · Sonraki hazırlamada seçili ayarlar kullanılacak.';
      }catch(_){$('llmRuntimeInfo').textContent='Model durumu henüz alınamadı.';}
    }
    const varsayilanlar = j.llm_varsayilanlar;
    if(!varsayilanlar || typeof varsayilanlar.sistem !== 'string' ||
        typeof varsayilanlar.sohbet !== 'string' || !varsayilanlar.mod)
      throw new Error('Sistem promptu varsayılanları alınamadı');
    llmVarsayilanlar = varsayilanlar;
    if($('llmSistem')) $('llmSistem').value = a.llm_sistem || varsayilanlar.sistem;
    if($('llmSistemSohbet')) $('llmSistemSohbet').value =
      a.llm_sohbet_sistem || varsayilanlar.sohbet;
    if($('llmStreaming')) $('llmStreaming').checked = (a.llm_streaming !== false);
    if($('llmLowBandwidth')) $('llmLowBandwidth').checked = (a.llm_low_bandwidth === true);
    if(a.llm_low_bandwidth === true) document.body.classList.add('low-bandwidth');
    if(a.llm_ctx){
      sohbetBaglamBoyutu = a.llm_ctx;
      if($('llmCtx')) $('llmCtx').value = a.llm_ctx;
      sohbetBaglamGuncelle(null);
    }
    const m = a.llm_mod || {};
    if($('llmModFikir')) $('llmModFikir').value = m.fikir || varsayilanlar.mod.fikir || '';
    if($('llmModGorsel')) $('llmModGorsel').value = m.gorsel || varsayilanlar.mod.gorsel || '';
    if($('llmModBirlestir')) $('llmModBirlestir').value = m.birlestir || varsayilanlar.mod.birlestir || '';
    if($('llmModCeviri')) $('llmModCeviri').value = m.ceviri || varsayilanlar.mod.ceviri || '';
    if($('llmModSohbet')) $('llmModSohbet').value = m.sohbet || varsayilanlar.mod.sohbet || '';
  }catch(e){ bilgi('Model tercihleri yüklenemedi: ' + (e.message || e)); }
}
for(const [id,key] of [['sohbetLlmModel','sohbet_llm_model'],
                        ['psLlmModel','ps_llm_model'],
                        ['uretimLlmModel','uretim_llm_model']]){
  if($(id)) $(id).addEventListener('change',()=>{
    try{localStorage.setItem(key,$(id).value);}catch(_){}
  });
}
$('llmTuneModel').addEventListener('change',()=>{
  llmTuningSeciliModeliKaydet();llmTuningSeciliModeliYukle();
});
for(const id of ['llmGpuLayers','llmCtxModel','llmThreads','llmBatch','llmUbatch',
  'llmFlashAttention','llmCacheK','llmCacheV','llmTopP','llmTopK',
  'llmRepeatPenalty','llmTemperature','llmUseMtp','llmMultimodal',
  'llmReasoning','llmReasoningEffort','llmReasoningBudget','llmMtpTokens']){
  $(id).addEventListener('input',llmTuningSeciliModeliKaydet);
  $(id).addEventListener('change',llmTuningSeciliModeliKaydet);
}
$('llmModelYenidenYukle').onclick=async()=>{
  const info=$('llmApplyInfo'),button=$('llmModelYenidenYukle');button.disabled=true;info.textContent='Ayarlar kaydediliyor…';
  try{
    llmTuningSeciliModeliKaydet();
    const body={llm_model:$('llmModel').value,llm_model_tuning:runpodLlmTuningDraft};
    const saved=await ayarlarGuncelle(body),result=await saved.json().catch(()=>({}));
    if(!saved.ok) throw new Error(result.hata||'Ayarlar kaydedilemedi');
    const response=await fetch('/api/llm/hazirla',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({llm_model:$('llmTuneModel').value})});
    const job=await response.json().catch(()=>({}));if(!response.ok||!job.is_id) throw new Error(job.hata||'Model hazırlama kuyruğa eklenemedi');
    info.textContent='Model hazırlanıyor'+(job.sira?' · sıra '+job.sira:'');llmIsTakipEt(job.is_id,'llm_preload');
  }catch(error){info.textContent='Hata: '+error.message;button.disabled=false;}
};
$('llmCtxKaydet').onclick = async () => {
  const el = $('llmCtxBilgi');
  if(!el) return;
  el.textContent = 'Gönderiliyor...';
  try{
    const r = await ayarlarGuncelle({llm_ctx: +$('llmCtx').value});
    const j = await r.json().catch(() => ({}));
    if(!r.ok){ el.textContent = 'HATA: ' + (j.hata || r.status); return; }
    const u = j.llm_ctx_uygulama || {};
    const deger = (u.istenen != null) ? u.istenen : ((j.ayarlar && j.ayarlar.llm_ctx) || $('llmCtx').value);
    const p = ['Kaydedildi: bağlam ' + deger + ' token'];
    if(u.uygulandi && u.model_yeniden_baslatilacak)
      p.push('çalışan model yeni değerde başlatılacak; bir sonraki işte geçerli');
    else if(u.uygulandi && u.calisan_ctx != null && u.calisan_ctx === deger)
      p.push('çalışan model bu değeri kullanıyor');
    else if(u.uygulandi)
      p.push('çalışan modelde ' + (u.calisan_ctx != null ? u.calisan_ctx : 'eski') + ' token geçerli; yeni değer bir sonraki başlatmada uygulanır');
    else
      p.push('bir sonraki LLM işinde uygulanır');
    el.textContent = p.join(' · ');
    sohbetBaglamBoyutu = deger;
    sohbetBaglamGuncelle(null);
  }catch(e){ el.textContent = 'HATA: ' + e; }
};
function llmPromptKayitDegeri(id, varsayilan){
  const metin = $(id).value.trim();
  return metin === String(varsayilan || '').trim() ? '' : metin;
}
async function llmSistemTercihKaydet(varsayilanaDon){
  const bilgiEl = $('llmSistemBilgi');
  try{
    if(!llmVarsayilanlar) throw new Error('Önce sistem promptu varsayılanları yüklenmeli');
    if(varsayilanaDon){
      $('llmSistem').value = llmVarsayilanlar.sistem;
      $('llmSistemSohbet').value = llmVarsayilanlar.sohbet;
      $('llmModFikir').value = llmVarsayilanlar.mod.fikir || '';
      $('llmModGorsel').value = llmVarsayilanlar.mod.gorsel || '';
      $('llmModBirlestir').value = llmVarsayilanlar.mod.birlestir || '';
      $('llmModCeviri').value = llmVarsayilanlar.mod.ceviri || '';
      $('llmModSohbet').value = llmVarsayilanlar.mod.sohbet || '';
    }
    const govde = {
      llm_sistem: llmPromptKayitDegeri('llmSistem', llmVarsayilanlar.sistem),
      llm_sohbet_sistem: llmPromptKayitDegeri('llmSistemSohbet', llmVarsayilanlar.sohbet),
      llm_streaming: $('llmStreaming') ? $('llmStreaming').checked : true,
      llm_low_bandwidth: $('llmLowBandwidth') ? $('llmLowBandwidth').checked : false,
      llm_mod: {
        fikir: llmPromptKayitDegeri('llmModFikir', llmVarsayilanlar.mod.fikir),
        gorsel: llmPromptKayitDegeri('llmModGorsel', llmVarsayilanlar.mod.gorsel),
        birlestir: llmPromptKayitDegeri('llmModBirlestir', llmVarsayilanlar.mod.birlestir),
        ceviri: llmPromptKayitDegeri('llmModCeviri', llmVarsayilanlar.mod.ceviri),
        sohbet: llmPromptKayitDegeri('llmModSohbet', llmVarsayilanlar.mod.sohbet)}};
    const r = await ayarlarGuncelle(govde);
    const j = await r.json().catch(() => ({}));
    if(!r.ok) throw new Error(j.hata || r.status);
    bilgiEl.textContent = varsayilanaDon ? 'Varsayılan promptlar geri yüklendi.' : 'Kaydedildi.';
  }catch(e){ bilgiEl.textContent = 'HATA: ' + (e.message || e); }
}
$('llmSistemKaydet').onclick = () => llmSistemTercihKaydet(false);
$('llmSistemVarsayilan').onclick = () => llmSistemTercihKaydet(true);
const llmPromptVarsayilanAlanlari = {
  llmSistem: () => llmVarsayilanlar.sistem,
  llmSistemSohbet: () => llmVarsayilanlar.sohbet,
  llmModFikir: () => llmVarsayilanlar.mod.fikir,
  llmModGorsel: () => llmVarsayilanlar.mod.gorsel,
  llmModBirlestir: () => llmVarsayilanlar.mod.birlestir,
  llmModCeviri: () => llmVarsayilanlar.mod.ceviri,
  llmModSohbet: () => llmVarsayilanlar.mod.sohbet
};
document.querySelectorAll('[data-llm-varsayilan]').forEach(btn => {
  btn.onclick = () => {
    if(!llmVarsayilanlar){
      $('llmSistemBilgi').textContent = 'HATA: Varsayılan promptlar yüklenemedi.';
      return;
    }
    const id = btn.getAttribute('data-llm-varsayilan');
    const varsayilanOku = llmPromptVarsayilanAlanlari[id];
    if(!varsayilanOku || !$(id)) return;
    $(id).value = varsayilanOku();
    llmSistemTercihKaydet(false);
  };
});
if($('llmLowBandwidth')){
  $('llmLowBandwidth').onchange = ()=>{
    if($('llmLowBandwidth').checked){
      document.body.classList.add('low-bandwidth');
    }else{
      document.body.classList.remove('low-bandwidth');
    }
  };
}
let lbImg=null, lbListe=[], lbIdx=0, lbTamEkran=false;
let lbZoom=1, lbOffsetX=0, lbOffsetY=0, lbPinchMesafe=0, lbPinchDurum=null, lbPanDurum=null;
function lbUstOlustur(){
  if(document.getElementById('lb')){ lbImg=document.getElementById('lbImg'); return; }
  const lb=document.createElement('div'); lb.id='lb';
  lb.innerHTML='<button id="lbKapat" aria-label="Kapat">✕</button>' +
    '<div id="lbSahne"><img id="lbImg" draggable="false" alt=""></div>' +
    '<div id="lbBilgi"></div>' +
    '<button id="lbOnceki" class="lb-yon" aria-label="Önceki">‹</button>' +
    '<button id="lbSonraki" class="lb-yon" aria-label="Sonraki">›</button>';
  document.body.appendChild(lb);
  lbImg=document.getElementById('lbImg');
  document.getElementById('lbKapat').onclick=lbKapat;
  document.getElementById('lbOnceki').onclick=e=>{e.stopPropagation();lbGit(-1);};
  document.getElementById('lbSonraki').onclick=e=>{e.stopPropagation();lbGit(1);};
  const sahne=document.getElementById('lbSahne');
  sahne.addEventListener('click', e=>{
    if(e.target!==lbImg) return;
    const r=sahne.getBoundingClientRect();
    const x=e.clientX-r.left;
    if(x < r.width*0.3) lbGit(-1);
    else if(x > r.width*0.7) lbGit(1);
    else lbTamEkranDegistir();
  });
  /* Zoom/pan: tekerlek ile buyut, surukleyerek kaydir, cift tikla sifirla. */
  sahne.addEventListener('wheel', e=>{
    if(!lbTamEkran) return;
    e.preventDefault();
    const adim = e.deltaY < 0 ? 1.18 : 1/1.18;
    lbZoom = Math.max(1, Math.min(8, lbZoom * adim));
    if(lbZoom === 1){ lbOffsetX = 0; lbOffsetY = 0; }
    lbZoomUygula();
  }, {passive: false});
  sahne.addEventListener('mousedown', e=>{
    if(!lbTamEkran || e.target!==lbImg || e.button!==0) return;
    lbPanDurum = {x: e.clientX, y: e.clientY, offsetX: lbOffsetX, offsetY: lbOffsetY};
    e.preventDefault();
  });
  window.addEventListener('mousemove', e=>{
    if(!lbPanDurum) return;
    lbOffsetX = lbPanDurum.offsetX + (e.clientX - lbPanDurum.x);
    lbOffsetY = lbPanDurum.offsetY + (e.clientY - lbPanDurum.y);
    lbZoomUygula();
  });
  window.addEventListener('mouseup', ()=>{ lbPanDurum = null; });
  sahne.addEventListener('touchstart', e=>{
    if(!lbTamEkran) return;
    if(e.touches.length === 2){
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      lbPinchMesafe = Math.hypot(dx, dy);
      lbPinchDurum = {scale: lbZoom};
    }else if(e.touches.length === 1 && lbZoom > 1){
      lbPanDurum = {x: e.touches[0].clientX, y: e.touches[0].clientY,
        offsetX: lbOffsetX, offsetY: lbOffsetY};
    }
  }, {passive: true});
  sahne.addEventListener('touchmove', e=>{
    if(!lbTamEkran) return;
    if(e.touches.length === 2 && lbPinchDurum){
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      const d = Math.hypot(dx, dy);
      lbZoom = Math.max(1, Math.min(8, lbPinchDurum.scale * d / (lbPinchMesafe || 1)));
      if(lbZoom === 1){ lbOffsetX = 0; lbOffsetY = 0; }
      lbZoomUygula();
      e.preventDefault();
    }else if(e.touches.length === 1 && lbPanDurum && lbZoom > 1){
      lbOffsetX = lbPanDurum.offsetX + (e.touches[0].clientX - lbPanDurum.x);
      lbOffsetY = lbPanDurum.offsetY + (e.touches[0].clientY - lbPanDurum.y);
      lbZoomUygula();
      e.preventDefault();
    }
  }, {passive: false});
  sahne.addEventListener('touchend', ()=>{
    if(!lbPinchMesafe){ lbPanDurum = null; return; }
    lbPinchMesafe = 0; lbPinchDurum = null; lbPanDurum = null;
  }, {passive: true});
  lb.addEventListener('click', e=>{ if(e.target===lb) lbKapat(); });
}
function lbZoomUygula(){
  if(!lbImg) return;
  const sahne = document.getElementById('lbSahne');
  if(!sahne) return;
  const maxX = Math.max(0, (lbImg.offsetWidth * lbZoom - sahne.clientWidth) / 2);
  const maxY = Math.max(0, (lbImg.offsetHeight * lbZoom - sahne.clientHeight) / 2);
  lbOffsetX = Math.max(-maxX, Math.min(maxX, lbOffsetX));
  lbOffsetY = Math.max(-maxY, Math.min(maxY, lbOffsetY));
  lbImg.style.transform = 'translate3d(' + lbOffsetX + 'px,' + lbOffsetY + 'px,0) scale(' + lbZoom + ')';
}
function lbTamEkranDegistir(){
  /* Döngü: normal (contain) -> saf (büyütülebilir) -> normal. */
  lbTamEkran=!lbTamEkran;
  const lb=document.getElementById('lb'); if(!lb) return;
  lb.classList.toggle('tam-ekran', lbTamEkran);
  if(!lbTamEkran){ lbZoom = 1; lbOffsetX = 0; lbOffsetY = 0; }
  lbZoomUygula();
  const kapat=document.getElementById('lbKapat');
  if(kapat) kapat.hidden=lbTamEkran;
  lbBilgiCiz();
}
function lbBilgiCiz(){
  const el=document.getElementById('lbBilgi'); if(!el) return;
  if(lbTamEkran){ el.innerHTML=''; el.hidden=true; return; }
  el.hidden=false;
  const k=lbListe[lbIdx]||{};
  const p=[];
  if(k.ad) p.push('<b>'+escHtml(k.ad)+'</b>');
  if(k.boyut) p.push(k.boyut.join(' × '));
  if(k.bayt!=null) p.push(boyutYaz(k.bayt));
  if(k.bicim) p.push(escHtml(k.bicim));
  if(k.tarih) p.push(String(k.tarih).replace('T',' '));
  if(k.kaynak_sayisi>1) p.push(k.kaynak_sayisi+' kopya');
  if(k.kaynaklar && k.kaynaklar.length){
    const y=k.kaynaklar.map(x=>{
      const t=x.tur==='ice-aktarim'?'İçe aktarılan':(x.tur==='kutuphane'?'Kütüphane':(x.tur==='gecmis'?'Geçmiş':'Sohbet'));
      return t+': '+x.ad;
    });
    p.push('<span class="lb-kaynak">'+y.map(escHtml).join('<br>')+'</span>');
  }
  let eylem='';
  if(k.hash){
    eylem+='<button id="lbReferans" class="kucukbtn">Referans olarak ekle</button>';
    eylem+='<button id="lbArsivSil" class="tehlike kucukbtn">Arşiv kopyasını sil</button>';
  }else if(k.ad){
    eylem+='<button id="lbReferans" class="kucukbtn">Referans olarak ekle</button>';
    eylem+='<button id="lbLibSil" class="tehlike kucukbtn">Kütüphaneden sil</button>';
  }
  el.innerHTML=p.join(' · ')+(eylem?'<div class="lb-eylem">'+eylem+'</div>':'');
  const ref=document.getElementById('lbReferans');
  if(ref) ref.onclick=async()=>{
    const slot=uretimBosSlotBul();
    if(!slot){ bilgi('İki referans görsel ekli. Önce birini kaldır.'); return; }
    await libAta({url:k.url||k.thumb}, slot);
    lbKapat();
    bilgi('Referans olarak eklendi');
  };
  const sil1=document.getElementById('lbArsivSil');
  if(sil1) sil1.onclick=async()=>{
    if(!confirm('Bu fotoğrafın arşiv kopyası silinsin mi? Diğer bölümlerdeki kayıtlar etkilenmez.')) return;
    try{
      const r=await fetch('/api/arsiv/sil',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({hash:k.hash})});
      const j=await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(j.hata||('HTTP '+r.status));
      lbKapat();
      await arsivYukle(true);
    }catch(e){ bilgi('Silinemedi: '+(e.message||e)); }
  };
  const sil2=document.getElementById('lbLibSil');
  if(sil2) sil2.onclick=async()=>{
    if(!confirm('Silinsin mi? '+k.ad)) return;
    try{
      const r=await fetch('/api/kutuphane/sil',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ad:k.ad})});
      const j=await r.json().catch(()=>({}));
      if(!r.ok) throw new Error(j.hata||('HTTP '+r.status));
      lbKapat();
      await kutuphaneYukle();
    }catch(e){ bilgi('Silinemedi: '+(e.message||e)); }
  };
}
function lbGoster(){
  const k=lbListe[lbIdx]; if(!k) return;
  lbZoom = 1; lbOffsetX = 0; lbOffsetY = 0;
  lbImg.src=k.url||k.thumb||'';
  lbImg.alt=k.ad||'Görsel';
  lbImg.style.transform='scale(1)';
  const lb=document.getElementById('lb');
  const kapat=document.getElementById('lbKapat');
  if(kapat) kapat.hidden=lbTamEkran;
  lbBilgiCiz();
}
function lbAcListe(liste, idx){
  lbUstOlustur();
  lbListe=Array.isArray(liste)?liste:[];
  lbIdx=Math.max(0,Math.min(idx|0,lbListe.length-1));
  lbTamEkran=false;
  const lb=document.getElementById('lb');
  lb.classList.remove('tam-ekran');
  lb.classList.add('acik');
  document.body.classList.add('lb-kitli');
  lbGoster();
}
function lbGit(delta){
  if(!lbListe.length) return;
  lbIdx=(lbIdx+delta+lbListe.length)%lbListe.length;
  lbGoster();
}
function lbAc(src){
  lbAcListe([{url:src,ad:''}], 0);
}
function lbKapat(){
  const lb=document.getElementById('lb'); if(!lb) return;
  lb.classList.remove('acik'); lb.classList.remove('tam-ekran');
  document.body.classList.remove('lb-kitli');
  lbTamEkran=false; lbListe=[]; lbIdx=0;
  if(lbImg) lbImg.src='';
}
document.addEventListener('keydown', e=>{
  const lb=document.getElementById('lb');
  if(!lb || !lb.classList.contains('acik')) return;
  if(e.key==='Escape') lbKapat();
  else if(e.key==='ArrowLeft') lbGit(-1);
  else if(e.key==='ArrowRight') lbGit(1);
});
let sonScrollY=0;
window.addEventListener('scroll',()=>{
  const ust=document.querySelector('header.ust'); if(!ust) return;
  const y=window.scrollY||window.pageYOffset;
  if(y>sonScrollY && y>50) ust.classList.add('gizli');
  else ust.classList.remove('gizli');
  sonScrollY=y;
},{passive:true});
$('backupExport').onclick=async()=>{
  const button=$('backupExport'),status=$('backupExportStatus');
  const password=RUNPOD_WEBUI ? window.prompt('Tüm sistem yedeği için bir parola belirle. Geri yüklerken aynı parola gerekecek.') : '';
  if(RUNPOD_WEBUI && !password)return;
  button.disabled=true;status.textContent=RUNPOD_WEBUI?'Tüm hesaplar mühürleniyor ve sistem yedeği şifreleniyor…':'Kasa şifrelenip yedekleniyor…';
  try{
    const response=await fetch('/api/backup-export',{method:'POST',cache:'no-store',headers:RUNPOD_WEBUI?{'X-Backup-Password':password}:{}});
    if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.hata||'HTTP '+response.status);}
    const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=RUNPOD_WEBUI?'webui-system.wuis.gpg':'webui-vault.wui.gpg';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
    status.textContent=RUNPOD_WEBUI?'Tüm sistem yedeği AES-256 ile şifrelendi ve indirildi.':'Şifreli kullanıcı yedeği indirildi.';
  }catch(error){status.textContent='Yedek alınamadı: '+error.message;}
  finally{button.disabled=false;}
};
parolaGozleri(); cozunurlukGorunumu(); oranKilitGuncelle(); uretimIcGorunum(); uretimModGuncelle(); gecGorunumUygula(); lbUstOlustur(); sinemaTemasiIzlemeyiBaslat();
$('baslangicYenidenDene').onclick = boot;
boot();
