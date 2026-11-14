import { useEffect, useMemo, useState } from "react";
import { ArrowUp, Check, ChevronDown, Image, LoaderCircle, SlidersHorizontal, Square, Video, X } from "lucide-react";
import type { JsonRecord, ModelInstance, ProductionApi } from "../../lib/api/client";

type Phase = "loading" | "setup" | "login" | "vault" | "ready" | "error";
type Mode = "image" | "video";
type Job = { id: string; status: string; progress?: number; message?: string; result?: JsonRecord };

interface Props { api: ProductionApi; }

function ShellNav({ legacy }: { legacy: string }) {
  return <header className="prod-topbar"><a className="prod-brand" href="/?ui=next"><span className="prod-brand__mark">M</span><span>Moody Studio<small>CREATIVE WORKSPACE</small></span></a>
    <nav className="prod-nav" aria-label="Çalışma alanı"><a className="prod-nav__active" href="#production" aria-current="page">Üretim</a><a href={legacy}>Prompt stüdyosu</a><a href={legacy}>Kütüphane</a><a href={legacy}>Sohbet</a></nav>
    <a className="prod-legacy-link" href={legacy}>Eski arayüz <span aria-hidden="true">↗</span></a></header>;
}

function AuthGate({ phase, refresh, api, vaultMissing, vaultRecover }: { phase: Phase; refresh: () => void; api: ProductionApi; vaultMissing: boolean; vaultRecover: boolean }) {
  const [user, setUser] = useState(""), [password, setPassword] = useState(""), [password2, setPassword2] = useState(""), [ackRisk, setAckRisk] = useState(false), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      if (phase === "setup") {
        await api.post("/api/kurulum", { kullanici: user.trim(), sifre: password });
        await api.post("/api/giris", { kullanici: user.trim(), sifre: password });
      }
      else if (phase === "login") await api.post("/api/giris", { kullanici: user.trim(), sifre: password });
      else if (phase === "vault") {
        if (vaultMissing && password !== password2) throw new Error("Kasa parolaları aynı olmalı.");
        if (vaultRecover) await api.post("/api/kasa/kurtar", { parola: password });
        else if (vaultMissing) await api.post("/api/kasa/olustur", { parola: password, parola2: password2, riski_anladim: ackRisk });
        else await api.post("/api/kasa/ac", { parola: password });
      }
      setPassword(""); setPassword2(""); refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "İşlem tamamlanamadı."); }
    finally { setBusy(false); }
  };
  const title = phase === "setup" ? "İlk hesabını oluştur" : phase === "login" ? "Tekrar hoş geldin" : phase === "vault" ? vaultRecover ? "Kasayı kurtar" : vaultMissing ? "Kasa oluştur" : "Kasayı aç" : "WebUI hazırlanıyor";
  return <main className="auth-page"><form className="auth-card" onSubmit={submit}>
    <span className="auth-mark">M</span><p className="eyebrow">MOODY STUDIO</p><h1>{title}</h1>
    <p className="auth-copy">{phase === "setup" ? "Hesabın ve kasa parolan bu cihazda saklanır." : phase === "login" ? "Devam etmek için hesabına giriş yap." : phase === "vault" ? vaultRecover ? "Tamamlanmamış kasa kurtarma işlemini sürdür." : vaultMissing ? "Yeni şifreli çalışma alanının parolasını belirle." : "Şifreli çalışma alanına erişmek için kasa parolanı gir." : "Sunucuya ulaşılamıyor."}</p>
    {(phase === "setup" || phase === "login") && <label>Kullanıcı adı<input autoComplete="username" value={user} onChange={e=>setUser(e.target.value)} required /></label>}
    {phase !== "error" && phase !== "loading" && <label>{phase === "vault" ? vaultMissing ? "Yeni kasa parolası" : "Kasa parolası" : "Parola"}<input type="password" autoComplete={phase === "login" || (phase === "vault" && !vaultMissing) ? "current-password" : "new-password"} value={password} onChange={e=>setPassword(e.target.value)} required /></label>}
    {phase === "vault" && vaultMissing && <><label>Yeni kasa parolasını doğrula<input type="password" autoComplete="new-password" value={password2} onChange={e=>setPassword2(e.target.value)} required /></label><label className="ack-risk"><input type="checkbox" checked={ackRisk} onChange={e=>setAckRisk(e.target.checked)}/> Parola gücü uyarısını anladım.</label></>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {phase === "error" ? <button type="button" className="primary-button" onClick={refresh}>Yeniden dene</button> : <button className="primary-button" disabled={busy}>{busy ? "Kontrol ediliyor…" : "Devam et"}</button>}
  </form></main>;
}

