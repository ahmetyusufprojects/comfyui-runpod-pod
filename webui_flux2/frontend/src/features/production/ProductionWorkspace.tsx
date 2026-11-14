import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Image, LoaderCircle, SlidersHorizontal, Square, Video, X } from "lucide-react";
import type { JsonRecord, ModelInstance, ProductionApi } from "../../lib/api/client";
import { GenerationOptions, type GenerationLoraOption, type GenerationModelOption, type GenerationOptionsValue } from "./GenerationOptions";

type Phase = "loading" | "setup" | "login" | "vault" | "ready" | "error";
type Mode = "image" | "video";
type Job = { id: string; status: string; progress?: number; message?: string; result?: JsonRecord };
type LoraStatus = GenerationLoraOption & { weight?: number; default_selected?: boolean };

interface Props {
  api: ProductionApi;
  embedded?: boolean;
  initialPrompt?: string;
  initialNegative?: string;
  referenceFile?: File | null;
  onOpenPromptStudio?: (prompt: string, editMode: boolean, negative: string) => void;
}

const DEFAULT_OPTIONS: GenerationOptionsValue = {
  mode: "image", imageModelId: "", videoModelId: "wan22", width: 1024, height: 1024,
  resolutionPreset: "1024x1024", aspectLocked: true, scaleToMax: true, referenceMaxSide: 1536,
  videoSeconds: 0.5, videoFps: 8, videoMaxSide: 320, videoProfile: "fast",
  steps: 9, seed: -1, cfg: 1, sampler: "dpmpp_2m_sde", fluxBbox: false,
  kreaEditStrength: 0.65, promptAssist: false, promptTranslate: false, promptEdit: false,
  promptRepeat: false, continuous: false, llmModelId: "", loraWeights: {},
  imageEnhance: false, imageEnhanceMethod: "skin", imageEnhanceScale: 2,
  videoEnhance: false, videoEnhanceMethod: "seedvr2_fp8", videoEnhanceScale: 2,
  videoFpsMultiplier: 2,
};

function isVideoModel(item: ModelInstance) {
  const family = item.family.toLowerCase();
  return family === "video" || family === "wan22" || item.id === "video" || item.id === "wan22" || family.includes("video") || family.includes("wan");
}

function savedInstance(value: unknown, models: ModelInstance[], predicate: (item: ModelInstance) => boolean) {
  const text = typeof value === "string" ? value : "";
  return models.find(item => predicate(item) && item.id === text)?.id
    ?? models.find(item => predicate(item) && item.family === text)?.id
    ?? models.find(item => predicate(item) && item.ready)?.id
    ?? models.find(predicate)?.id
    ?? "";
}

