import { useCallback, useEffect, useMemo, useState } from "react";
import { LogOut, RefreshCw } from "lucide-react";

/** Deliberately narrow: the installer talks only through JSON GET/POST operations. */
export interface InstallerApi {
  get<T = Record<string, unknown>>(path: string, signal?: AbortSignal): Promise<T>;
  post<T = Record<string, unknown>>(path: string, data: Record<string, unknown>): Promise<T>;
}
type Dict = Record<string, any>;
type Tab = "downloads" | "settings" | "backups" | "system";
const actionLabels: Record<string, string> = { idle: "Kurulu değil", queued: "Sırada", running: "Hazırlanıyor", paused: "Duraklatıldı", ready: "Hazır", installed: "Dosyalar hazır", error: "Hata", cancelled: "İptal edildi" };
const jsonHeaders = { "Content-Type": "application/json" };
const LORA_MODEL_GROUPS: Array<[string, string]> = [["z_image", "zimage"], ["flux2", "flux"], ["flux3", "flux3"], ["krea2", "krea2"], ["illustrious", "illustrious"]];
const LORA_MODEL_NAMES: Record<string, string> = { z_image: "Z-Image", flux2: "Flux 2 Klein", flux3: "Flux 3 Klein", krea2: "Krea 2", illustrious: "Illustrious" };
const BACKUP_STAGE_LABELS: Record<string, string> = { uploaded: "Yedek bulutta saklandı", parola_dogrulaniyor: "Yedek parolası ve arşiv doğrulanıyor", arsiv_aciliyor: "Şifreli arşiv açılıyor", kayitlar_dogrulaniyor: "Kullanıcı kayıtları ve model ayarları doğrulanıyor", sisteme_yerlestiriliyor: "Yedek sisteme yerleştiriliyor", tamamlandi: "Yedek geri yüklendi", error: "Parola yanlış veya yedek okunamadı" };
function backupStageLabel(stage: unknown): string { const value = String(stage || "Yedek geri yükleniyor"); return BACKUP_STAGE_LABELS[value] || value; }

