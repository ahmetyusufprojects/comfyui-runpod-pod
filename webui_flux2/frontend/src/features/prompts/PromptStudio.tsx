import { useEffect, useRef, useState } from "react";
import { ArrowUp, Check, LoaderCircle, X } from "lucide-react";
import type { JsonRecord, ProductionApi } from "../../lib/api/client";
import "./PromptStudio.css";

export type PromptMode = "fikir" | "gorsel" | "birlestir" | "video" | "duzenle" | "ceviri";
export type PromptProfile = "auto" | "general" | "z_image" | "flux3" | "krea2" | "illustrious" | "minimaxh3" | "wan22";
export interface PromptResult extends JsonRecord {
  positive?: string;
  positive_tr?: string;
  negative?: string;
  caption_tr?: string;
  tags?: string[];
  sure_ms?: number;
  token?: number;
  tok_s?: number;
  uyari?: string;
}
export interface PromptPresetDefaults extends JsonRecord {
  model?: string;
  workflow?: string;
  steps?: number;
  cfg?: number;
  sampler?: string;
  width?: number;
  height?: number;
  max_side?: number;
  max_olcek?: boolean;
  video_model_instance?: string;
  video_profile?: string;
  video_max_side?: number;
  suresn?: number;
  fps?: number;
  image_model?: string;
  model_instance?: string;
  loras?: Array<{ id: string; weight: number }>;
}
export interface SavedPromptPreset extends JsonRecord {
  ad: string;
  prompt: string;
  negative?: string;
}
type PromptRef = { name: string; mime: string; data: string; preview: string };
type PendingPrompt = { id: string; mode: PromptMode; text: string; refs: PromptRef[] };
type HistoryEntry = PromptResult & { id: string; yildiz?: boolean; tarih?: string; mod?: string; metin?: string };
type LlmOption = { id: string; ad: string; hazir?: boolean };
type JobStatus = "queued" | "running" | "stopping" | "done" | "error" | "cancelled";
type ActiveJob = { id: string; status: JobStatus; message: string };

interface Props {
  api: ProductionApi;
  initialPrompt?: string;
  initialNegative?: string;
  editMode?: boolean;
  onApply?: (result: PromptResult) => void;
  presetDefaults?: PromptPresetDefaults;
}

const PROFILE_OPTIONS: Array<[PromptProfile, string]> = [
  ["auto", "Otomatik · üretim modelini izle"], ["general", "Genel"], ["z_image", "Z-Image"],
  ["flux3", "Flux 3 Klein"], ["krea2", "Krea 2"], ["illustrious", "Illustrious"],
  ["minimaxh3", "MiniMax H3 · video"], ["wan22", "Wan 2.2 · sessiz video"],
];

function bytesToData(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error(`${file.name} okunamadı.`));
    reader.readAsDataURL(file);
  });
}

function toJobStatus(value: unknown): JobStatus {
  if (value === "kuyrukta") return "queued";
  if (value === "calisiyor") return "running";
  if (value === "bitti") return "done";
  if (value === "hata") return "error";
  if (value === "durduruldu") return "cancelled";
  return "running";
}

function buildRequest(item: PendingPrompt, profile: PromptProfile, llmModel: string, creativity: number, language: string, bbox: boolean, editContext: string, initialPrompt: string): JsonRecord {
  const text = item.mode === "duzenle"
    ? `Mevcut promptu kullanıcının isteğine göre düzenle.\n\nMevcut prompt:\n${initialPrompt}\n\nDüzenleme isteği:\n${item.text}\n\nÖnceki düzenleme bağlamı:\n${editContext}`
    : item.text;
  return {
    mod: item.mode,
    metin: text,
    dil: language,
    yaraticilik: creativity,
    gorseller: item.refs.map((ref) => ({ mime: ref.mime, veri: ref.data })),
    model_prompt_profile: profile,
    llm_model: llmModel || undefined,
    bbox_mode: profile === "flux3" && bbox,
  };
}