function MediaCanvas({ mode, job }: { mode: Mode; job: Job | null }) {
  const media = (job?.result ?? {}) as JsonRecord;
  const firstImage = Array.isArray(media.gorseller) ? media.gorseller[0] : undefined;
  const src = (media.video_url ?? firstImage ?? media.url ?? media.src ?? media.image_url) as string | undefined;
  const poster = (media.video_poster_url ?? media.poster_url ?? media.poster) as string | undefined;
  const video = mode === "video" || Boolean(media.video_url);
  return <section className="media-stage" aria-label="Üretim sonucu">
    {src ? video ? <video className="media-stage__media" src={src} poster={poster} controls playsInline /> : <img className="media-stage__media" src={src} alt="Üretilen sonuç" /> :
      <div className="media-empty"><span className="media-empty__icon">{mode === "video" ? <Video /> : <Image />}</span><p className="eyebrow">YARATIM ALANI</p><h1>{job?.status === "running" ? "Sonucun hazırlanıyor" : "Aklındakini hayata geçir"}</h1><p>{job?.message || "Bir açıklama yaz, modelini seç ve üretime başla."}</p><div className="canvas-hint"><span /> Sonuçların burada görünecek</div></div>}
    {job?.status === "error" && job.message && <p className="workspace-error" role="alert">{job.message}</p>}
    {job && (["running","queued","stopping"].includes(job.status)) && <div className="job-progress" role="status"><div><LoaderCircle className="spin" size={16}/><span>{job.message || (job.status === "queued" ? "Sırada bekliyor" : "Üretiliyor")}</span><b>{Math.round((job.progress ?? 0) * 100)}%</b></div><div className="job-progress__track"><span style={{ width: `${Math.max(0, Math.min(100, (job.progress ?? 0.04) * 100))}%` }}/></div></div>}
  </section>;
}

function Composer({ mode, setMode, prompt, setPrompt, busy, onSubmit, onCancel, file, setFile, onControls }: {
  mode: Mode; setMode: (m: Mode)=>void; prompt: string; setPrompt: (p:string)=>void; busy:boolean; onSubmit:()=>void; onCancel:()=>void;
  file: File | null; setFile: (f:File|null)=>void; onControls:()=>void;
}) {
  return <section className="composer" aria-label="Üretim isteği"><div className="composer__modes" role="tablist" aria-label="Üretim türü">
    <button role="tab" aria-selected={mode === "image"} className={mode === "image" ? "selected" : ""} onClick={()=>setMode("image")}><Image size={15}/> Görsel</button>
    <button role="tab" aria-selected={mode === "video"} className={mode === "video" ? "selected" : ""} onClick={()=>setMode("video")}><Video size={15}/> Video</button>
  </div><div className="composer__entry">
    <label className="composer__prompt-label" htmlFor="production-prompt">İsteğini yaz</label>
    <textarea id="production-prompt" value={prompt} onChange={e=>setPrompt(e.target.value)} rows={2} placeholder={mode === "image" ? "Görselini tarif et…" : "Videoda neler olsun?"} disabled={busy} />
    <div className="composer__actions"><label className="icon-button file-button" title="Referans görsel ekle"><Image size={17}/><input type="file" accept="image/*" onChange={e=>setFile(e.target.files?.[0] ?? null)} /></label>
      {file && <span className="file-chip">{file.name}<button aria-label="Referans görseli kaldır" onClick={()=>setFile(null)}><X size={13}/></button></span>}
      <button className="icon-button control-trigger" onClick={onControls}><SlidersHorizontal size={16}/> <span>Ayarlar</span></button>
      {busy ? <button className="send-button send-button--stop" onClick={onCancel} aria-label="Üretimi durdur"><Square size={15} fill="currentColor"/> Durdur</button> : <button className="send-button" onClick={onSubmit} disabled={!prompt.trim()}><ArrowUp size={17}/><span>Üret</span></button>}
    </div></div></section>;
}