async function rawJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...init });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.hata || `HTTP ${response.status}`) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return body as T;
}
function uploadEncryptedBackup(file: File, onProgress: (percent: number) => void): Promise<Dict> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/backup-upload"); xhr.withCredentials = true;
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.min(100, Math.round(100 * event.loaded / event.total))); };
    xhr.onerror = () => reject(new Error("Şifreli yedek yüklenemedi."));
    xhr.onload = () => { let body: Dict = {}; try { body = JSON.parse(xhr.responseText || "{}"); } catch {} if (xhr.status < 200 || xhr.status >= 300) reject(new Error(body.hata || `HTTP ${xhr.status}`)); else resolve(body); };
    xhr.send(file);
  });
}
function record(v: unknown): Dict { return v && typeof v === "object" ? v as Dict : {}; }
function humanBytes(value: number): string { return value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GiB` : `${(value / 1024 ** 2).toFixed(1)} MiB`; }
function makeToken(): string { return (globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).replace(/[^a-f0-9]/gi, "").toLowerCase().padEnd(32, "0").slice(0, 32); }
function backupFileStorageKey(file: File): string { return `webui-react-backup-upload:${file.name}:${file.size}:${file.lastModified}`; }
function backupRestoreStorageKey(id: string): string { return `webui-react-r2-restore:${id}`; }
function localValue(key: string): string { try { return window.localStorage.getItem(key) || ""; } catch { return ""; } }
function setLocalValue(key: string, value: string | null): void { try { value ? window.localStorage.setItem(key, value) : window.localStorage.removeItem(key); } catch { /* Storage can be disabled; the active page still works. */ } }
const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

export interface InstallerWorkspaceProps { api: InstallerApi; }

export function InstallerWorkspace({ api }: InstallerWorkspaceProps) {
  const [tab, setTab] = useState<Tab>("downloads"), [pane, setPane] = useState("startup");
  const [ready, setReady] = useState<Dict | null>(null), [user, setUser] = useState<Dict | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [loginName, setLoginName] = useState(""), [loginPass, setLoginPass] = useState("");
  const [status, setStatus] = useState<Dict>({}), [settings, setSettings] = useState<Dict>({}), [logs, setLogs] = useState("");
  const [logSource, setLogSource] = useState("setup"), [backupList, setBackupList] = useState<Dict>({}), [backupStatus, setBackupStatus] = useState("");
  const [restorePassword, setRestorePassword] = useState(""), [backupPassword, setBackupPassword] = useState(""), [restoreProgress, setRestoreProgress] = useState(0), [defaultOrder, setDefaultOrder] = useState<string[]>([]);
  const [modelFamily, setModelFamily] = useState(""), [optionalFiles, setOptionalFiles] = useState<Record<string, boolean>>({}), [loraModel, setLoraModel] = useState("z_image"), [modelName, setModelName] = useState("");
  const groups: Dict[] = status.gruplar || [];
  const availableLoraModels = useMemo(() => LORA_MODEL_GROUPS.filter(([, groupId]) => groups.some(group => group.id === groupId)), [groups]);
  const availableLoras: Dict[] = (settings.ayarlar?.loras || []).filter((lora: Dict) => availableLoraModels.some(([id]) => id === lora.model));
  const groupMap = useMemo(() => new Map(groups.map(g => [g.id, g])), [groups]);
  const loadStatus = useCallback(async () => setStatus(await api.get("/api/pod-kurulum")), [api]);
  const applySettings = (data: Dict) => {
    setSettings(data);
    const ids = (data.gruplar || []).map((group: Dict) => String(group.id));
    const selected = data.ayarlar?.default_download_order || [];
    setDefaultOrder([...selected.filter((id: string) => ids.includes(id)), ...ids.filter((id: string) => !selected.includes(id))]);
  };
  const loadSettings = useCallback(async () => {
    const data = await api.get<Dict>("/api/system-settings");
    setSettings(data);
    const ids = (data.gruplar || []).map((group: Dict) => String(group.id));
    const selected = data.ayarlar?.default_download_order || [];
    setDefaultOrder([...selected.filter((id: string) => ids.includes(id)), ...ids.filter((id: string) => !selected.includes(id))]);
  }, [api]);
  const refresh = useCallback(async () => {
    setError("");
    try {
      const state = await api.get<Dict>("/api/setup-state"); setReady(state);
      if (!state.hazir) { setUser(null); return; }
      if (state.parolasiz) { setUser({ kullanici: "Kurulum paneli", rol: "admin", parolasiz: true }); await loadStatus(); return; }
      try { const me = await api.get<Dict>("/api/ben"); setUser(me.rol === "admin" ? me : null); }
      catch { setUser(null); }
      if (state.parolasiz || user?.rol === "admin") await loadStatus();
    } catch (e) { setError(e instanceof Error ? e.message : "Sunucuya ulaşılamadı."); }
  }, [api, loadStatus, user?.rol]);
  useEffect(() => { void refresh(); const id = window.setInterval(() => { void refresh(); }, 4000); return () => window.clearInterval(id); }, [refresh]);
  useEffect(() => { if (user?.rol === "admin" && tab === "settings") void loadSettings(); }, [user, tab, loadSettings]);
  useEffect(() => { if (user?.rol === "admin" && tab === "backups") void api.get<Dict>("/api/r2-backups").then(setBackupList).catch(e => setBackupStatus(String(e))); }, [api, user, tab]);
  useEffect(() => {
    if (availableLoraModels.some(([id]) => id === loraModel)) return;
    setLoraModel(availableLoraModels[0]?.[0] || "");
  }, [availableLoraModels, loraModel]);

  const action = async (islem: string, data: Dict = {}) => { setBusy(true); setError(""); try { setStatus(await api.post("/api/pod-kurulum", { ...data, islem })); } catch (e) { setError(e instanceof Error ? e.message : "İşlem başarısız."); } finally { setBusy(false); } };
  const postSettings = async (payload: Dict, message = "Kaydedildi.") => { setBusy(true); setError(""); try { applySettings(await api.post<Dict>("/api/system-settings", payload)); setBackupStatus(message); } catch (e) { setError(e instanceof Error ? e.message : "Ayar kaydedilemedi."); } finally { setBusy(false); } };
  const login = async (event: React.FormEvent) => { event.preventDefault(); setBusy(true); setError(""); try { const result = await api.post<Dict>("/api/giris", { kullanici: loginName.trim(), sifre: loginPass }); setLoginPass(""); if (result.rol !== "admin") throw new Error("Bu panel yönetici girişi gerektiriyor."); setUser(result); await loadStatus(); } catch (e) { setError(e instanceof Error ? e.message : "Giriş başarısız."); } finally { setBusy(false); } };
  const logout = async () => { try { await api.post("/api/cikis", {}); } finally { setUser(null); } };

  if (!ready) return <main className="installer-shell"><p role="status">Kurulum servisi kontrol ediliyor…</p>{error && <p role="alert">{error}</p>}</main>;
  if (!ready.hazir) return <main className="installer-shell"><h1>Kurulum hazırlanıyor</h1><p>Bağımsız model indirme servisi açılıyor. Birazdan yeniden denenecek.</p>{error && <p role="alert">{error}</p>}</main>;
  if (!user || user.rol !== "admin") return <main className="installer-shell installer-auth"><form onSubmit={login}><span aria-hidden="true">◇</span><p className="installer-eyebrow">YÖNETİCİ PANELİ</p><h1>Kurulum ekranına giriş</h1><p>Bu panelin ayarları ve model indirmeleri için yönetici girişi gerekir.</p><label>Kullanıcı adı<input autoComplete="username" value={loginName} onChange={e => setLoginName(e.target.value)} required /></label><label>Parola<input type="password" autoComplete="current-password" value={loginPass} onChange={e => setLoginPass(e.target.value)} required /></label>{error && <p role="alert">{error}</p>}<button disabled={busy}>{busy ? "Giriş yapılıyor…" : "Giriş yap"}</button></form></main>;

  const progress = record(status.ilerleme), active = status.aktif as string | undefined, queued: string[] = status.sirada || [];
  return <main className="installer-shell">
    <header className="installer-header"><div><p className="installer-eyebrow">MOODY STUDIO · RUNPOD</p><h1>Kurulum ve sistem yönetimi</h1><p>{ready.storage_persistent ? "Kalıcı volume bağlı · genel ayarlar korunur" : "Volume yok · Pod diskindeki dosyalar geçicidir"}</p></div><div className="installer-header-actions"><span>{user.parolasiz ? "Parolasız yönetici" : user.kullanici}</span><button type="button" onClick={() => void refresh()} aria-label="Yenile"><RefreshCw size={17}/></button>{!user.parolasiz && <button type="button" onClick={() => void logout()}><LogOut size={16}/> Çıkış</button>}</div></header>
    {error && <p className="installer-alert" role="alert">{error}</p>}
    <nav className="installer-tabs" aria-label="Kurulum bölümleri">{([ ["downloads", "⇩", "İndirmeler"], ["settings", "⚙", "Genel ayarlar"], ["backups", "▣", "Yedekler"], ["system", "⌘", "Sistem"] ] as const).map(([id, icon, label]) => <button key={id} type="button" aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}><span aria-hidden="true">{icon}</span>{label}</button>)}</nav>
    {tab === "downloads" && <section className="installer-section" data-state={busy?"submitting":"idle"} aria-busy={busy}><div className="installer-section-heading"><div><h2>Model indirmeleri</h2><p>Bağımlılıklar otomatik sırayla hazırlanır. Sıra değişikliği sunucu tarafından doğrulanır.</p></div><button type="button" onClick={() => void loadStatus()}><RefreshCw size={16}/> Durumu yenile</button></div>
      {active && <article className="installer-progress"><div><b>{groupMap.get(active)?.ad || active} · {progress.stage || "Hazırlanıyor"}</b><span>{Number.isFinite(progress.rate_mib_s) ? `${Number(progress.rate_mib_s).toFixed(1)} MiB/sn` : ""}</span></div><p>{progress.file || ""}</p><progress max={Number(progress.total) || 100} value={Number(progress.total) ? Number(progress.downloaded) : undefined}/><small>{Number(progress.total) ? `${humanBytes(Number(progress.downloaded) || 0)} / ${humanBytes(Number(progress.total))}` : "İşlem sürüyor…"}</small></article>}
      <div className="installer-card-grid">{groups.map(g => { const state = g.durum || "idle", index = queued.indexOf(g.id); return <article className="installer-card" key={g.id}><div className="installer-card-title"><h3>{g.ad}</h3><span data-state={state}>{actionLabels[state] || state}</span></div><p>{g.ayrinti || ""}</p>{g.bagimliliklar?.length > 0 && <small>Önce hazırlanır: {g.bagimliliklar.map((id: string) => groupMap.get(id)?.ad || id).join(", ")}</small>}<div className="installer-actions">
        {index >= 0 ? <><button disabled={busy || index === 0} onClick={() => void action("sira", { grup: g.id, yon: "yukari" })} aria-label={`${g.ad} yukarı al`}>↑</button><button disabled={busy || index === queued.length - 1} onClick={() => void action("sira", { grup: g.id, yon: "asagi" })} aria-label={`${g.ad} aşağı al`}>↓</button><button disabled={busy} onClick={() => void action("duraklat", { grup: g.id })}>Duraklat</button><button disabled={busy} onClick={() => void action("iptal", { grup: g.id })}>Sıradan çıkar</button></>
          : active === g.id && state === "running" ? <><button disabled={busy} onClick={() => void action("duraklat", { grup: g.id })}>Duraklat</button><button disabled={busy} onClick={() => void action("iptal", { grup: g.id })}>İndirmeyi iptal et</button></>
          : state === "paused" ? <><button disabled={busy} onClick={() => void action("devam", { grup: g.id })}>Devam et</button><button disabled={busy} onClick={() => void action("iptal", { grup: g.id })}>İptal et</button></>
          : state === "ready" || state === "installed" ? <button disabled={busy} onClick={() => void action("kaldir", { grup: g.id })}>Modeli kaldır</button>
          : <><button disabled={busy} onClick={() => void action("hazirla", { grup: g.id })}>{state === "error" || state === "cancelled" ? "Yeniden dene" : "Kuruluma ekle"}</button>{(state === "error" || state === "cancelled") && <button disabled={busy} onClick={() => void action("kaldir", { grup: g.id })}>Kısmi indirmeyi sil</button>}</>}
      </div></article>; })}</div>
      <section className="installer-subsection"><h3>İndirme sırası</h3><p>Bu oturumdaki sırayı değiştirmek için yukarı/aşağı düğmelerini kullan. Kalıcı varsayılan sıra Genel ayarlar bölümündedir.</p><ol>{queued.map(id => <li key={id}>{groupMap.get(id)?.ad || id}</li>)}</ol></section>
    </section>}
    {tab === "settings" && <section className="installer-section" data-state={busy?"submitting":"idle"} aria-busy={busy}><div className="installer-section-heading"><div><h2>Evrensel ayarlar</h2><p>Bu tercihler kullanıcıya özel değildir; bağlı volume varsa orada korunur.</p></div></div>{busy&&<p role="status">Kaydediliyor…</p>}<nav className="installer-subtabs" aria-label="Ayar kategorileri">{[["startup","Başlangıç"],["models","Modeller"],["links","Bağlantılar"],["loras","LoRA"],["system","Genel"]].map(([id,label])=><button type="button" key={id} aria-pressed={pane===id} onClick={()=>setPane(id)}>{label}</button>)}</nav>
      {pane === "startup" && <div className="installer-panel"><label className="installer-check"><input type="checkbox" checked={settings.ayarlar?.auto_download === true} onChange={e => setSettings((s: Dict) => ({...s, ayarlar:{...s.ayarlar, auto_download:e.target.checked}}))}/> Pod açıldığında varsayılan model indirme sırasını otomatik başlat</label><h3>Varsayılan indirme sırası</h3><p>Seçili grupların indirme sırasını oklarla değiştir. Bu, bu oturumdaki kuyruk sırasından ayrıdır.</p>{defaultOrder.map((id,index) => { const group=(settings.gruplar||[]).find((item:Dict)=>item.id===id); if(!group)return null; const selected=(settings.ayarlar?.default_download_order||[]).includes(id); return <div className="installer-row" key={id}><label className="installer-check"><input type="checkbox" checked={selected} onChange={e=>{const previous=settings.ayarlar?.default_download_order||[];const next=defaultOrder.filter(item=>e.target.checked?item===id||previous.includes(item):item!==id&&previous.includes(item));setSettings((s:Dict)=>({...s,ayarlar:{...s.ayarlar,default_download_order:next}}));}}/>{group.label||group.ad}</label><div className="installer-actions"><button type="button" disabled={index===0} aria-label={`${group.label||group.ad} yukarı taşı`} onClick={()=>setDefaultOrder(order=>{const next=[...order];[next[index-1],next[index]]=[next[index],next[index-1]];return next;})}>↑</button><button type="button" disabled={index===defaultOrder.length-1} aria-label={`${group.label||group.ad} aşağı taşı`} onClick={()=>setDefaultOrder(order=>{const next=[...order];[next[index+1],next[index]]=[next[index],next[index+1]];return next;})}>↓</button></div></div>; })}<button onClick={() => void postSettings({islem:"kaydet", auto_download:settings.ayarlar?.auto_download, default_download_order:defaultOrder.filter(id=>(settings.ayarlar?.default_download_order||[]).includes(id))})}>Varsayılanları kaydet</button></div>}
      {pane === "models" && <ModelManagementPanel settings={settings} busy={busy} modelFamily={modelFamily} setModelFamily={value => { setModelFamily(value); setOptionalFiles({}); }} optionalFiles={optionalFiles} setOptionalFiles={setOptionalFiles} modelName={modelName} setModelName={setModelName} postSettings={postSettings}/>}
      {pane === "links" && <div className="installer-panel"><h3>Model dosyası bağlantıları</h3>{(settings.gruplar||[]).flatMap((g:Dict)=>(g.files||[]).filter((f:Dict)=>f.link).map((f:Dict)=><LinkRow key={`${g.id}-${f.name}`} group={g} file={f} save={link=>void postSettings({islem:"baglanti",grup:g.id,dosya:f.name,baglanti:link},"Bağlantı kaydedildi.")} reset={()=>void postSettings({islem:"varsayilan",grup:g.id,dosya:f.name},"Varsayılan bağlantı yüklendi.")}/>))}</div>}
      {pane === "loras" && <div className="installer-panel"><h3>LoRA kayıtları</h3>{availableLoras.map((l:Dict)=><LoraRow key={l.id} lora={l} status={(settings.lora_durum||[]).find((s:Dict)=>s.id===l.id)} save={payload=>void postSettings({islem:"lora_ayar",id:l.id,...payload})} remove={()=>{if(window.confirm("LoRA kaydı silinsin mi?"))void postSettings({islem:"lora_sil",id:l.id});}}/>)}{!availableLoras.length&&<p>Bu podda LoRA destekleyen görsel model ailesi yok.</p>}<h3>LoRA ekle</h3><form className="installer-form" onSubmit={e=>{e.preventDefault();const form=e.currentTarget;const data=new FormData(form);void postSettings({islem:"lora_ekle",name:data.get("name"),model:data.get("model"),link:data.get("link")},"LoRA eklendi.");form.reset();}}><label>Ad<input name="name" required/></label><label>Görsel modeli<select name="model" value={loraModel} onChange={e=>setLoraModel(e.target.value)} disabled={!availableLoraModels.length} required>{availableLoraModels.map(([id])=><option key={id} value={id}>{LORA_MODEL_NAMES[id]}</option>)}</select></label>{!availableLoraModels.length&&<small>Önce desteklenen bir görsel modeli kurucuya ekle.</small>}<label>Hugging Face veya Civitai indirme bağlantısı<input name="link" type="url" required/></label><button disabled={busy||!availableLoraModels.length}>LoRA kaydet</button></form></div>}
      {pane === "system" && <div className="installer-panel"><h3>Sistem genel ayarları</h3><p>Model kayıtları, indirme sırası ve LoRA tanımları sistem genelinde geçerlidir.</p><button onClick={()=>void loadSettings()}>Ayarları yenile</button></div>}{backupStatus && <p role="status">{backupStatus}</p>}
    </section>}
    {tab === "backups" && <BackupPanel backupList={backupList} setBackupList={setBackupList} status={backupStatus} setStatus={setBackupStatus} restorePassword={restorePassword} setRestorePassword={setRestorePassword} password={backupPassword} setPassword={setBackupPassword} progress={restoreProgress} setProgress={setRestoreProgress}/ >}
    {tab === "system" && <SystemTools api={api} ready={ready} busy={busy} logSource={logSource} setLogSource={setLogSource} logs={logs} setLogs={setLogs}/>}
  </main>;
}

function SystemTools({api,ready,busy,logSource,setLogSource,logs,setLogs}:{api:InstallerApi;ready:Dict;busy:boolean;logSource:string;setLogSource:(value:string)=>void;logs:string;setLogs:(value:string)=>void}) {
  const [working,setWorking]=useState(false),[message,setMessage]=useState(""),[channel,setChannel]=useState("");
  const pending = working || busy;
  const loadLogs = useCallback(async()=>{
    try { const data=await api.get<Dict>(`/api/log?kaynak=${encodeURIComponent(logSource)}&satir=200`); setLogs((data.satirlar||[]).join("\n")); }
    catch(error) { setMessage(error instanceof Error?error.message:"Loglar alınamadı."); }
  },[api,logSource,setLogs]);
  useEffect(()=>{
    let alive=true;
    void api.get<Dict>("/api/app-update").then(state=>{if(alive)setChannel(String(state.kanal||""));}).catch(()=>{if(alive)setChannel("");});
    return ()=>{alive=false;};
  },[api]);
  useEffect(()=>{
    void loadLogs();
    const id=window.setInterval(()=>void loadLogs(),8000);
    return ()=>window.clearInterval(id);
  },[loadLogs]);
  const update = async () => {
    setWorking(true); setMessage("Güncelleme hazırlanıyor…");
    try {
      await api.post("/api/app-update",{});
      let disconnected=false;
      for(let i=0;i<180;i++) {
        await wait(2000);
        let state:Dict;
        try { state=await api.get<Dict>("/api/app-update"); }
        catch(error) {
          const code=(error as Error & {status?:number}).status;
          if(code===401&&disconnected){window.location.reload();return;}
          disconnected=true; setMessage("Uygulama yeniden açılıyor; bağlantı bekleniyor…"); continue;
        }
        disconnected=false;
        if(state.kanal)setChannel(String(state.kanal));
        if(state.status==="error") throw new Error(state.message||"Güncelleme başarısız.");
        if(state.status==="ready"){setMessage("Güncelleme tamamlandı; sayfa yenileniyor…");window.location.reload();return;}
        setMessage(state.status==="restarting"?"Kasalar mühürlendi; uygulama yeniden açılıyor…":`Güncelleme sürüyor${state.status?` · ${state.status}`:"…"}`);
      }
      setMessage("Uygulamanın açılması bekleniyor; sayfayı biraz sonra yenileyin.");
    } catch(error) { setMessage(error instanceof Error?error.message:"Güncelleme tamamlanamadı."); }
    finally { setWorking(false); }
  };
  const restart = async () => {
    if(!window.confirm("Uygulama servisleri yeniden başlatılsın mı? İndirilen model dosyaları korunur."))return;
    setWorking(true); setMessage("Güvenli kapanış ve yeniden başlatma bekleniyor…");
    try {
      await api.post("/api/app-restart",{});
      for(let i=0;i<120;i++) {
        await wait(2000);
        try { const state=await api.get<Dict>("/api/setup-state"); if(state.hazir){setMessage("Uygulama yeniden başladı; indirilen modeller korundu.");return;} }
        catch { /* The setup port can be unavailable during process restart. */ }
      }
      throw new Error("Uygulama 4 dakika içinde yeniden yanıt vermedi.");
    } catch(error) { setMessage(error instanceof Error?error.message:"Yeniden başlatma tamamlanamadı."); }
    finally { setWorking(false); }
  };
  const clearStorage = async () => {
    if(!window.confirm("İndirilen modeller ve önbellekler silinsin mi? Kullanıcı kayıtları korunur."))return;
    setWorking(true); setMessage("Depolama temizleniyor…");
    try { const result=await api.post<Dict>("/api/storage-clean",{onay:true}); setMessage(`${result.removed||0} konum temizlendi · ${humanBytes(Number(result.freed_bytes)||0)} boşaltıldı.`); }
    catch(error) { setMessage(error instanceof Error?error.message:"Depolama temizlenemedi."); }
    finally { setWorking(false); }
  };
  return <section className="installer-section" data-state={pending?"submitting":"idle"} aria-busy={pending}><h2>Sistem araçları</h2><p>Bakım işlemleri model dosyalarını korur; depolama temizliği indirilen modelleri ve önbelleği kaldırır.</p>
    <div className="installer-panel"><label>Log kaynağı<select value={logSource} onChange={event=>setLogSource(event.target.value)}>{[["setup","Kurucu"],["comfy","ComfyUI"],["webui","WebUI"],["llama","LLM"]].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label><button disabled={pending} onClick={()=>void loadLogs()}>Logları getir</button><pre className="installer-log">{logs||"Loglar alınırken bekleyin…"}</pre></div>
    <div className="installer-panel"><h3>Uygulama</h3><p>Kanal: {channel||ready.kanal||"yükleniyor…"}</p><button disabled={pending} onClick={()=>void update()}>Uygulamayı güncelle</button><button disabled={pending} onClick={()=>void restart()}>Servisleri yeniden başlat</button></div>
    <div className="installer-panel"><h3>Depolama temizliği</h3><p>İndirilen modelleri ve önbelleği siler. Kullanıcı hesapları, kasa ve genel ayarlar korunur.</p><button className="installer-danger" disabled={pending} onClick={()=>void clearStorage()}>Depolamayı temizle</button></div>
    {message&&<p role="status">{message}</p>}
  </section>;
}

function ModelManagementPanel({settings,busy,modelFamily,setModelFamily,optionalFiles,setOptionalFiles,modelName,setModelName,postSettings}:{settings:Dict;busy:boolean;modelFamily:string;setModelFamily:(value:string)=>void;optionalFiles:Record<string,boolean>;setOptionalFiles:React.Dispatch<React.SetStateAction<Record<string,boolean>>>;modelName:string;setModelName:(value:string)=>void;postSettings:(payload:Dict,message?:string)=>Promise<void>}) {
  const instances:Dict[] = settings.model_instances || [];
  const families:Dict[] = settings.model_families || [];
  const base = instances.find(item => item.id === modelFamily);
  const files:Dict[] = base?.files || [];
  const submit = (event:React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!base) return;
    const form = event.currentTarget, links:Dict = {}, exclude_files:string[] = [];
    for (const file of files) {
      if (file.optional && !optionalFiles[file.name]) { exclude_files.push(file.name); continue; }
      const input = form.elements.namedItem(`file-${file.name}`) as HTMLInputElement | null;
      const value = input?.value.trim() || "";
      if (file.optional && !value) { input?.focus(); return; }
      if (value) links[file.name] = value;
    }
    if (!links[base.primary_file]) return;
    void postSettings({islem:"model_ekle",family:modelFamily,name:modelName,links,exclude_files},"Model eklendi.");
  };
  return <div className="installer-panel">
    <h3>Kayıtlı model örnekleri</h3>
    {instances.map(model => <ModelRow key={model.id} model={model} onSave={name => void postSettings({islem:"model_ad",id:model.id,name})} onDelete={() => { if (window.confirm("Bu model kaydını ve yalnızca ona ait dosyaları silmek istiyor musun?")) void postSettings({islem:"model_sil",id:model.id}); }}/ >)}
    <h3>Model örneği ekle</h3>
    <form className="installer-form" onSubmit={submit}>
      <label>Model ailesi<select value={modelFamily} onChange={event=>setModelFamily(event.target.value)} required><option value="">Seç…</option>{families.map(family=><option key={family.id} value={family.id}>{family.name}</option>)}</select></label>
      {files.map(file=><div className="installer-model-file" key={file.name}>
        {file.optional && <label className="installer-check"><input type="checkbox" checked={!!optionalFiles[file.name]} onChange={event=>setOptionalFiles(current=>({...current,[file.name]:event.target.checked}))}/>{file.name} dosyasını ekle (isteğe bağlı)</label>}
        <label>{file.optional?`${file.name} bağlantısı`:file.name}<input name={`file-${file.name}`} type="url" placeholder={file.optional?"Eklenecekse indirme bağlantısını gir":file.name===base?.primary_file?"Ana dosya için yeni bağlantıyı gir":"Boş bırakılırsa varsayılan bağlantı kullanılır"} disabled={!!file.optional&&!optionalFiles[file.name]} required={file.name===base?.primary_file||!!file.optional&&!!optionalFiles[file.name]}/></label>
      </div>)}
      <label>Görünen ad<input value={modelName} onChange={event=>setModelName(event.target.value)} required maxLength={80}/></label>
      <button disabled={busy || !modelFamily || !base}>Model kaydı oluştur</button>
    </form>
  </div>;
}

function ModelRow({model,onSave,onDelete}:{model:Dict;onSave:(name:string)=>void;onDelete:()=>void}) { const [name,setName]=useState(model.name||""); return <article className="installer-row"><span>{model.family} · {model.ready?"Dosyalar hazır":"İndirilmedi"}</span><input aria-label={`Model adı: ${model.name}`} value={name} onChange={e=>setName(e.target.value)}/><button onClick={()=>onSave(name)}>Adı kaydet</button>{!model.builtin&&<button onClick={onDelete}>Model kaydını sil</button>}</article>; }
function LinkRow({group,file,save,reset}:{group:Dict;file:Dict;save:(v:string)=>void;reset:()=>void}) { const [value,setValue]=useState(file.link||""); useEffect(()=>setValue(file.link||""),[file.link]); return <div className="installer-link-row"><label>{group.label||group.ad} · {file.name}<input type="url" value={value} onChange={e=>setValue(e.target.value)}/></label><button onClick={()=>save(value)}>Kaydet</button><button onClick={reset}>Varsayılan</button></div>; }
function LoraRow({lora,status,save,remove}:{lora:Dict;status?:Dict;save:(v:Dict)=>void;remove:()=>void}) { const [enabled,setEnabled]=useState(!!lora.enabled),[selected,setSelected]=useState(!!lora.default_selected),[auto,setAuto]=useState(!!lora.auto_download),[weight,setWeight]=useState(Number(lora.weight||1)); return <article className="installer-lora"><b>{lora.name}</b><small>{status?.hazir?"Dosya hazır":status?.durum==="running"?"İndiriliyor":"İndirilmemiş"}</small><div><label><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/> Kullanılabilir</label><label><input type="checkbox" checked={selected} onChange={e=>setSelected(e.target.checked)}/> Varsayılan seç</label><label><input type="checkbox" checked={auto} onChange={e=>setAuto(e.target.checked)}/> Otomatik indir</label><label>Ağırlık<input type="number" min="0" max="2" step="0.05" value={weight} onChange={e=>setWeight(Number(e.target.value))}/></label></div><button onClick={()=>save({enabled,default_selected:selected,auto_download:auto,weight})}>Ayarları kaydet</button>{!lora.built_in&&<button onClick={remove}>LoRA kaydını sil</button>}</article>; }

function BackupPanel({backupList,setBackupList,status,setStatus,password,setPassword,restorePassword,setRestorePassword,progress,setProgress}:{backupList:Dict;setBackupList:(v:Dict)=>void;status:string;setStatus:(v:string)=>void;password:string;setPassword:(v:string)=>void;restorePassword:string;setRestorePassword:(v:string)=>void;progress:number;setProgress:(v:number)=>void}) {
  const [excludeMedia,setExcludeMedia]=useState(false),[name,setName]=useState("webui-system"),[file,setFile]=useState<File|null>(null),[fileToken,setFileToken]=useState(""),[selected,setSelected]=useState<Dict|null>(null),[working,setWorking]=useState(false);
  const poll=async (id:string)=>{while(true){let s:Dict;try{s=await rawJson<Dict>(`/api/backup-progress?token=${encodeURIComponent(id)}`);}catch(error){const code=(error as Error&{status?:number}).status;if(code===401||code===403||code===404)throw error;setStatus("Bağlantı geçici olarak kesildi; yedek işlemi sürüyor ve durum yeniden denenecek…");await wait(2500);continue;}setStatus(`${backupStageLabel(s.stage||s.status)}${s.files?` · ${s.files} dosya`:""}${s.bytes?` · ${humanBytes(Number(s.bytes))}`:""}`);if(s.status==="complete"){setProgress(100);return s;}if(s.status==="error")throw new Error(s.error||backupStageLabel(s.stage)||"İşlem başarısız.");setProgress(s.total?Math.round(100*Number(s.bytes||0)/Number(s.total)):0);await wait(900);}};
  const getFileToken=async()=>{if(!file)throw new Error("Şifreli yedek dosyası seç.");const key=backupFileStorageKey(file);let id=fileToken||localValue(key);if(!id){const body=await uploadEncryptedBackup(file,p=>{setProgress(p);setStatus(`Şifreli dosya bu Pod’a yükleniyor · %${p}`);});id=String(body.token||"");if(!/^[a-f0-9]{32}$/.test(id))throw new Error("Sunucu yükleme kimliği döndürmedi.");setFileToken(id);setLocalValue(key,id);}return {id,key};};
  const importToken=async(id:string)=>{
    let existing:Dict|undefined;
    try{existing=await rawJson<Dict>(`/api/backup-progress?token=${encodeURIComponent(id)}`);}catch(error){const code=(error as Error&{status?:number}).status;if(code===401||code===403)throw error;}
    const importAlreadyComplete=(state?:Dict)=>state?.status==="complete"&&/geri yükleme tamamlandı/i.test(String(state.stage||""));
    if(importAlreadyComplete(existing))return existing?.result||{};
    if(existing?.status==="running"){
      const finished=await poll(id);
      if(!/geri yükleme tamamlandı/i.test(String(finished.stage||"")))throw new Error("Önceki yedek işlemi tamamlandı; durumunu kontrol edip tekrar deneyin.");
      return finished.result||{};
    }
    let done=false;
    const pending=rawJson<Dict>("/api/backup-import",{method:"POST",headers:{...jsonHeaders,"X-Backup-Password":restorePassword},body:JSON.stringify({token:id})}).then(value=>({value}),error=>({error})).then(result=>{done=true;return result;});
    while(!done){
      try{const state=await rawJson<Dict>(`/api/backup-progress?token=${encodeURIComponent(id)}`);setStatus(`${backupStageLabel(state.stage)}${state.files?` · ${state.files} dosya`:""}${state.bytes?` · ${humanBytes(Number(state.bytes))}`:""}`);if(state.total)setProgress(Math.round(100*Number(state.bytes||0)/Number(state.total)));if(state.status==="error"&&existing?.status==="running")throw new Error(state.error||backupStageLabel(state.stage)||"Geri yükleme başarısız.");}
      catch(error){const code=(error as Error&{status?:number}).status;if(code===401||code===403||code===404)throw error;setStatus("Bağlantı geçici olarak kesildi; aynı yedek yüklemesi korunuyor ve durum yeniden denenecek…");}
      await wait(850);
    }
    const result=await pending;
    if("error" in result){if((result.error as Error & {status?:number})?.status===404)throw Object.assign(result.error as Error,{clearUpload:true});throw result.error;}
    return result.value||{};
  };
  const exportLocal=async()=>{if(password.length<8)throw new Error("Yeni yedek parolası en az 8 karakter olmalı.");const res=await fetch("/api/backup-export",{method:"POST",credentials:"same-origin",headers:{"X-Backup-Password":password,...(excludeMedia?{"X-Backup-Media":"exclude"}:{})}});if(!res.ok){const body=await res.json().catch(()=>({}));throw new Error(body.hata||"Yedek dışa aktarılamadı.");}const url=URL.createObjectURL(await res.blob()),a=document.createElement("a");a.href=url;a.download="webui-system.wuis.gpg";a.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);setStatus(excludeMedia?"Medyasız, şifreli yedek indirildi.":"Tam sistem yedeği şifrelenerek indirildi.");};
  const importLocal=async()=>{if(!restorePassword)throw new Error("Yedek parolası gerekli.");const {id,key}=await getFileToken();try{await importToken(id);setLocalValue(key,null);setFileToken("");setProgress(100);setStatus("Yedek geri yüklendi. Yedekteki hesapla yeniden giriş yap.");}catch(error){if((error as Error & {clearUpload?:boolean})?.clearUpload){setLocalValue(key,null);setFileToken("");}throw error;}};
  const refresh=async()=>{const data=await rawJson<Dict>("/api/r2-backups");setBackupList(data);setStatus(data.connected?`R2 bağlı · ${data.bucket}`:data.error||"R2 yapılandırılmamış.");};
  const createR2=async()=>{if(password.length<8)throw new Error("Yeni yedek parolası en az 8 karakter olmalı.");if(!window.confirm("Yedek hazırlanırken açık kasalar mühürlenir. Şifreli yedek oluşturulsun mu?"))return;const started=await rawJson<Dict>("/api/r2-backup-create",{method:"POST",headers:{...jsonHeaders,"X-Backup-Password":password},body:JSON.stringify({name,exclude_media:excludeMedia})});await poll(started.job_id);await refresh();};
  const saveR2Upload=async()=>{const {id,key}=await getFileToken();await rawJson("/api/r2-backup-save",{method:"POST",headers:jsonHeaders,body:JSON.stringify({token:id,name})});await poll(id);setLocalValue(key,null);setFileToken("");await refresh();setStatus("Şifreli yedek R2’ye kaydedildi.");};
  const restoreR2=async()=>{if(!selected||restorePassword.length<8)throw new Error("Yedek seç ve en az 8 karakterlik parolayı gir.");if(!window.confirm("Seçilen yedek mevcut sistem kayıtlarını değiştirecek. Devam edilsin mi?"))return;const key=backupRestoreStorageKey(String(selected.id));const id=localValue(key)||makeToken();setLocalValue(key,id);await rawJson("/api/r2-backup-prepare-restore",{method:"POST",headers:jsonHeaders,body:JSON.stringify({id:selected.id,token:id})});await poll(id);await importToken(id);setLocalValue(key,null);setProgress(100);setStatus("Yedek geri yüklendi. Yedekteki hesapla yeniden giriş yap.");};
  const run=async(fn:()=>Promise<void>)=>{if(working)return;setWorking(true);setProgress(0);try{setStatus("İşlem başlatılıyor…");await fn();}catch(e){setStatus(e instanceof Error?e.message:"İşlem tamamlanamadı.");}finally{setWorking(false);}};
  const chooseFile=(next:File|null)=>{setFile(next);setFileToken(next?localValue(backupFileStorageKey(next)):"");setProgress(0);};
  const backups:Dict[]=backupList.backups||[];
  return <section className="installer-section" data-state={working?"submitting":"idle"} aria-busy={working}><h2>Şifreli yedekler</h2><p>Yedekler AES şifreli arşiv olarak saklanır. Yanlış parola girilirse Pod’a yüklenen dosya korunur; aynı dosyayı yeniden seçerek tekrar deneyebilirsin.</p><div className="installer-backup-grid"><article className="installer-panel"><h3>Dışa aktar</h3><label>Yedek adı<input value={name} onChange={e=>setName(e.target.value)}/></label><label>Yedek parolası<input type="password" minLength={8} autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)}/></label><small>Yeni yedek parolası en az 8 karakter olmalı.</small><label className="installer-check"><input type="checkbox" checked={excludeMedia} onChange={e=>setExcludeMedia(e.target.checked)}/> Medya hariç (sohbet, promptlar, model ve genel ayarlar kalır)</label><button disabled={working||password.length<8} onClick={()=>void run(exportLocal)}>Şifreli dosyayı indir</button><button disabled={working||password.length<8||!backupList.connected} onClick={()=>void run(createR2)}>R2 deposuna kaydet</button></article><article className="installer-panel"><h3>İçe aktar veya R2’ye yükle</h3><label>Şifreli yedek dosyası<input type="file" accept=".gpg,.wuis" onChange={e=>chooseFile(e.target.files?.[0]||null)}/></label><button disabled={working||!file} onClick={()=>void run(saveR2Upload)}>Dosyayı R2’ye kaydet</button><label>Yedek parolası<input type="password" value={restorePassword} onChange={e=>setRestorePassword(e.target.value)}/></label><button disabled={working||!file} onClick={()=>void run(importLocal)}>Bu Pod’a geri yükle</button></article></div><div className="installer-panel"><div className="installer-section-heading"><h3>R2 yedekleri</h3><button disabled={working} onClick={()=>void run(refresh)}>Yenile</button></div>{backupList.connected===false&&<p>{backupList.error||"R2 bağlantısı kullanılamıyor."}</p>}{!backups.length&&backupList.connected&&<p>Henüz kayıtlı yedek yok.</p>}{backups.map(b=><article className="installer-row" key={b.id}><div><b>{b.name}</b><small>{b.modified} · {humanBytes(Number(b.size)||0)} · şifreli</small></div><a href={`/api/r2-backup-download?id=${encodeURIComponent(b.id)}`} download>İndir</a><button disabled={working} onClick={()=>setSelected(b)}>Geri yükle</button><button disabled={working} className="installer-danger" onClick={()=>{if(window.confirm("Bu R2 yedeği kalıcı olarak silinsin mi?"))void run(async()=>{await rawJson("/api/r2-backup-delete",{method:"POST",headers:jsonHeaders,body:JSON.stringify({id:b.id})});await refresh();});}}>Sil</button></article>)}</div>{selected&&<div className="installer-panel"><h3>{selected.name} · geri yükle</h3><label>Yedek parolası<input type="password" minLength={8} value={restorePassword} onChange={e=>setRestorePassword(e.target.value)}/></label><button disabled={working||restorePassword.length<8} onClick={()=>void run(restoreR2)}>İndir ve geri yükle</button><button disabled={working} onClick={()=>setSelected(null)}>Kapat</button></div>}{status&&<p role="status">{status}</p>}{progress>0&&<progress max={100} value={progress}/>}</section>;
}

export default InstallerWorkspace;
