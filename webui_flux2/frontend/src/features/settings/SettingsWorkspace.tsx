import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, KeyRound, LoaderCircle, RefreshCw, Save, Shield, SlidersHorizontal } from "lucide-react";
import type { JsonRecord, ProductionApi } from "../../lib/api/client";

type Tab = "general" | "llm" | "prompts" | "security";
type Dict = Record<string, any>;
interface Props { api: ProductionApi; userName: string; }
type LlmModel = { id: string; ad: string; hazir?: boolean; ready?: boolean; mtp_dahili?: boolean; mtp_dosyasi?: string; gorsel_dosyasi?: string; tuning_defaults?: Dict };

function object(value: unknown): Dict { return value && typeof value === "object" ? value as Dict : {}; }
function number(value: unknown, fallback: number): number { const n = Number(value); return Number.isFinite(n) ? n : fallback; }

export function SettingsWorkspace({ api, userName }: Props) {
  const [tab, setTab] = useState<Tab>("general");
  const [settings, setSettings] = useState<Dict>({});
  const [defaults, setDefaults] = useState<Dict>({});
  const [models, setModels] = useState<LlmModel[]>([]);
  const [vaultMinutes, setVaultMinutes] = useState(30);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [vaultCurrent, setVaultCurrent] = useState("");
  const [vaultNext, setVaultNext] = useState("");
  const [vaultRepeat, setVaultRepeat] = useState("");
  const [reloadJob, setReloadJob] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [response, status, vault] = await Promise.all([
        api.settings(), api.status().catch(() => ({} as JsonRecord)), api.vault().catch(() => ({} as JsonRecord)),
      ]);
      const data = object(response.ayarlar);
      setSettings(data);
      setDefaults(object(response.llm_varsayilanlar));
      setModels(Array.isArray(status.llm_models) ? status.llm_models as LlmModel[] : []);
      setVaultMinutes(number(vault.bekleme_dk, 30));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Ayarlar yüklenemedi."); }
    finally { setLoading(false); }
  }, [api]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!reloadJob) return;
    let alive = true;
    const poll = async () => {
      try {
        const result = await api.get<JsonRecord>(`/api/is/${encodeURIComponent(reloadJob)}`);
        if (!alive) return;
        if (result.durum === "bitti") { setMessage("Model hazır."); setReloadJob(""); }
        else if (["hata", "durduruldu"].includes(String(result.durum))) { setError(String(result.hata || "Model hazırlanamadı.")); setReloadJob(""); }
      } catch (reason) { if (alive) setError(reason instanceof Error ? reason.message : "Model durumu alınamadı."); }
    };
    void poll(); const timer = window.setInterval(() => void poll(), 1200);
    return () => { alive = false; window.clearInterval(timer); };
  }, [api, reloadJob]);

  const save = async (payload: Dict, success: string) => {
    setBusy(true); setError(""); setMessage("");
    try { const result = await api.post<JsonRecord>("/api/ayarlar", payload); setSettings(object(result.ayarlar)); setMessage(success); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Ayarlar kaydedilemedi."); }
    finally { setBusy(false); }
  };
  const update = (key: string, value: unknown) => setSettings((current) => ({ ...current, [key]: value }));
  const selectedModel = String(settings.llm_model || models.find((model) => model.ready)?.id || "");
  const tuning = object(settings.llm_model_tuning);
  const selectedTuning = object(tuning[selectedModel]);
  const selectedModelInfo = models.find((model) => model.id === selectedModel);
  const supportsMtp = Boolean(selectedModelInfo?.mtp_dahili || selectedModelInfo?.mtp_dosyasi);
  const supportsVision = Boolean(selectedModelInfo?.gorsel_dosyasi);
  const updateTuning = (key: string, value: unknown) => setSettings((current) => {
    const all = object(current.llm_model_tuning);
    return { ...current, llm_model_tuning: { ...all, [selectedModel]: { ...object(all[selectedModel]), [key]: value } } };
  });
  const availableModels = useMemo(() => models, [models]);

  const saveGeneral = () => save({
    gecmis_limiti: number(settings.gecmis_limiti, 0),
    uretim_varsayilan_steps: number(settings.uretim_varsayilan_steps, 9),
    uretim_varsayilan_seed: number(settings.uretim_varsayilan_seed, -1),
    uretim_varsayilan_max_side: number(settings.uretim_varsayilan_max_side, 1536),
  }, "Genel ve üretim varsayılanları kaydedildi.");
  const saveLlm = () => save({ llm_model: selectedModel, llm_ctx: number(settings.llm_ctx, 8192), llm_model_tuning: { ...tuning, [selectedModel]: selectedTuning } }, "LLM ayarları kaydedildi.");
  const promptOverride = (value: unknown, fallback: unknown) => {
    const text = String(value ?? "").trim();
    return text === String(fallback ?? "").trim() ? "" : text;
  };
  const savePrompts = () => save({
    llm_prompt_destek_yaraticilik: number(settings.llm_prompt_destek_yaraticilik, 0.7),
    llm_prompt_destek_uzunluk: settings.llm_prompt_destek_uzunluk || "orta",
    uretim_prompt_destek: settings.uretim_prompt_destek === true,
    uretim_prompt_ceviri: settings.uretim_prompt_ceviri === true,
    uretim_prompt_duzenle: settings.uretim_prompt_duzenle === true,
    llm_sistem: promptOverride(settings.llm_sistem, defaults.sistem),
    llm_sohbet_sistem: promptOverride(settings.llm_sohbet_sistem, defaults.sohbet),
    llm_mod: Object.fromEntries((["fikir", "gorsel", "birlestir", "ceviri", "sohbet"] as const).map(key => [key, promptOverride(object(settings.llm_mod)[key], object(defaults.mod)[key])])),
    llm_streaming: settings.llm_streaming !== false,
    llm_low_bandwidth: settings.llm_low_bandwidth === true,
  }, "Prompt ve LLM talimatları kaydedildi.");
  const resetPromptOverrides = () => setSettings(current => ({ ...current, llm_sistem: "", llm_sohbet_sistem: "", llm_mod: {} }));

  const changeAccountPassword = async () => {
    setBusy(true); setError(""); setMessage("");
    try { await api.post("/api/sifre", { eski: currentPassword, yeni: newPassword }); setCurrentPassword(""); setNewPassword(""); setMessage("Hesap parolası değiştirildi."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Hesap parolası değiştirilemedi."); }
    finally { setBusy(false); }
  };
  const changeVaultPassword = async () => {
    setBusy(true); setError(""); setMessage("");
    try { await api.post("/api/kasa/parola", { mevcut: vaultCurrent, yeni: vaultNext, tekrar: vaultRepeat }); setVaultCurrent(""); setVaultNext(""); setVaultRepeat(""); setMessage("Kasa parolası değiştirildi."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Kasa parolası değiştirilemedi."); }
    finally { setBusy(false); }
  };
  const saveTimeout = async () => {
    setBusy(true); setError(""); setMessage("");
    try { const result = await api.post<JsonRecord>("/api/kasa/sure", { dakika: vaultMinutes }); setVaultMinutes(number(result.dakika, vaultMinutes)); setMessage("Kasanın otomatik kilit süresi kaydedildi."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Kasa süresi kaydedilemedi."); }
    finally { setBusy(false); }
  };
  const prepareModel = async () => {
    setBusy(true); setError(""); setMessage("");
    try { const result = await api.post<JsonRecord>("/api/llm/hazirla", { llm_model: selectedModel }); if (!result.is_id) throw new Error(String(result.hata || "Model hazırlama işi başlatılamadı.")); setReloadJob(String(result.is_id)); setMessage("Model hazırlanıyor; bu sırada diğer bölümleri kullanabilirsin."); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Model hazırlanamadı."); }
    finally { setBusy(false); }
  };
  const cancelPrepare = async () => { if (!reloadJob) return; try { await api.post("/api/is/iptal", { is_id: reloadJob }); setMessage("Model hazırlama durdurma isteği gönderildi."); } catch (reason) { setError(reason instanceof Error ? reason.message : "İş durdurulamadı."); } };

  const tabs: Array<[Tab, string, typeof SlidersHorizontal]> = [["general", "Genel", SlidersHorizontal], ["llm", "LLM", LoaderCircle], ["prompts", "Promptlar", Check], ["security", "Güvenlik", Shield]];
  if (loading) return <section className="feature-page"><div className="feature-state"><LoaderCircle className="spin"/> Ayarlar yükleniyor…</div></section>;
  return <section className="feature-page settings-workspace" aria-labelledby="settings-title">
    <header className="feature-heading"><div><p className="eyebrow">KİŞİSEL TERCİHLER</p><h1 id="settings-title">Ayarlar</h1><p>{userName} · Ayarlar bu kullanıcının şifreli kasasında saklanır.</p></div><button className="button" type="button" onClick={() => void load()}><RefreshCw size={15}/> Yenile</button></header>
    <nav className="settings-tabs" aria-label="Ayar kategorileri">{tabs.map(([id, label, Icon]) => <button type="button" key={id} aria-pressed={tab === id} onClick={() => { setTab(id); setMessage(""); setError(""); }}><Icon size={16}/>{label}</button>)}</nav>
    {error && <div className="feature-alert" role="alert">{error}</div>}{message && <p className="settings-notice" role="status">{message}</p>}
    {tab === "general" && <div className="settings-grid">
      <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">KAYITLAR</p><h2>Geçmiş ve varsayılan üretim</h2></div></div>
        <label className="settings-field">Geçmiş kayıt sınırı <small>0 değeri sınırsızdır.</small><input type="number" min={0} value={number(settings.gecmis_limiti, 0)} onChange={(e) => update("gecmis_limiti", Number(e.target.value))}/></label>
        <div className="settings-fields-three"><label className="settings-field">Varsayılan adım<input type="number" min={1} max={50} value={number(settings.uretim_varsayilan_steps, 9)} onChange={(e) => update("uretim_varsayilan_steps", Number(e.target.value))}/></label><label className="settings-field">Seed<input type="number" value={number(settings.uretim_varsayilan_seed, -1)} onChange={(e) => update("uretim_varsayilan_seed", Number(e.target.value))}/></label><label className="settings-field">Maksimum kenar<input type="number" min={256} max={4096} step={16} value={number(settings.uretim_varsayilan_max_side, 1536)} onChange={(e) => update("uretim_varsayilan_max_side", Number(e.target.value))}/></label></div>
        <button className="button button--primary" onClick={() => void saveGeneral()} disabled={busy}><Save size={15}/> Kaydet</button>
      </section>
      <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">KASA OTURUMU</p><h2>Otomatik kilit</h2></div></div><p className="muted">Kasa parolası sorulmadan açık kalacağı süre.</p><div className="settings-inline"><input type="number" min={1} max={1440} value={vaultMinutes} onChange={(e) => setVaultMinutes(Number(e.target.value))}/><span>dakika</span><button className="button" onClick={() => void saveTimeout()} disabled={busy}>Süreyi kaydet</button></div></section>
    </div>}
    {tab === "llm" && <div className="settings-grid settings-grid--wide">
      <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">MODEL ÇALIŞMA AYARLARI</p><h2>LLM modeli ve performans</h2></div></div>
        <label className="settings-field">Varsayılan LLM<select value={selectedModel} onChange={(e) => update("llm_model", e.target.value)}><option value="">Otomatik</option>{availableModels.map((model) => <option key={model.id} value={model.id}>{model.ad}{model.hazir || model.ready ? " · hazır" : " · kurulu değil"}</option>)}</select></label>
        <p className="muted">Model dosyaları ve kayıtları Kurucu bölümünden yönetilir. Seçenekler modele özel olarak saklanır.</p>
        <div className="settings-field settings-global-context"><label htmlFor="llm-context-fallback">Genel bağlam sınırı</label><small>Modele özel değer belirlenmemişse bu sınır kullanılır.</small><div className="settings-inline"><input id="llm-context-fallback" type="number" min={1024} max={32768} step={128} value={number(settings.llm_ctx, 8192)} onChange={(e) => update("llm_ctx", Number(e.target.value))}/><span>token</span></div></div>
        <div className="settings-fields-three">
          <label className="settings-field">GPU katmanı<select value={String(selectedTuning.gpu_layers ?? "all")} onChange={(e) => updateTuning("gpu_layers", e.target.value === "all" || e.target.value === "auto" ? e.target.value : Number(e.target.value))}><option value="all">Tüm katmanlar</option><option value="auto">Otomatik</option>{[0,8,16,24,32,48,64,80,96,128,256].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
          <label className="settings-field">Modele özel bağlam<input type="number" min={1024} max={32768} step={128} value={number(selectedTuning.context ?? selectedModelInfo?.tuning_defaults?.context ?? settings.llm_ctx, 8192)} onChange={(e) => updateTuning("context", Number(e.target.value))}/><button type="button" className="settings-reset" onClick={() => updateTuning("context", undefined)}>Genel sınırı kullan</button></label>
          <label className="settings-field">CPU iş parçacığı<input type="number" min={1} max={64} value={number(selectedTuning.threads ?? settings.llm_threads, 8)} onChange={(e) => updateTuning("threads", Number(e.target.value))}/></label>
          <label className="settings-field">Batch<input type="number" min={128} max={8192} step={128} value={number(selectedTuning.batch ?? settings.llm_batch, 512)} onChange={(e) => updateTuning("batch", Number(e.target.value))}/></label>
          <label className="settings-field">Micro batch<input type="number" min={128} max={8192} step={128} value={number(selectedTuning.ubatch, 128)} onChange={(e) => updateTuning("ubatch", Number(e.target.value))}/></label>
          <label className="settings-field">Sıcaklık<input type="number" min={0.1} max={2} step={0.05} value={number(selectedTuning.temperature ?? settings.llm_temperature, 0.7)} onChange={(e) => updateTuning("temperature", Number(e.target.value))}/></label>
          <label className="settings-field">Top-p<input type="number" min={0.05} max={1} step={0.05} value={number(selectedTuning.top_p ?? settings.llm_top_p, 0.9)} onChange={(e) => updateTuning("top_p", Number(e.target.value))}/></label>
          <label className="settings-field">Top-k<input type="number" min={0} max={1000} value={number(selectedTuning.top_k ?? settings.llm_top_k, 40)} onChange={(e) => updateTuning("top_k", Number(e.target.value))}/></label>
          <label className="settings-field">Tekrar cezası<input type="number" min={0.8} max={2} step={0.05} value={number(selectedTuning.repeat_penalty ?? settings.llm_repeat_penalty, 1.1)} onChange={(e) => updateTuning("repeat_penalty", Number(e.target.value))}/></label>
          <label className="settings-field">Flash Attention<select value={String(selectedTuning.flash_attn ?? selectedModelInfo?.tuning_defaults?.flash_attn ?? "on")} onChange={(e) => updateTuning("flash_attn", e.target.value)}><option value="on">Açık</option><option value="auto">Otomatik</option><option value="off">Kapalı</option></select></label>
          <label className="settings-field">K cache türü<select value={String(selectedTuning.cache_type_k ?? "f16")} onChange={(e) => updateTuning("cache_type_k", e.target.value)}>{["f32","f16","bf16","q8_0","q4_0","q4_1","iq4_nl","q5_0","q5_1"].map(value=><option key={value} value={value}>{value}</option>)}</select></label>
          <label className="settings-field">V cache türü<select value={String(selectedTuning.cache_type_v ?? "f16")} onChange={(e) => updateTuning("cache_type_v", e.target.value)}>{["f32","f16","bf16","q8_0","q4_0","q4_1","iq4_nl","q5_0","q5_1"].map(value=><option key={value} value={value}>{value}</option>)}</select></label>
          <label className="settings-field">Düşünme modu<select value={String(selectedTuning.reasoning ?? "on")} onChange={(e) => updateTuning("reasoning", e.target.value)}><option value="auto">Model seçsin</option><option value="on">Açık</option><option value="off">Kapalı</option></select></label>
          <label className="settings-field">Düşünme düzeyi<select value={String(selectedTuning.reasoning_effort ?? "default")} onChange={(e) => updateTuning("reasoning_effort", e.target.value)}>{[["default","Varsayılan"],["minimal","En az"],["low","Düşük · hızlı"],["medium","Orta"],["high","Yüksek"],["xhigh","Çok yüksek"],["max","Maksimum"]].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
          <label className="settings-field">Düşünme token sınırı<input type="number" min={-1} max={32768} step={32} value={number(selectedTuning.reasoning_budget, 160)} onChange={(e) => updateTuning("reasoning_budget", Number(e.target.value))}/><small>-1: sınırsız</small></label>
          <label className="settings-field">MTP taslak tokeni<input type="number" min={1} max={16} value={number(selectedTuning.mtp_draft_tokens, 3)} disabled={!supportsMtp} onChange={(e) => updateTuning("mtp_draft_tokens", Number(e.target.value))}/><small>{supportsMtp ? "Modelin MTP dosyası kullanılır." : "Bu model için MTP dosyası tanımlı değil."}</small></label>
        </div>
        <div className="settings-checks"><label><input type="checkbox" checked={selectedTuning.use_mtp ?? settings.llm_use_mtp ?? false} disabled={!supportsMtp} onChange={(e) => updateTuning("use_mtp", e.target.checked)}/> MTP hızlandırması</label><label><input type="checkbox" checked={selectedTuning.multimodal ?? settings.llm_multimodal ?? true} disabled={!supportsVision} onChange={(e) => updateTuning("multimodal", e.target.checked)}/> Görsel anlama</label></div>
        <div className="settings-actions"><button className="button button--primary" onClick={() => void saveLlm()} disabled={busy}><Save size={15}/> Ayarları kaydet</button><button className="button" onClick={() => void prepareModel()} disabled={busy || !selectedModel}><KeyRound size={15}/> Modeli yeniden yükle</button>{reloadJob && <button className="button button--danger" onClick={() => void cancelPrepare()}><LoaderCircle size={15}/> Hazırlamayı durdur</button>}</div>
      </section>
    </div>}
    {tab === "prompts" && <div className="settings-grid settings-grid--wide">
      <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">PROMPT DESTEĞİ</p><h2>Üretim ve sohbet tercihleri</h2></div></div>
        <div className="settings-checks settings-checks--stack"><label><input type="checkbox" checked={settings.uretim_prompt_destek === true} onChange={(e) => update("uretim_prompt_destek", e.target.checked)}/> Üretimden önce Prompt Stüdyosu ile iyileştir</label><label><input type="checkbox" checked={settings.uretim_prompt_duzenle === true} onChange={(e) => update("uretim_prompt_duzenle", e.target.checked)}/> Prompt Stüdyosu mevcut promptu düzenlesin</label><label><input type="checkbox" checked={settings.uretim_prompt_ceviri === true} onChange={(e) => update("uretim_prompt_ceviri", e.target.checked)}/> Türkçe promptu görsel üretimi için İngilizceye çevir</label><label><input type="checkbox" checked={settings.llm_streaming !== false} onChange={(e) => update("llm_streaming", e.target.checked)}/> LLM canlı yanıt aktarımı</label><label><input type="checkbox" checked={settings.llm_low_bandwidth === true} onChange={(e) => update("llm_low_bandwidth", e.target.checked)}/> Düşük bant genişliği modu</label></div>
        <div className="settings-fields-three"><label className="settings-field">Prompt yaratıcılığı<input type="number" min={0.2} max={1.2} step={0.1} value={number(settings.llm_prompt_destek_yaraticilik, 0.7)} onChange={(e) => update("llm_prompt_destek_yaraticilik", Number(e.target.value))}/></label><label className="settings-field">Yanıt uzunluğu<select value={String(settings.llm_prompt_destek_uzunluk || "orta")} onChange={(e) => update("llm_prompt_destek_uzunluk", e.target.value)}><option value="kisa">Kısa</option><option value="orta">Orta</option><option value="uzun">Uzun</option></select></label></div>
        <div className="settings-fields-two"><div className="settings-prompt-override"><label className="settings-field">Prompt üretim sistem promptu<textarea rows={7} value={String(settings.llm_sistem || defaults.sistem || "")} onChange={(e) => update("llm_sistem", e.target.value)}/></label><button type="button" className="settings-reset" onClick={() => update("llm_sistem", "")}>Varsayılanı kullan</button></div><div className="settings-prompt-override"><label className="settings-field">Sohbet sistem promptu<textarea rows={7} value={String(settings.llm_sohbet_sistem || defaults.sohbet || "")} onChange={(e) => update("llm_sohbet_sistem", e.target.value)}/></label><button type="button" className="settings-reset" onClick={() => update("llm_sohbet_sistem", "")}>Varsayılanı kullan</button></div></div>
        <details className="settings-details"><summary>Mod bazlı talimatlar</summary><div className="settings-fields-two">{([ ["fikir","Fikir"], ["gorsel","Görsel"], ["birlestir","Birleştirme"], ["ceviri","Çeviri"], ["sohbet","Sohbet"] ] as Array<[string,string]>).map(([key,label]) => <div className="settings-prompt-override" key={key}><label className="settings-field">{label}<textarea rows={4} value={String(object(settings.llm_mod)[key] || object(defaults.mod)[key] || "")} onChange={(e) => update("llm_mod", { ...object(settings.llm_mod), [key]: e.target.value })}/></label><button type="button" className="settings-reset" onClick={() => update("llm_mod", { ...object(settings.llm_mod), [key]: "" })}>Varsayılanı kullan</button></div>)}</div></details>
        <button type="button" className="settings-reset" onClick={resetPromptOverrides}>Tüm sistem promptlarını varsayılanlara döndür</button>
        <button className="button button--primary" onClick={() => void savePrompts()} disabled={busy}><Save size={15}/> Tercihleri kaydet</button>
      </section>
    </div>}
    {tab === "security" && <div className="settings-grid">
      <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">HESAP</p><h2>Hesap parolası</h2></div></div><p className="muted">Giriş parolası kasa parolasından ayrıdır.</p><label className="settings-field">Mevcut hesap parolası<input type="password" autoComplete="current-password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)}/></label><label className="settings-field">Yeni hesap parolası<input type="password" autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)}/></label><button className="button" onClick={() => void changeAccountPassword()} disabled={busy || !currentPassword || !newPassword}>Hesap parolasını değiştir</button></section>
      <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">ŞİFRELİ KASA</p><h2>Kasa parolası</h2></div></div><p className="muted">Değişiklik sırasında kasa verileri yeni parolayla yeniden şifrelenir.</p><label className="settings-field">Mevcut kasa parolası<input type="password" autoComplete="current-password" value={vaultCurrent} onChange={(e) => setVaultCurrent(e.target.value)}/></label><label className="settings-field">Yeni kasa parolası<input type="password" autoComplete="new-password" value={vaultNext} onChange={(e) => setVaultNext(e.target.value)}/></label><label className="settings-field">Yeni parolayı doğrula<input type="password" autoComplete="new-password" value={vaultRepeat} onChange={(e) => setVaultRepeat(e.target.value)}/></label><button className="button" onClick={() => void changeVaultPassword()} disabled={busy || !vaultCurrent || !vaultNext || !vaultRepeat}>Kasa parolasını değiştir</button></section>
    </div>}
    {message && <p className="settings-notice settings-notice--bottom" role="status"><Check size={15}/>{message}</p>}
  </section>;
}

export function AdminWorkspace({ api }: { api: ProductionApi }) {
  type UserRow = { kullanici: string; rol: string; kota_mb: number; kota_kayit: number; kullanim_mb: number; kayit_sayisi: number };
  const [users, setUsers] = useState<UserRow[]>([]), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [name, setName] = useState(""), [password, setPassword] = useState(""), [role, setRole] = useState("uye"), [quotaMb, setQuotaMb] = useState(0), [quotaCount, setQuotaCount] = useState(0);
  const load = useCallback(async () => { setLoading(true); setError(""); try { const data = await api.get<JsonRecord>("/api/kullanicilar"); setUsers(Array.isArray(data.kullanicilar) ? data.kullanicilar as UserRow[] : []); } catch (reason) { setError(reason instanceof Error ? reason.message : "Kullanıcı listesi alınamadı."); } finally { setLoading(false); } }, [api]);
  useEffect(() => { void load(); }, [load]);
  const run = async (action: () => Promise<unknown>, ok: string) => { setBusy(true); setError(""); setNotice(""); try { await action(); setNotice(ok); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "İşlem başarısız."); } finally { setBusy(false); } };
  const create = (event: React.FormEvent) => { event.preventDefault(); void run(() => api.post("/api/kullanici/ekle", { kullanici: name.trim(), sifre: password, rol: role, kota_mb: quotaMb, kota_kayit: quotaCount }), `“${name.trim()}” kullanıcısı eklendi.`).then(() => { setName(""); setPassword(""); }); };
  return <section className="feature-page" aria-labelledby="admin-title"><header className="feature-heading"><div><p className="eyebrow">YÖNETİM</p><h1 id="admin-title">Kullanıcılar</h1><p>Hesap adları, roller ve kota bilgileri.</p></div><button className="button" onClick={() => void load()}><RefreshCw size={15}/> Yenile</button></header>
    {error && <div className="feature-alert" role="alert">{error}</div>}{notice && <p className="settings-notice" role="status">{notice}</p>}
    <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">HESAPLAR</p><h2>{users.length} kullanıcı</h2></div></div>
      {loading ? <p className="muted" role="status">Kullanıcılar yükleniyor…</p> : error ? <p className="muted">Liste alınamadı. Yeniden deneyebilirsin.</p> : !users.length ? <p className="muted">Kullanıcı kaydı bulunamadı.</p> : <div className="admin-user-list">{users.map((user) => <AdminUser key={user.kullanici} user={user} busy={busy} onSave={(mb, count) => void run(() => api.post("/api/kullanici/kota", { kullanici: user.kullanici, kota_mb: mb, kota_kayit: count }), `“${user.kullanici}” kotası güncellendi.`)} onDelete={() => { if (window.confirm(`${user.kullanici} hesabı ve bu hesaba ait veriler silinsin mi?`)) void run(() => api.post("/api/kullanici/sil", { kullanici: user.kullanici }), `“${user.kullanici}” hesabı silindi.`); }}/>)}</div>}
    </section>
    <form className="feature-panel admin-create" onSubmit={create}><div className="feature-panel__heading"><div><p className="eyebrow">YENİ HESAP</p><h2>Kullanıcı ekle</h2></div></div><div className="settings-fields-three"><label className="settings-field">Kullanıcı adı<input value={name} onChange={(e) => setName(e.target.value)} required autoComplete="off"/></label><label className="settings-field">İlk parola<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="new-password"/></label><label className="settings-field">Rol<select value={role} onChange={(e) => setRole(e.target.value)}><option value="uye">Üye</option><option value="admin">Admin</option></select></label><label className="settings-field">Kota · MB<input type="number" min={0} value={quotaMb} onChange={(e) => setQuotaMb(Number(e.target.value))}/></label><label className="settings-field">Kota · kayıt<input type="number" min={0} value={quotaCount} onChange={(e) => setQuotaCount(Number(e.target.value))}/></label></div><p className="muted">0 kota değeri sınırsız alan veya kayıt anlamına gelir.</p><button className="button button--primary" disabled={busy}>Hesap oluştur</button></form>
  </section>;
}

function AdminUser({ user, busy, onSave, onDelete }: { user: { kullanici: string; rol: string; kota_mb: number; kota_kayit: number; kullanim_mb: number; kayit_sayisi: number }; busy: boolean; onSave: (mb: number, count: number) => void; onDelete: () => void }) {
  const [mb, setMb] = useState(user.kota_mb), [count, setCount] = useState(user.kota_kayit);
  return <article className="admin-user-row"><div className="admin-user-identity"><b>{user.kullanici}</b><span>{user.rol} · kullanım {number(user.kullanim_mb, 0)} MB / {user.kayit_sayisi} kayıt</span></div><label>Kota MB<input type="number" min={0} value={mb} onChange={(e) => setMb(Number(e.target.value))}/></label><label>Kota kayıt<input type="number" min={0} value={count} onChange={(e) => setCount(Number(e.target.value))}/></label><button className="button" disabled={busy} onClick={() => onSave(mb, count)}>Kaydet</button><button className="button button--danger" disabled={busy} onClick={onDelete}>Sil</button></article>;
}