function Controls({ mode, setMode, instances, selected, setSelected, width, setWidth, height, setHeight, steps, setSteps, videoSeconds, setVideoSeconds, negative, setNegative, mobile, close }: {
  mode:Mode;setMode:(m:Mode)=>void;instances:ModelInstance[];selected:string;setSelected:(s:string)=>void;width:number;setWidth:(n:number)=>void;height:number;setHeight:(n:number)=>void;steps:number;setSteps:(n:number)=>void;videoSeconds:number;setVideoSeconds:(n:number)=>void;negative:string;setNegative:(s:string)=>void;mobile:boolean;close:()=>void;
}) {
  const choices = instances.filter(item => item.family.toLowerCase().includes(mode === "image" ? "image" : "video") || (mode === "image" && item.engine === "comfy" && !item.family.toLowerCase().includes("video")) || (mode === "video" && item.family.toLowerCase().includes("wan")));
  return <><button className={`panel-scrim${mobile ? " is-open" : ""}`} aria-label="Ayarları kapat" onClick={close}/><aside className={`control-panel${mobile ? " control-panel--open" : ""}`} aria-label="Üretim ayarları">
    <div className="control-panel__head"><div><p className="eyebrow">KONTROLLER</p><h2>Üretim ayarları</h2></div><button className="icon-button close-controls" aria-label="Ayarları kapat" onClick={close}><X size={18}/></button></div>
    <div className="control-panel__body"><div className="control-mode"><button className={mode === "image" ? "active" : ""} onClick={()=>setMode("image")}><Image size={15}/> Görsel</button><button className={mode === "video" ? "active" : ""} onClick={()=>setMode("video")}><Video size={15}/> Video</button></div>
      <label className="field-label">{mode === "image" ? "Görsel modeli" : "Video modeli"}<span className="select-wrap"><select value={selected} onChange={e=>setSelected(e.target.value)} aria-label="Model seçimi">
        <option value="">Model seç</option>{choices.map(item=><option key={item.id} value={item.id} disabled={!item.ready}>{item.name}{!item.ready ? " · Kuruluyor" : ""}</option>)}
      </select><ChevronDown size={15}/></span></label>
      {choices.length === 0 && <p className="field-note">Bu tür için model listelenmedi. Modelleri Kurucu’dan ekleyebilirsin.</p>}
      {selected && !instances.find(i=>i.id===selected)?.ready && <p className="field-note" role="status">Bu model henüz hazır değil.</p>}
      {mode === "image" ? <div className="field-grid"><label className="field-label">Genişlik<input type="number" min={64} step={16} value={width} onChange={e=>setWidth(Number(e.target.value))}/></label><label className="field-label">Yükseklik<input type="number" min={64} step={16} value={height} onChange={e=>setHeight(Number(e.target.value))}/></label></div> : <label className="field-label">Klip süresi · saniye<input type="number" min={0.5} max={30} step={0.5} value={videoSeconds} onChange={e=>setVideoSeconds(Number(e.target.value))}/></label>}
      <label className="field-label">Adım sayısı<input type="number" min={1} max={100} value={steps} onChange={e=>setSteps(Number(e.target.value))}/></label>
      {mode === "image" && <label className="field-label">Negatif prompt<textarea className="negative-prompt" value={negative} onChange={e=>setNegative(e.target.value)} rows={3} placeholder="İstenmeyen ayrıntıları yaz"/></label>}
      <p className="control-note"><Check size={14}/> Model seçenekleri sunucunun hazır durumuna göre güncellenir.</p></div>
  </aside></>;
}