function AuthGate({ phase, refresh, api, vaultMissing, vaultRecover }: { phase: Phase; refresh: () => void; api: ProductionApi; vaultMissing: boolean; vaultRecover: boolean }) {
  const [user, setUser] = useState(""), [password, setPassword] = useState(""), [password2, setPassword2] = useState(""), [ackRisk, setAckRisk] = useState(false), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError("");
    try {
      if (phase === "setup") {
        await api.post("/api/kurulum", { kullanici: user.trim(), sifre: password });
        await api.post("/api/giris", { kullanici: user.trim(), sifre: password });
      } else if (phase === "login") await api.post("/api/giris", { kullanici: user.trim(), sifre: password });
      else if (phase === "vault") {
        if (vaultMissing && password !== password2) throw new Error("Kasa parolaları aynı olmalı.");
        if (vaultRecover) await api.post("/api/kasa/kurtar", { parola: password });
        else if (vaultMissing) await api.post("/api/kasa/olustur", { parola: password, parola2: password2, riski_anladim: ackRisk });
        else await api.post("/api/kasa/ac", { parola: password });
      }
      setPassword(""); setPassword2(""); await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "İşlem tamamlanamadı."); }
    finally { setBusy(false); }
  };
  const title = phase === "setup" ? "İlk hesabını oluştur" : phase === "login" ? "Tekrar hoş geldin" : phase === "vault" ? vaultRecover ? "Kasayı kurtar" : vaultMissing ? "Kasa oluştur" : "Kasayı aç" : "WebUI hazırlanıyor";
  return <main className="auth-page"><form className="auth-card" onSubmit={submit}>
    <span className="auth-mark">M</span><p className="eyebrow">MOODY STUDIO</p><h1>{title}</h1>
    <p className="auth-copy">{phase === "setup" ? "Hesabın ve kasa parolan bu cihazda saklanır." : phase === "login" ? "Devam etmek için hesabına giriş yap." : phase === "vault" ? vaultRecover ? "Tamamlanmamış kasa kurtarma işlemini sürdür." : vaultMissing ? "Yeni şifreli çalışma alanının parolasını belirle." : "Şifreli çalışma alanına erişmek için kasa parolanı gir." : "Sunucuya ulaşılamıyor."}</p>
    {(phase === "setup" || phase === "login") && <label>Kullanıcı adı<input autoComplete="username" value={user} onChange={event => setUser(event.target.value)} required/></label>}
    {phase !== "error" && phase !== "loading" && <label>{phase === "vault" ? vaultMissing ? "Yeni kasa parolası" : "Kasa parolası" : "Parola"}<input type="password" autoComplete={phase === "login" || (phase === "vault" && !vaultMissing) ? "current-password" : "new-password"} value={password} onChange={event => setPassword(event.target.value)} required/></label>}
    {phase === "vault" && vaultMissing && <><label>Yeni kasa parolasını doğrula<input type="password" autoComplete="new-password" value={password2} onChange={event => setPassword2(event.target.value)} required/></label><label className="ack-risk"><input type="checkbox" checked={ackRisk} onChange={event => setAckRisk(event.target.checked)}/> Parola gücü uyarısını anladım.</label></>}
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
    {src ? video ? <video className="media-stage__media" src={src} poster={poster} controls playsInline/> : <img className="media-stage__media" src={src} alt="Üretilen sonuç"/> :
      <div className="media-empty"><span className="media-empty__icon">{mode === "video" ? <Video/> : <Image/>}</span><p className="eyebrow">YARATIM ALANI</p><h1>{job?.status === "running" ? "Sonucun hazırlanıyor" : "Aklındakini hayata geçir"}</h1><p>{job?.message || "Bir açıklama yaz, modelini seç ve üretime başla."}</p><div className="canvas-hint"><span/> Sonuçların burada görünecek</div></div>}
    {job?.status === "error" && job.message && <p className="workspace-error" role="alert">{job.message}</p>}
    {job && ["running", "queued", "stopping"].includes(job.status) && <div className="job-progress" role="status"><div><LoaderCircle className="spin" size={16}/><span>{job.message || (job.status === "queued" ? "Sırada bekliyor" : "Üretiliyor")}</span><b>{Math.round((job.progress ?? 0) * 100)}%</b></div><div className="job-progress__track"><span style={{ width: `${Math.max(0, Math.min(100, (job.progress ?? 0.04) * 100))}%` }}/></div></div>}
  </section>;
}