export function PromptStudio({ api, initialPrompt = "", initialNegative = "", editMode = false, onApply, presetDefaults }: Props) {
  const [mode, setMode] = useState<PromptMode>(editMode ? "duzenle" : "fikir");
  const [profile, setProfile] = useState<PromptProfile>("auto");
  const [language, setLanguage] = useState("en");
  const [creativity, setCreativity] = useState(0.7);
  const [repeat, setRepeat] = useState(1);
  const [llms, setLlms] = useState<LlmOption[]>([]);
  const [llmModel, setLlmModel] = useState("");
  const [bbox, setBbox] = useState(false);
  const [draft, setDraft] = useState("");
  const [refs, setRefs] = useState<PromptRef[]>([]);
  const [pending, setPending] = useState<PendingPrompt[]>([]);
  const pendingRef = useRef<PendingPrompt[]>([]);
  const runner = useRef(false);
  const [queueRunning, setQueueRunning] = useState(false);
  const [activeJob, setActiveJob] = useState<ActiveJob | null>(null);
  const [result, setResult] = useState<PromptResult | null>(null);
  const [negative, setNegative] = useState(initialNegative);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historySearch, setHistorySearch] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [saveName, setSaveName] = useState("");
  const [saveStatus, setSaveStatus] = useState("");
  const [presets, setPresets] = useState<SavedPromptPreset[]>([]);
  const [presetsLoading, setPresetsLoading] = useState(false);
  const [presetError, setPresetError] = useState("");
  const [editContext, setEditContext] = useState("");
  const editContextRef = useRef("");
  const fileInputs = useRef<Array<HTMLInputElement | null>>([]);
  const reconnectRef = useRef(false);

  const loadPresets = async () => {
    setPresetsLoading(true);
    setPresetError("");
    try {
      const data = await api.get<JsonRecord>("/api/promptlar");
      setPresets(Array.isArray(data.promptlar) ? data.promptlar as SavedPromptPreset[] : []);
    } catch (reason) {
      setPresetError(reason instanceof Error ? reason.message : "Kayıtlı promptlar yüklenemedi.");
    } finally {
      setPresetsLoading(false);
    }
  };

  const applyPreset = (preset: SavedPromptPreset) => {
    const savedPrompt = String(preset.prompt ?? "");
    const savedNegative = String(preset.negative ?? "");
    setResult({ ...preset, positive: savedPrompt, negative: savedNegative });
    setNegative(savedNegative);
    onApply?.({ ...preset, positive: savedPrompt, negative: savedNegative });
    setStatus(`“${preset.ad}” kaydı üretim alanına uygulandı.`);
  };

  const deletePreset = async (preset: SavedPromptPreset) => {
    try {
      await api.post("/api/promptlar/sil", { ad: preset.ad });
      setPresets(current => current.filter(item => item.ad !== preset.ad));
      setStatus(`“${preset.ad}” kaydı silindi.`);
    } catch (reason) {
      setPresetError(reason instanceof Error ? reason.message : "Prompt kaydı silinemedi.");
    }
  };

  useEffect(() => {
    setMode(editMode ? "duzenle" : "fikir");
    setEditContext("");
    editContextRef.current = "";
  }, [editMode]);

  useEffect(() => { setNegative(initialNegative); }, [initialNegative]);

  useEffect(() => {
    let alive = true;
    api.get<JsonRecord>("/api/durum").then((data) => {
      if (!alive) return;
      const models = Array.isArray(data.llm_models) ? data.llm_models as LlmOption[] : [];
      setLlms(models);
      setLlmModel((current) => current || models.find((item) => item.hazir)?.id || models[0]?.id || "");
    }).catch((reason: unknown) => { if (alive) setError(reason instanceof Error ? reason.message : "LLM modelleri yüklenemedi."); });
    return () => { alive = false; };
  }, [api]);

  const loadHistory = async (query = historySearch) => {
    setHistoryLoading(true);
    try {
      const data = await api.get<JsonRecord>(`/api/prompt-gecmisi?limit=200&q=${encodeURIComponent(query)}`);
      setHistory(Array.isArray(data.kayitlar) ? data.kayitlar as HistoryEntry[] : []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Prompt geçmişi yüklenemedi.");
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => { void loadHistory(""); }, [api]);
  useEffect(() => { void loadPresets(); }, [api]);

  useEffect(() => {
    let alive = true;
    const reconnect = async () => {
      if (reconnectRef.current) return;
      let user = "";
      let jobId = "";
      try {
        const me = await api.me();
        user = String(me.kullanici ?? "");
        if (!user) return;
        const jobs = await api.get<JsonRecord>("/api/isler");
        if (!alive) return;
        const active = Array.isArray(jobs.isler) ? jobs.isler as JsonRecord[] : [];
        const promptJobs = active.filter(job => job.tur === "llm_prompt" && (job.durum === "kuyrukta" || job.durum === "calisiyor"));
        let savedId = "";
        try { savedId = localStorage.getItem(`llm_is_llm_prompt_${user}`) ?? ""; } catch { /* storage may be disabled */ }
        jobId = (savedId && promptJobs.some(job => job.is_id === savedId) ? savedId : String(promptJobs[promptJobs.length - 1]?.is_id ?? ""));
        if (!jobId) return;
        reconnectRef.current = true;
        try { localStorage.setItem(`llm_is_llm_prompt_${user}`, jobId); } catch { /* storage may be disabled */ }
        runner.current = true;
        setQueueRunning(true);
        setActiveJob({ id: jobId, status: "running", message: "Önceden başlatılan prompt işi izleniyor" });
        const completed = await pollUntilDone(jobId, () => alive);
        if (!alive) return;
        if (completed.state === "done" && completed.data.sonuc) {
          const nextResult = completed.data.sonuc as PromptResult;
          setResult(nextResult);
          setNegative(String(nextResult.negative ?? ""));
          setStatus("Önceden başlatılan prompt işi tamamlandı.");
          await loadHistory();
        } else if (completed.state === "cancelled") {
          setStatus("Önceden başlatılan prompt işi durduruldu.");
        } else if (completed.state === "error") {
          setError(String(completed.data.hata ?? "Önceden başlatılan prompt işi tamamlanamadı."));
        }
      } catch (reason) {
        if (alive) setError(reason instanceof Error ? reason.message : "Önceki prompt işi izlenemedi.");
      } finally {
        if (alive) {
          try { if (localStorage.getItem(`llm_is_llm_prompt_${user}`) === jobId) localStorage.removeItem(`llm_is_llm_prompt_${user}`); } catch { /* storage may be disabled */ }
          setActiveJob(null);
          setQueueRunning(false);
          runner.current = false;
        }
      }
    };
    void reconnect();
    return () => { alive = false; };
  }, [api]);

  const addToQueue = () => {
    setError("");
    const text = draft.trim();
    if (!text && mode !== "birlestir") { setError(mode === "gorsel" ? "Görseldeki değişikliği yaz." : "Önce bir istek yaz."); return; }
    if (mode === "gorsel" && refs.length < 1) { setError("Görsel düzenlemesi için bir referans fotoğraf ekle."); return; }
    if (mode === "birlestir" && refs.length < 2) { setError("Görsel birleştirmek için iki referans ekle."); return; }
    if (mode === "duzenle" && !initialPrompt.trim()) { setError("Düzenlenecek üretim promptu boş."); return; }
    const items = Array.from({ length: Math.max(1, Math.min(10, Math.floor(repeat) || 1)) }, (_, index) => ({
      id: `${Date.now()}-${index}-${Math.random().toString(36).slice(2, 7)}`,
      mode, text, refs: [...refs],
    }));
    const next = [...pendingRef.current, ...items];
    pendingRef.current = next;
    setPending(next);
    setDraft("");
    setStatus(`${items.length} prompt sıraya eklendi.`);
  };

  const movePending = (id: string, direction: -1 | 1) => {
    const next = [...pendingRef.current];
    const index = next.findIndex((item) => item.id === id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    pendingRef.current = next;
    setPending(next);
  };

  const removePending = (id: string) => {
    const next = pendingRef.current.filter((item) => item.id !== id);
    pendingRef.current = next;
    setPending(next);
  };

  const pollUntilDone = async (id: string, stillActive: () => boolean = () => true): Promise<{ state: JobStatus; data: JsonRecord }> => {
    while (stillActive()) {
      const data = await api.get<JsonRecord>(`/api/is/${encodeURIComponent(id)}`);
      const state = toJobStatus(data.durum);
      const progress = data.ilerleme && typeof data.ilerleme === "object" ? data.ilerleme as JsonRecord : {};
      const message = String(progress.calisan ?? (state === "queued" ? `LLM kuyruğunda${data.sira ? ` · sıra ${data.sira}` : ""}` : "Prompt hazırlanıyor"));
      if (stillActive()) setActiveJob({ id, status: state, message });
      const current = data.sonuc && typeof data.sonuc === "object" ? data.sonuc as JsonRecord : {};
      if (state === "done" || state === "error" || state === "cancelled") return { state, data: { ...data, sonuc: current } };
      await new Promise((resolve) => window.setTimeout(resolve, 850));
    }
    return { state: "cancelled", data: {} };
  };

  const runQueue = async () => {
    if (runner.current || pendingRef.current.length === 0) return;
    runner.current = true;
    setQueueRunning(true);
    setError("");
    let currentEditContext = editContextRef.current;
    let currentPrompt = initialPrompt;
    let username = "";
    try { username = String((await api.me()).kullanici ?? ""); } catch { /* queue still works if identity lookup is unavailable */ }
    const taskKey = username ? `llm_is_llm_prompt_${username}` : "";
    while (pendingRef.current.length) {
      const [item, ...rest] = pendingRef.current;
      pendingRef.current = rest;
      setPending(rest);
      try {
        const queued = await api.post<JsonRecord>("/api/prompt-uret", buildRequest(item, profile, llmModel, creativity, language, bbox, currentEditContext, currentPrompt));
        if (typeof queued.is_id !== "string") throw new Error("Sunucu iş numarası döndürmedi.");
        if (taskKey) { try { localStorage.setItem(taskKey, queued.is_id); } catch { /* storage may be disabled */ } }
        setActiveJob({ id: queued.is_id, status: "queued", message: queued.sira ? `LLM kuyruğunda · sıra ${queued.sira}` : "Prompt hazırlanıyor" });
        const completed = await pollUntilDone(queued.is_id);
        if (taskKey) { try { if (localStorage.getItem(taskKey) === queued.is_id) localStorage.removeItem(taskKey); } catch { /* storage may be disabled */ } }
        setActiveJob(null);
        if (completed.state === "done") {
          const nextResult = completed.data.sonuc as PromptResult;
          setResult(nextResult);
          setNegative(String(nextResult.negative ?? ""));
          if (editMode && nextResult.positive) {
            currentEditContext = `${currentEditContext}${currentEditContext ? "\n\n" : ""}İstek: ${item.text}\nDüzenlenmiş prompt: ${nextResult.positive}`;
            currentPrompt = nextResult.positive;
            editContextRef.current = currentEditContext;
            setEditContext(currentEditContext);
            onApply?.(nextResult);
          }
          setStatus("Prompt hazır.");
          await loadHistory();
        } else if (completed.state === "cancelled") {
          setStatus("İş durduruldu; sıradaki prompt devam edecek.");
        } else {
          setError(String(completed.data.hata ?? "Prompt üretimi tamamlanamadı."));
        }
      } catch (reason) {
        if (taskKey) { try { localStorage.removeItem(taskKey); } catch { /* storage may be disabled */ } }
        setActiveJob(null);
        setError(reason instanceof Error ? reason.message : "Prompt isteği gönderilemedi.");
      }
    }
    setQueueRunning(false);
    runner.current = false;
  };

  const cancelActive = async () => {
    if (!activeJob) return;
    setActiveJob({ ...activeJob, status: "stopping", message: "Durduruluyor…" });
    try { await api.post("/api/is/iptal", { is_id: activeJob.id }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Prompt durdurulamadı."); }
  };

  const addImages = async (index: number, files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    setError("");
    try {
      const dataUrl = await bytesToData(file);
      const [header, data] = dataUrl.split(",", 2);
      const mime = /data:([^;]+)/.exec(header)?.[1] || file.type || "image/jpeg";
      setRefs((current) => { const next = [...current]; next[index] = { name: file.name, mime, data, preview: dataUrl }; return next; });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Görsel eklenemedi."); }
    finally { const input = fileInputs.current[index]; if (input) input.value = ""; }
  };

  const removeImage = (index: number) => setRefs((current) => current.filter((_, itemIndex) => itemIndex !== index));

  const toggleFavorite = async (entry: HistoryEntry) => {
    try {
      await api.post("/api/prompt-gecmisi/yildiz", { id: entry.id, yildiz: !entry.yildiz });
      await loadHistory();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Yıldız değiştirilemedi."); }
  };

  const deleteHistory = async (entry: HistoryEntry) => {
    try {
      await api.post("/api/prompt-gecmisi/sil", { id: entry.id });
      setHistory((current) => current.filter((item) => item.id !== entry.id));
      setStatus("Prompt geçmişinden silindi.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Kayıt silinemedi."); }
  };

  const clearHistory = async () => {
    if (!window.confirm("Tüm prompt geçmişi silinsin mi?")) return;
    try {
      await api.post("/api/prompt-gecmisi/temizle", {});
      setHistory([]);
      setStatus("Prompt geçmişi temizlendi.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Geçmiş temizlenemedi."); }
  };

  const applyResult = (value: PromptResult) => {
    onApply?.({ ...value, negative });
    setStatus(editMode ? "Düzenleme üretim promptuna uygulandı." : "Prompt üretim alanına aktarıldı.");
  };

  const savePreset = async () => {
    if (!result?.positive || !saveName.trim()) { setSaveStatus("Önce bir ad yaz."); return; }
    try {
      const defaults = presetDefaults === undefined
        ? { model: "z_image", workflow: "otomatik", steps: 9, cfg: 1, sampler: "euler", width: 1024, height: 1024, max_side: 1536 }
        : presetDefaults;
      await api.post("/api/promptlar", { ad: saveName.trim(), prompt: result.positive, negative, ...defaults });
      setSaveName("");
      setSaveStatus("Prompt kayıtlarına eklendi.");
      await loadPresets();
    } catch (reason) { setSaveStatus(reason instanceof Error ? reason.message : "Prompt kaydedilemedi."); }
  };

  const copyResult = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(`EN: ${result.positive ?? ""}\nTR: ${result.positive_tr ?? ""}${negative ? `\nNEGATIVE: ${negative}` : ""}`);
      setStatus("Prompt panoya kopyalandı.");
    } catch { setError("Panoya kopyalama izni verilmedi."); }
  };

  return <section className="prompt-studio" aria-labelledby="prompt-studio-title">
    <header className="prompt-studio__header"><div><p className="eyebrow">YAZI VE REFERANSLARLA</p><h1 id="prompt-studio-title">Prompt stüdyosu</h1><p>Model seç, isteğini sıraya koy ve sonuçları geçmişinden yeniden kullan.</p></div>
      <label className="prompt-studio__model"><span>LLM modeli</span><select aria-label="Prompt LLM modeli" value={llmModel} onChange={(event) => setLlmModel(event.target.value)}>
        {!llms.length && <option value="">Model bilgisi yükleniyor</option>}{llms.map((item) => <option key={item.id} value={item.id}>{item.ad}{item.hazir ? "" : " · kurulu değil"}</option>)}
      </select></label>
    </header>

    <div className="prompt-studio__layout">
      <main className="prompt-studio__editor">
        <section className="prompt-card" aria-label="Prompt isteği">
          <div className="prompt-card__top"><div><p className="eyebrow">İSTEK</p><h2>{editMode ? "Mevcut promptu düzenle" : "Yeni prompt hazırla"}</h2></div><span className="prompt-mode-badge">✳ {mode}</span></div>
          {editMode && <div className="prompt-current"><span>Üretim promptu</span><p>{initialPrompt || "Henüz prompt yok."}</p></div>}
          {editMode && editContext && <details className="prompt-details"><summary>Bu üretimdeki düzenleme bağlamı</summary><p>{editContext}</p></details>}
          <div className="prompt-fields">
            <label className="prompt-field">Çalışma biçimi<select value={mode} onChange={(event) => setMode(event.target.value as PromptMode)}>
              <option value="fikir">Fikirden prompt</option><option value="gorsel">Görsel düzenleme</option><option value="birlestir">İki görseli birleştir</option><option value="video">Video sahnesi</option><option value="duzenle">Mevcut promptu düzenle</option><option value="ceviri">Prompt çevirisi</option>
            </select></label>
            <label className="prompt-field">Sistem promptu<select value={profile} onChange={(event) => setProfile(event.target.value as PromptProfile)}>{PROFILE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          <p className="prompt-helper" role="status">{profile === "auto" ? "Otomatik seçim üretim modelini izler; modelin kendisini değiştirmez." : `${PROFILE_OPTIONS.find(([value]) => value === profile)?.[1]} prompt yönergesi kullanılır.`}</p>
          <div className="prompt-ref-grid">
            {[0, 1].map((index) => <div className={`prompt-ref${refs[index] ? " prompt-ref--filled" : ""}`} key={index}>
              <input ref={(element) => { fileInputs.current[index] = element; }} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(event) => void addImages(index, event.target.files)}/>
              {refs[index] ? <><img src={refs[index].preview} alt={`Referans ${index + 1}: ${refs[index].name}`}/><span>{refs[index].name}</span><button className="prompt-icon-btn" aria-label={`Referans ${index + 1} fotoğrafını kaldır`} onClick={() => removeImage(index)}><X size={14}/></button></> : <button className="prompt-ref__add" onClick={() => fileInputs.current[index]?.click()}><span aria-hidden="true">＋</span><span>Referans {index + 1}</span><small>Fotoğraf ekle</small></button>}
            </div>)}
          </div>
          <label className="prompt-field prompt-field--wide">İsteğin<textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={4} placeholder={mode === "video" ? "Sahneyi, hareketi ve kamera davranışını anlat…" : mode === "gorsel" ? "Fotoğrafta ne değişsin?" : mode === "duzenle" ? "Mevcut promptta neyi değiştirelim?" : "Ne üretmek istiyorsun?"}/></label>
          <div className="prompt-options">
            <label>Çıktı dili<select value={language} onChange={(event) => setLanguage(event.target.value)}><option value="en">İngilizce</option><option value="tr">Türkçe</option></select></label>
            <label>Yaratıcılık<input type="number" min={0.2} max={1.2} step={0.1} value={creativity} onChange={(event) => setCreativity(Math.min(1.2, Math.max(0.2, Number(event.target.value))))}/></label>
            <label>Prompt sayısı<input type="number" min={1} max={10} step={1} value={repeat} onChange={(event) => setRepeat(Math.min(10, Math.max(1, Number(event.target.value))))}/></label>
          </div>
          {profile === "flux3" && <label className="prompt-check"><input type="checkbox" checked={bbox} onChange={(event) => setBbox(event.target.checked)}/><span><strong>Flux bbox yerleşim tablosu</strong><small>Çıktıya nesne kutularını ekler; ana JSON alanları değişmez.</small></span></label>}
          {error && <p className="prompt-error" role="alert">{error}</p>}
          {status && <p className="prompt-status" role="status">{status}</p>}
          <div className="prompt-actions">
            {queueRunning ? <button className="prompt-primary" onClick={() => void cancelActive()} disabled={!activeJob || activeJob.status === "stopping"}><X size={15}/> Aktif işi durdur</button> : <button className="prompt-secondary" onClick={addToQueue}>＋ Sıraya ekle</button>}
            {!queueRunning && <button className="prompt-primary" onClick={() => void runQueue()} disabled={pending.length === 0}>✳ Kuyruğu başlat{pending.length ? ` · ${pending.length}` : ""}</button>}
          </div>
        </section>

        {(pending.length > 0 || activeJob) && <section className="prompt-queue" aria-label="Prompt kuyruğu">
          <div className="prompt-card__top"><div><p className="eyebrow">İŞ KUYRUĞU</p><h2>{activeJob ? "İşleniyor" : "Bekleyen promptlar"}</h2></div>{activeJob && <span className="prompt-job"><LoaderCircle size={13} className="prompt-spinner"/>{activeJob.message}</span>}</div>
          {pending.map((item, index) => <div className="prompt-queue-item" key={item.id}><span className="prompt-queue-item__number">{index + 1}</span><span className="prompt-queue-item__text">{item.text || `${item.refs.length} referans görsel`}</span><button className="prompt-icon-btn" onClick={() => movePending(item.id, -1)} aria-label="Promptu yukarı taşı" disabled={index === 0 || queueRunning}><ArrowUp size={14}/></button><button className="prompt-icon-btn" onClick={() => movePending(item.id, 1)} aria-label="Promptu aşağı taşı" disabled={index === pending.length - 1 || queueRunning}>↓</button><button className="prompt-icon-btn" onClick={() => removePending(item.id)} aria-label="Promptu kuyruktan kaldır" disabled={queueRunning}>×</button></div>)}
        </section>}

        {result && <section className="prompt-result" aria-live="polite"><div className="prompt-card__top"><div><p className="eyebrow">SONUÇ</p><h2>Hazır prompt</h2></div><span className="prompt-result__meta">{result.sure_ms ? `${(result.sure_ms / 1000).toFixed(1)} sn` : ""}{result.token ? ` · ${result.token} token` : ""}</span></div>
          <label className="prompt-field prompt-field--wide">Prompt<textarea rows={5} value={result.positive ?? ""} readOnly/></label>
          {result.positive_tr && <details className="prompt-details"><summary>Türkçe prompt</summary><p>{result.positive_tr}</p></details>}
          <label className="prompt-field prompt-field--wide">Negatif prompt<textarea rows={2} value={negative} onChange={(event) => setNegative(event.target.value)} placeholder="İsteğe bağlı"/></label>
          {result.caption_tr && <p className="prompt-caption">{result.caption_tr}</p>}
          {!!result.tags?.length && <div className="prompt-tags">{result.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>}
          {result.uyari && <p className="prompt-warning">{result.uyari}</p>}
          <div className="prompt-actions"><button className="prompt-primary" onClick={() => applyResult(result)}><Check size={15}/>{editMode ? "Düzenlemeyi uygula" : "Üretime aktar"}</button><button className="prompt-secondary" onClick={() => void copyResult()}>⧉ Kopyala</button></div>
          <div className="prompt-save"><input value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder="Prompt kayıt adı" aria-label="Prompt kayıt adı"/><button className="prompt-secondary" onClick={() => void savePreset()}>Kaydet</button>{saveStatus && <span role="status">{saveStatus}</span>}</div>
        </section>}
      </main>

      <aside className="prompt-history" aria-label="Kayıtlar ve prompt geçmişi">
        <section className="prompt-saved-presets" aria-label="Kayıtlı promptlar">
          <div className="prompt-history__head"><div><p className="eyebrow">KAYITLAR</p><h2>Hazır promptlar</h2></div><button className="prompt-icon-btn" onClick={() => void loadPresets()} aria-label="Kayıtlı promptları yenile" disabled={presetsLoading}><LoaderCircle size={14} className={presetsLoading ? "prompt-spinner" : ""}/></button></div>
          {presetError && <p className="prompt-error" role="alert">{presetError}</p>}
          {presetsLoading && <p className="prompt-history__empty">Kayıtlar yükleniyor…</p>}
          {!presetsLoading && !presets.length && !presetError && <p className="prompt-history__empty">Henüz kayıtlı prompt yok.</p>}
          <div className="prompt-saved-presets__list">{presets.map(preset => <article className="prompt-saved-preset" key={preset.ad}>
            <div><b>{preset.ad}</b><p>{preset.prompt}</p><small>{[preset.model, preset.workflow, preset.steps ? `${preset.steps} adım` : "", preset.width && preset.height ? `${preset.width} × ${preset.height}` : ""].filter(Boolean).join(" · ")}</small></div>
            <button className="prompt-history-item__apply" onClick={() => applyPreset(preset)}>Uygula</button>
            <button className="prompt-icon-btn prompt-saved-preset__delete" onClick={() => void deletePreset(preset)} aria-label={`${preset.ad} kaydını sil`}>×</button>
          </article>)}</div>
        </section>
        <div className="prompt-history__head"><div><p className="eyebrow">GEÇMİŞ</p><h2>Son promptlar</h2></div><button className="prompt-icon-btn" onClick={() => void loadHistory()} aria-label="Prompt geçmişini yenile" disabled={historyLoading}><LoaderCircle size={14} className={historyLoading ? "prompt-spinner" : ""}/></button></div>
        <input className="prompt-history__search" value={historySearch} onChange={(event) => { setHistorySearch(event.target.value); void loadHistory(event.target.value); }} placeholder="Prompt geçmişinde ara…" aria-label="Prompt geçmişinde ara"/>
        <div className="prompt-history__filters"><button className={favoritesOnly ? "is-active" : ""} onClick={() => setFavoritesOnly((value) => !value)}>☆ Favoriler</button><button onClick={() => void clearHistory()}>× Tümünü sil</button></div>
        <div className="prompt-history__list" aria-live="polite">
          {historyLoading && <p className="prompt-history__empty">Geçmiş yükleniyor…</p>}
          {!historyLoading && history.filter((item) => !favoritesOnly || item.yildiz).length === 0 && <p className="prompt-history__empty">{favoritesOnly ? "Favori prompt yok." : "Henüz prompt kaydı yok."}</p>}
          {history.filter((item) => !favoritesOnly || item.yildiz).map((entry) => <article className="prompt-history-item" key={entry.id}>
            <div className="prompt-history-item__meta"><span>{entry.tarih?.replace("T", " ") ?? entry.mod ?? "Prompt"}</span><div><button className="prompt-icon-btn" onClick={() => void toggleFavorite(entry)} aria-label={entry.yildiz ? "Favoriden çıkar" : "Favoriye ekle"}>{entry.yildiz ? "★" : "☆"}</button><button className="prompt-icon-btn" onClick={() => void deleteHistory(entry)} aria-label="Geçmiş kaydını sil">×</button></div></div>
            <button className="prompt-history-item__open" onClick={() => { setResult(entry); setNegative(String(entry.negative ?? "")); }}>{entry.positive || entry.caption_tr || entry.metin || "Prompt"}</button>
            <button className="prompt-history-item__apply" onClick={() => applyResult(entry)}>{editMode ? "Düzenleme alanına uygula" : "Üretime aktar"}</button>
          </article>)}
        </div>
      </aside>
    </div>
  </section>;
}