export function ProductionWorkspace({ api }: Props) {
  const [phase,setPhase]=useState<Phase>("loading"),[instances,setInstances]=useState<ModelInstance[]>([]),[vaultMissing,setVaultMissing]=useState(false),[vaultRecover,setVaultRecover]=useState(false),[mode,setMode]=useState<Mode>("image"),[prompt,setPrompt]=useState(""),[negative,setNegative]=useState(""),[selectedImage,setSelectedImage]=useState(""),[selectedVideo,setSelectedVideo]=useState(""),[width,setWidth]=useState(1024),[height,setHeight]=useState(1024),[steps,setSteps]=useState(8),[videoSeconds,setVideoSeconds]=useState(2),[file,setFile]=useState<File|null>(null),[job,setJob]=useState<Job|null>(null),[controlsOpen,setControlsOpen]=useState(false),[loadingError,setLoadingError]=useState("");
  const selected=mode==="image"?selectedImage:selectedVideo;
  const setSelected=(value:string)=>mode==="image"?setSelectedImage(value):setSelectedVideo(value);
  const legacy=useMemo(()=>`${window.location.origin}/`,[]);
  const refresh=async()=>{setPhase("loading");setLoadingError("");try{
    const install=await api.setup();
    if(install.kurulu===false){setPhase("setup");return;}
    try{await api.me();}catch(e){if(e instanceof Error&&e.message.includes("HTTP 401")){setPhase("login");return;}throw e;}
    const vault=await api.vault();
    if(!vault.acik){setVaultMissing(vault.kasa_var===false);setVaultRecover(vault.kurtarma===true);setPhase("vault");return;}
    const [status,settings]=await Promise.all([api.status(),api.settings().catch(()=>({} as JsonRecord))]);
    const modelList=Array.isArray(status.model_instances)?status.model_instances as ModelInstance[]:[];
    setInstances(modelList);
    const defaults=(settings.varsayilanlar ?? settings) as JsonRecord;
    const initial=(defaults.image_model_instance ?? defaults.model_instance ?? modelList.find(m=>m.ready&&m.engine==="comfy"&&!m.family.toLowerCase().includes("video"))?.id ?? "") as string;
    setSelectedImage(current=>current || initial);
    setSelectedVideo(current=>current || modelList.find(m=>m.ready&&m.family.toLowerCase().includes("video"))?.id || "");
    setPhase("ready");
  }catch(e){setLoadingError(e instanceof Error?e.message:"Sunucuya bağlanılamadı.");setPhase("error");}};
  useEffect(()=>{void refresh();},[]);
  useEffect(()=>{if(phase!=="ready"||!job?.id||!["queued","running","stopping"].includes(job.status))return;
    let cancelled=false;const poll=async()=>{try{const data=await api.get<JsonRecord>(`/api/is/${encodeURIComponent(job.id)}`);if(cancelled)return;
      const progress=data.ilerleme as JsonRecord|undefined;
      const state=String(data.durum??"");
      const mapped=state==="kuyrukta"?"queued":state==="calisiyor"?"running":state==="bitti"?"done":state==="hata"?"error":state==="durduruldu"?"cancelled":state;
      const rawProgress=Number(progress?.oran??0);
      const next:Job={...job,status:mapped,progress:rawProgress,message:String(data.hata??progress?.calisan??(state==="kuyrukta"?"Sırada bekliyor":"Üretiliyor")),result:data.sonuc as JsonRecord|undefined};setJob(next);
    }catch(e){if(!cancelled)setJob({...job,message:e instanceof Error?e.message:"İş durumu alınamadı."});}};
    void poll();const timer=window.setInterval(poll,1200);return()=>{cancelled=true;window.clearInterval(timer);};
  },[api,phase,job?.id,job?.status]);
  const b64=async(f:File)=>{const data=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsDataURL(f);});return data.split(",",2)[1]||"";};
  const submit=async()=>{if(!prompt.trim())return;const instance=instances.find(i=>i.id===selected);if(!instance?.ready){setLoadingError("Önce hazır bir model seç.");setControlsOpen(true);return;}
    setLoadingError("");try{const image_base64=file?await b64(file):"";const payload={prompt,negative,model:instance.family,image_model:mode==="image"?instance.family:"auto",model_instance:mode==="image"?instance.id:null,llm_model:undefined,width,height,max_side:Math.max(width,height),max_olcek:false,video_model_instance:mode==="video"?instance.id:null,video_profile:"fast",suresn:videoSeconds,fps:8,steps,cfg:1,sampler:"euler",seed:-1,is_tipi:mode==="video"?"video-uretim-hazirlik":file?"goruntu-duzenleme":"goruntu-uretim",image_base64,image_mime:file?.type||"image/jpeg",prompt_destek:false,prompt_ceviri:false};
      const response=await api.post<JsonRecord>("/api/kuyruk",payload);const id=String(response.is_id??response.id??"");if(!id)throw new Error("Sunucu iş numarası döndürmedi.");setJob({id,status:"queued",progress:0,message:"İstek kuyruğa alındı"});
    }catch(e){setLoadingError(e instanceof Error?e.message:"İstek gönderilemedi.");}};
  const cancel=async()=>{if(!job?.id)return;try{await api.post("/api/is/iptal",{is_id:job.id});setJob({...job,status:"stopping",message:"Durdurma isteği gönderildi"});}catch(e){setLoadingError(e instanceof Error?e.message:"İş durdurulamadı.");}};
  if(phase!=="ready")return <AuthGate phase={phase} refresh={()=>void refresh()} api={api} vaultMissing={vaultMissing} vaultRecover={vaultRecover}/>;
  return <div className="production-app"><ShellNav legacy={legacy}/><main id="production" className="production-main">
    <div className="workspace-heading"><div><p className="eyebrow">YENİ ÇALIŞMA</p><h1>Üretim alanı</h1></div><span className="connection-state"><i/> Bağlı</span></div>
    <div className="workspace-grid"><div className="workspace-center"><MediaCanvas mode={mode} job={job}/>{loadingError&&<p className="workspace-error" role="alert">{loadingError}</p>}<Composer mode={mode} setMode={setMode} prompt={prompt} setPrompt={setPrompt} busy={Boolean(job&&["queued","running","stopping"].includes(job.status))} onSubmit={()=>void submit()} onCancel={()=>void cancel()} file={file} setFile={setFile} onControls={()=>setControlsOpen(true)}/></div>
      <Controls mode={mode} setMode={setMode} instances={instances} selected={selected} setSelected={setSelected} width={width} setWidth={setWidth} height={height} setHeight={setHeight} steps={steps} setSteps={setSteps} videoSeconds={videoSeconds} setVideoSeconds={setVideoSeconds} negative={negative} setNegative={setNegative} mobile={controlsOpen} close={()=>setControlsOpen(false)}/></div>
    <footer className="workspace-footer"><span>Yeni arayüz aşamalı olarak geliştiriliyor.</span><a href={legacy}>Diğer özellikler için eski arayüzü aç ↗</a></footer>
  </main></div>;
}