function Composer({ mode, setMode, prompt, setPrompt, busy, onSubmit, onCancel, files, setFiles, onControls, onOpenPromptStudio }: {
  mode: Mode; setMode: (mode: Mode) => void; prompt: string; setPrompt: (prompt: string) => void;
  busy: boolean; onSubmit: () => void; onCancel: () => void; files: File[]; setFiles: (files: File[]) => void;
  onControls: () => void; onOpenPromptStudio: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const setAt = (index: number, file: File | null) => setFiles(index === 0 ? [file, files[1]].filter((item): item is File => Boolean(item)) : [files[0], file].filter((item): item is File => Boolean(item)));
  return <section className="composer" aria-label="Üretim isteği"><div className="composer__modes" role="tablist" aria-label="Üretim türü">
    <button type="button" role="tab" aria-selected={mode === "image"} className={mode === "image" ? "selected" : ""} onClick={() => setMode("image")}><Image size={15}/> Görsel</button>
    <button type="button" role="tab" aria-selected={mode === "video"} className={mode === "video" ? "selected" : ""} onClick={() => setMode("video")}><Video size={15}/> Video</button>
  </div><div className="composer__entry">
    <label className="composer__prompt-label" htmlFor="production-prompt">İsteğini yaz</label>
    <textarea id="production-prompt" value={prompt} onChange={event => setPrompt(event.target.value)} rows={3} placeholder={mode === "image" ? "Görselini tarif et…" : "Videoda neler olsun?"} disabled={busy}/>
    <div className="production-references">
      {[0, 1].map(index => <div className="production-reference" key={index}><input ref={index === 0 ? fileInputRef : undefined} type="file" accept="image/*" aria-label={index === 0 ? "Birinci referans fotoğrafı" : "İkinci referans fotoğrafı"} onChange={event => setAt(index, event.target.files?.[0] ?? null)}/>{files[index] && <span>{files[index].name}<button type="button" aria-label={`${index + 1}. referansı kaldır`} onClick={() => setAt(index, null)}><X size={13}/></button></span>}</div>)}
    </div>
    <div className="composer__actions"><button type="button" className="icon-button file-button" title="Referans fotoğrafı ekle" onClick={() => fileInputRef.current?.click()}><Image size={17}/><span>Fotoğraf</span></button>
      <button type="button" className="icon-button control-trigger" onClick={onOpenPromptStudio}><span>Promptu düzenle</span></button>
      <button type="button" className="icon-button control-trigger" onClick={onControls}><SlidersHorizontal size={16}/><span>Ayarlar</span></button>
      {busy ? <button type="button" className="send-button send-button--stop" onClick={onCancel} aria-label="Üretimi durdur"><Square size={15} fill="currentColor"/> Durdur</button> : <button type="button" className="send-button" onClick={onSubmit} disabled={!prompt.trim()}><ArrowUp size={17}/><span>Üret</span></button>}
    </div>
  </div></section>;
}

export function ProductionWorkspace({ api, embedded = false, initialPrompt = "", initialNegative = "", referenceFile = null, onOpenPromptStudio }: Props) {
  const [phase, setPhase] = useState<Phase>(embedded ? "ready" : "loading");
  const [instances, setInstances] = useState<ModelInstance[]>([]);
  const [llmModels, setLlmModels] = useState<GenerationModelOption[]>([]);
  const [loras, setLoras] = useState<LoraStatus[]>([]);
  const [prompt, setPrompt] = useState(initialPrompt);
  const [negative, setNegative] = useState(initialNegative);
  const [files, setFiles] = useState<File[]>(referenceFile ? [referenceFile] : []);
  const [options, setOptions] = useState<GenerationOptionsValue>(DEFAULT_OPTIONS);
  const [job, setJob] = useState<Job | null>(null);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const [loadingError, setLoadingError] = useState("");
  const [notice, setNotice] = useState("");
  const [vaultMissing, setVaultMissing] = useState(false);
  const [vaultRecover, setVaultRecover] = useState(false);
  const settingsReady = useRef(false);
  const continuousRef = useRef(false);
  const promptAssistUsedRef = useRef(false);
  const completedJobRef = useRef("");
  const submitJobRef = useRef<(assist: boolean) => Promise<void>>(async () => undefined);
  const oldAspect = useRef(DEFAULT_OPTIONS.width / DEFAULT_OPTIONS.height);

  const legacy = useMemo(() => `${window.location.origin}/?ui=legacy`, []);
  const imageModels = useMemo<GenerationModelOption[]>(() => instances
    .filter(item => item.engine === "comfy" && !isVideoModel(item))
    .map(item => ({ id: item.id, family: item.family, name: item.name, ready: item.ready, generationDefaults: item.generation_defaults })), [instances]);
  const videoModels = useMemo<GenerationModelOption[]>(() => {
    const selected = instances.filter(isVideoModel);
    const supported: GenerationModelOption[] = [];
    for (const family of ["wan22", "video"]) {
      const related = selected.filter(item => family === "wan22"
        ? item.family.toLowerCase().includes("wan") || item.id === family
        : (item.family.toLowerCase().includes("video") && !item.family.toLowerCase().includes("wan")) || item.id === family);
      if (related.length) supported.push({ id: family, family, name: related[0].name, ready: related.some(item => item.ready) });
    }
    return supported;
  }, [instances]);
  const selectedImage = instances.find(item => item.id === options.imageModelId);
  const currentFamily = options.mode === "image" ? selectedImage?.family ?? "" : options.videoModelId;
  const referenceCount = options.mode === "video" && options.videoModelId === "wan22" ? 0 : files.length;
  const busy = Boolean(job && ["queued", "running", "stopping"].includes(job.status));

  const refresh = useCallback(async () => {
    if (!embedded) setPhase("loading");
    setLoadingError("");
    try {
      if (!embedded) {
        const setup = await api.setup();
        if (setup.kurulu === false) { setPhase("setup"); return; }
        try { await api.me(); }
        catch (reason) { if (reason instanceof Error && reason.message.includes("401")) { setPhase("login"); return; } throw reason; }
        const vault = await api.vault();
        if (!vault.acik) { setVaultMissing(vault.kasa_var === false); setVaultRecover(vault.kurtarma === true); setPhase("vault"); return; }
      }
      const [status, settingsResult] = await Promise.all([api.status(), api.settings().catch(() => ({} as JsonRecord))]);
      const modelList = Array.isArray(status.model_instances) ? status.model_instances as ModelInstance[] : [];
      const llms = Array.isArray(status.llm_models) ? status.llm_models as JsonRecord[] : [];
      const settings = (settingsResult.ayarlar ?? settingsResult) as JsonRecord;
      const savedForm = (settings.form ?? {}) as JsonRecord;
      setInstances(modelList);
      setLlmModels(llms.map(item => ({ id: String(item.id ?? ""), name: String(item.ad ?? item.label ?? item.id ?? "LLM"), ready: Boolean(item.hazir) })).filter(item => item.id));
      const imageId = savedInstance(savedForm.model_instance ?? savedForm.image_model, modelList, item => item.engine === "comfy" && !isVideoModel(item));
      const savedVideoId = String(savedForm.video_model_instance ?? "");
      const videoId = savedVideoId === "video" || savedVideoId === "wan22" ? savedVideoId : "wan22";
      const storedLoras = Array.isArray(savedForm.loras) ? savedForm.loras as Array<{ id?: string; weight?: number }> : [];
      const storedWeights = Object.fromEntries(storedLoras.filter(item => typeof item.id === "string").map(item => [String(item.id), Number(item.weight ?? 1)]));
      const savedAspect = Number(savedForm.aspect_ratio);
      const width = Number(savedForm.width ?? 1024), height = Number(savedForm.height ?? 1024);
      const selectedWorkflow = modelList.find(item => item.id === imageId)?.generation_defaults ?? {};
      const nextOptions: GenerationOptionsValue = {
        ...DEFAULT_OPTIONS,
        imageModelId: imageId,
        videoModelId: videoId,
        width: Number.isFinite(width) && width > 0 ? width : 1024,
        height: Number.isFinite(height) && height > 0 ? height : 1024,
        resolutionPreset: `${width}x${height}`,
        aspectLocked: Number.isFinite(savedAspect) ? savedAspect > 0 : true,
        scaleToMax: typeof savedForm.max_olcek === "boolean" ? savedForm.max_olcek : true,
        referenceMaxSide: Number(savedForm.max_side ?? 1536),
        videoSeconds: Number(savedForm.suresn ?? 0.5), videoFps: Number(savedForm.fps ?? 8),
        videoMaxSide: Number(savedForm.video_max_side ?? 320),
        videoProfile: (savedForm.video_profile === "normal" ? "normal" : savedForm.video_profile === "fast" ? "fast" : "quality"),
        steps: Number(savedForm.steps ?? selectedWorkflow.steps ?? settings.uretim_varsayilan_steps ?? 9),
        seed: Number(savedForm.seed ?? settings.uretim_varsayilan_seed ?? -1),
        cfg: Number(savedForm.cfg ?? selectedWorkflow.cfg ?? 1), sampler: String(savedForm.sampler ?? selectedWorkflow.sampler ?? "dpmpp_2m_sde"),
        fluxBbox: Boolean(savedForm.flux3_bbox_mode), kreaEditStrength: Number(savedForm.edit_strength ?? 0.65),
        promptAssist: Boolean(settings.uretim_prompt_destek), promptTranslate: Boolean(settings.uretim_prompt_ceviri),
        promptEdit: Boolean(settings.uretim_prompt_duzenle), promptRepeat: Boolean(settings.uretim_prompt_tekrar),
        llmModelId: String(settings.uretim_llm_model ?? ""),
        loraWeights: Object.keys(storedWeights).length ? storedWeights : {},
        imageEnhance: Boolean((savedForm.enhance_after as JsonRecord | undefined)?.enabled && (savedForm.enhance_after as JsonRecord).type === "image"),
        imageEnhanceMethod: String((savedForm.enhance_after as JsonRecord | undefined)?.preset ?? "skin"),
        imageEnhanceScale: Number((savedForm.enhance_after as JsonRecord | undefined)?.scale ?? 2),
        videoEnhance: Boolean((savedForm.enhance_after as JsonRecord | undefined)?.enabled && (savedForm.enhance_after as JsonRecord).type === "video"),
        videoEnhanceMethod: String((savedForm.enhance_after as JsonRecord | undefined)?.preset ?? "seedvr2_fp8"),
        videoEnhanceScale: Number((savedForm.enhance_after as JsonRecord | undefined)?.scale ?? 2),
        videoFpsMultiplier: Number((savedForm.enhance_after as JsonRecord | undefined)?.fps_multiplier ?? 2),
        continuous: Boolean(settings.uretim_kesintisiz),
      };
      if (!nextOptions.llmModelId) nextOptions.llmModelId = String(llms.find(item => item.hazir)?.id ?? llms[0]?.id ?? "");
      setOptions(nextOptions);
      oldAspect.current = nextOptions.width / Math.max(1, nextOptions.height);
      setPrompt(typeof savedForm.prompt === "string" && !initialPrompt ? savedForm.prompt : initialPrompt);
      setNegative(typeof savedForm.negatif === "string" && !initialNegative ? savedForm.negatif : initialNegative);
      settingsReady.current = true;
      setSettingsLoaded(true);
      setPhase("ready");
    } catch (reason) {
      setLoadingError(reason instanceof Error ? reason.message : "Sunucuya bağlanılamadı.");
      setPhase("error");
    }
  }, [api, embedded]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { if (initialPrompt) setPrompt(initialPrompt); }, [initialPrompt]);
  useEffect(() => { if (initialNegative) setNegative(initialNegative); }, [initialNegative]);
  useEffect(() => { if (referenceFile) setFiles(current => current.length ? [referenceFile, ...current.slice(1)] : [referenceFile]); }, [referenceFile]);

  useEffect(() => {
    if (!currentFamily || options.mode !== "image") { setLoras([]); return; }
    let active = true;
    api.get<JsonRecord>(`/api/loras?model=${encodeURIComponent(currentFamily)}`).then(result => {
      if (!active) return;
      const list = Array.isArray(result.loras) ? result.loras as JsonRecord[] : [];
      const normalized = list.map(item => ({ id: String(item.id ?? ""), name: String(item.name ?? item.id ?? "LoRA"), ready: Boolean(item.hazir), defaultWeight: Number(item.weight ?? 1), weight: Number(item.weight ?? 1), default_selected: Boolean(item.default_selected) })).filter(item => item.id);
      setLoras(normalized);
      setOptions(current => {
        const selected = Object.keys(current.loraWeights).length ? current.loraWeights : Object.fromEntries(normalized.filter(item => item.default_selected).map(item => [item.id, item.defaultWeight]));
        return { ...current, loraWeights: selected };
      });
    }).catch(() => { if (active) setLoras([]); });
    return () => { active = false; };
  }, [api, currentFamily, options.mode]);

  useEffect(() => {
    if (!settingsLoaded || !settingsReady.current) return;
    const timer = window.setTimeout(() => {
      const image = instances.find(item => item.id === options.imageModelId);
      const enhance = options.mode === "image" ? options.imageEnhance : options.videoEnhance;
      const form = {
        prompt, negatif: negative, model: image?.family ?? "auto", image_model: image?.family ?? "auto", model_instance: image?.id ?? "",
        flux3_bbox_mode: options.fluxBbox, edit_strength: options.kreaEditStrength,
        max_side: files.length ? options.referenceMaxSide : Math.max(options.width, options.height), max_olcek: options.scaleToMax,
        width: options.width, height: options.height, video_max_side: options.videoMaxSide,
        steps: options.steps, cfg: options.cfg, sampler: options.sampler, seed: options.seed,
        workflow: options.mode === "video" ? "video" : files.length > 1 ? "cift" : "tek",
        suresn: options.videoSeconds, fps: options.videoFps, video_profile: options.videoProfile,
        video_model_instance: options.videoModelId, aspect_ratio: options.aspectLocked ? options.width / Math.max(1, options.height) : null,
        loras: Object.entries(options.loraWeights).map(([id, weight]) => ({ id, weight })),
        enhance_after: enhance ? { enabled: true, type: options.mode, preset: options.mode === "image" ? options.imageEnhanceMethod : options.videoEnhanceMethod,
          scale: options.mode === "image" ? options.imageEnhanceScale : options.videoEnhanceScale,
          fps_multiplier: options.mode === "video" ? options.videoFpsMultiplier : 1 } : null,
      };
      void api.post("/api/ayarlar", {
        form, uretim_prompt_destek: options.promptAssist, uretim_prompt_duzenle: options.promptEdit,
        uretim_prompt_ceviri: options.promptTranslate, uretim_prompt_tekrar: options.promptRepeat,
        uretim_kesintisiz: options.continuous, uretim_llm_model: options.llmModelId,
      }).then(() => setNotice("Ayarlar kaydedildi.")).catch(reason => setLoadingError(reason instanceof Error ? reason.message : "Üretim ayarları kaydedilemedi."));
    }, 650);
    return () => window.clearTimeout(timer);
  }, [api, files.length, instances, negative, options, prompt, settingsLoaded]);

  const queueJob = useCallback(async (assist: boolean) => {
    if (!prompt.trim()) return;
    const selected = options.mode === "image" ? instances.find(item => item.id === options.imageModelId) : instances.find(item => options.videoModelId === "wan22"
      ? (item.family.toLowerCase().includes("wan") || item.id === "wan22")
      : ((item.family.toLowerCase().includes("video") && !item.family.toLowerCase().includes("wan")) || item.id === "video"));
    if (!selected?.ready) { setLoadingError("Önce hazır bir model seç."); setControlsOpen(true); return; }
    if (options.mode === "video" && options.videoModelId === "wan22" && files.length) { setLoadingError("Wan 2.2 metinden video üretir. Referans fotoğraflarını kaldır veya MiniMax H3 seç."); setControlsOpen(true); return; }
    setLoadingError("");
    const toBase64 = (file: File) => new Promise<string>((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || ""); reader.onerror = reject; reader.readAsDataURL(file);
    });
    try {
      const [image, image2] = await Promise.all([files[0] ? toBase64(files[0]) : "", files[1] ? toBase64(files[1]) : ""]);
      const requestedLoras = Object.entries(options.loraWeights).map(([id, weight]) => ({ id, weight }));
      const enhanceEnabled = options.mode === "image" ? options.imageEnhance : options.videoEnhance;
      const payload: JsonRecord = {
        prompt, negative, model: options.mode === "image" ? selected.family : "auto",
        image_model: options.mode === "image" ? selected.family : "auto",
        model_instance: options.mode === "image" ? selected.id : null,
        llm_model: options.llmModelId || undefined,
        width: options.width, height: options.height,
        max_side: referenceCount ? options.referenceMaxSide : Math.max(options.width, options.height),
        max_olcek: options.scaleToMax, video_max_side: options.videoMaxSide,
        video_profile: options.videoProfile === "fast" ? "fast" : options.videoProfile,
        video_model_instance: options.mode === "video" ? options.videoModelId : null,
        flux3_bbox_mode: options.fluxBbox, edit_strength: options.kreaEditStrength,
        steps: options.steps, cfg: options.cfg, sampler: options.sampler, seed: options.seed,
        workflow: options.mode === "video" ? "video" : files.length > 1 ? "cift" : "tek",
        image_base64: image, image2_base64: image2,
        image_mime: files[0]?.type || "image/jpeg", image2_mime: files[1]?.type || "image/jpeg",
        is_tipi: options.mode === "video" ? "video-uretim" : referenceCount ? "goruntu-duzenleme" : "goruntu-uretim",
        suresn: options.videoSeconds, fps: options.videoFps,
        prompt_destek: assist, prompt_ceviri: options.mode === "image" && options.promptTranslate,
        enhance_after: enhanceEnabled ? { enabled: true, type: options.mode,
          preset: options.mode === "image" ? options.imageEnhanceMethod : options.videoEnhanceMethod,
          scale: options.mode === "image" ? options.imageEnhanceScale : options.videoEnhanceScale,
          fps_multiplier: options.mode === "video" ? options.videoFpsMultiplier : 1 } : null,
        loras: options.mode === "image" ? requestedLoras : [],
      };
      const response = await api.post<JsonRecord>("/api/kuyruk", payload);
      const id = String(response.is_id ?? response.id ?? "");
      if (!id) throw new Error("Sunucu iş numarası döndürmedi.");
      setJob({ id, status: "queued", progress: 0, message: "İstek kuyruğa alındı" });
      if (assist) promptAssistUsedRef.current = true;
    } catch (reason) { setLoadingError(reason instanceof Error ? reason.message : "İstek gönderilemedi."); continuousRef.current = false; }
  }, [api, files, instances, options, prompt, referenceCount]);
  submitJobRef.current = queueJob;

  useEffect(() => {
    if (phase !== "ready" || !job?.id || !["queued", "running", "stopping"].includes(job.status)) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await api.get<JsonRecord>(`/api/is/${encodeURIComponent(job.id)}`);
        if (cancelled) return;
        const progress = data.ilerleme as JsonRecord | undefined;
        const state = String(data.durum ?? "");
        const status = state === "kuyrukta" ? "queued" : state === "calisiyor" ? "running" : state === "bitti" ? "done" : state === "hata" ? "error" : state === "durduruldu" ? "cancelled" : state;
        const raw = Number(progress?.oran ?? 0);
        const next: Job = { ...job, status, progress: raw > 1 ? raw / 100 : raw, message: String(data.hata ?? progress?.calisan ?? (state === "kuyrukta" ? "Sırada bekliyor" : "Üretiliyor")), result: data.sonuc as JsonRecord | undefined };
        setJob(next);
        if (["done", "error", "cancelled"].includes(status) && status !== "done") continuousRef.current = false;
        if (status === "done" && continuousRef.current && completedJobRef.current !== job.id) {
          completedJobRef.current = job.id;
          window.setTimeout(() => {
            if (continuousRef.current) void submitJobRef.current(options.promptAssist && options.promptRepeat && promptAssistUsedRef.current);
          }, 250);
        }
      } catch (reason) { if (!cancelled) setJob({ ...job, message: reason instanceof Error ? reason.message : "İş durumu alınamadı." }); }
    };
    void poll(); const timer = window.setInterval(() => void poll(), 1200);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [api, job?.id, job?.status, options.promptAssist, options.promptRepeat, phase]);

  const submit = () => {
    if (options.promptEdit && onOpenPromptStudio) { onOpenPromptStudio(prompt, true, negative); return; }
    continuousRef.current = options.continuous;
    promptAssistUsedRef.current = false;
    completedJobRef.current = "";
    void queueJob(options.promptAssist);
  };
  const cancel = async () => {
    continuousRef.current = false;
    if (!job?.id) return;
    try { await api.post("/api/is/iptal", { is_id: job.id }); setJob({ ...job, status: "stopping", message: "Durdurma isteği gönderildi" }); }
    catch (reason) { setLoadingError(reason instanceof Error ? reason.message : "İş durdurulamadı."); }
  };

  const changeOptions = (patch: Partial<GenerationOptionsValue>) => {
    setOptions(current => {
      const next = { ...current, ...patch };
      if (patch.imageModelId !== undefined && patch.imageModelId !== current.imageModelId) {
        const defaults = instances.find(item => item.id === patch.imageModelId)?.generation_defaults ?? {};
        if (typeof defaults.steps === "number") next.steps = defaults.steps;
        if (typeof defaults.cfg === "number") next.cfg = defaults.cfg;
        if (typeof defaults.sampler === "string") next.sampler = defaults.sampler;
      }
      if (patch.width !== undefined && next.aspectLocked) { next.height = Math.max(16, Math.round(patch.width / Math.max(oldAspect.current, 0.01) / 16) * 16); }
      if (patch.height !== undefined && next.aspectLocked) { next.width = Math.max(16, Math.round(patch.height * oldAspect.current / 16) * 16); }
      if (patch.promptAssist) next.promptTranslate = false;
      if (patch.promptTranslate) next.promptAssist = false;
      if (patch.width !== undefined || patch.height !== undefined) oldAspect.current = next.width / Math.max(1, next.height);
      return next;
    });
  };

  if (phase !== "ready") return <AuthGate phase={phase} refresh={() => void refresh()} api={api} vaultMissing={vaultMissing} vaultRecover={vaultRecover}/>;
  return <main id="production" className="production-main production-main--workspace">
    <div className="workspace-heading"><div><p className="eyebrow">YENİ ÇALIŞMA</p><h1>Üretim alanı</h1></div><span className="connection-state"><i/> Bağlı</span></div>
    <div className="workspace-grid workspace-grid--integrated"><div className="workspace-center"><MediaCanvas mode={options.mode} job={job}/>{loadingError && <p className="workspace-error" role="alert">{loadingError}</p>}{notice && <p className="production-settings-saved" role="status">{notice}</p>}
      <Composer mode={options.mode} setMode={mode => changeOptions({ mode })} prompt={prompt} setPrompt={setPrompt} busy={busy} onSubmit={submit} onCancel={() => void cancel()} files={files} setFiles={setFiles} onControls={() => setControlsOpen(true)} onOpenPromptStudio={() => onOpenPromptStudio?.(prompt, options.promptEdit, negative)}/>
    </div></div>
    <GenerationOptions value={options} onChange={changeOptions} imageModels={imageModels} videoModels={videoModels} llmModels={llmModels} loras={loras} referenceCount={referenceCount} open={controlsOpen} onClose={() => setControlsOpen(false)}/>
    {!embedded && <footer className="workspace-footer"><span>Çalışma alanı</span><a href={legacy}>Önceki arayüz ↗</a></footer>}
  </main>;
}
