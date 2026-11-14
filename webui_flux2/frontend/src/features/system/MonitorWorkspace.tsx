import { useCallback, useEffect, useState } from "react";
import { Activity, CircleAlert, Cpu, HardDrive, LoaderCircle, RefreshCw, Zap } from "lucide-react";
import type { JsonRecord } from "../../lib/api/client";
import "./SystemWorkspace.css";

interface Api { get<T = JsonRecord>(path: string, signal?: AbortSignal): Promise<T>; }
interface SystemSnapshot extends JsonRecord {
  ram?: { toplam_mb?: number | null; bos_mb?: number | null; yuzde?: number | null };
  comfy_vram?: Array<{ad?:string;bos_mb?:number;toplam_mb?:number}>;
  llm?: { canli?:boolean;model?:string;calisan_model?:string;ngl?:number;ctx?:number;calisan_ctx?:number };
  comfy_acik?:boolean; kuyruk_bekleyen?:number; vram_bos_mb?:number | null;
}

function mb(value?: number | null) { return value == null || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) / 1024).toFixed(1)} GB`; }

export function MonitorWorkspace({ api, isAdmin }: { api: Api; isAdmin: boolean }) {
  const [snapshot, setSnapshot] = useState<SystemSnapshot | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [source, setSource] = useState("webui");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try { const data = await api.get<SystemSnapshot>("/api/sistem", signal); if (!signal?.aborted) { setSnapshot(data); setError(""); } }
    catch (value) { if (!signal?.aborted) setError(value instanceof Error ? value.message : "Sistem durumu alınamadı."); }
    finally { if (!signal?.aborted) setLoading(false); }
  }, [api]);
  const loadLogs = useCallback(async (signal?: AbortSignal) => {
    if (!isAdmin) { setLogs([]); return; }
    try { const data = await api.get<{satirlar?:string[]}>(`/api/log?kaynak=${encodeURIComponent(source)}&satir=200`, signal); if (!signal?.aborted) setLogs(Array.isArray(data.satirlar) ? data.satirlar : []); }
    catch (value) { if (!signal?.aborted) setLogs([value instanceof Error ? value.message : "Loglar alınamadı."]); }
  }, [api, isAdmin, source]);
  useEffect(() => { const c = new AbortController(); void refresh(c.signal); const t = window.setInterval(() => void refresh(), 5000); return () => { c.abort(); window.clearInterval(t); }; }, [refresh]);
  useEffect(() => { const c = new AbortController(); void loadLogs(c.signal); return () => c.abort(); }, [loadLogs]);

  const ram = snapshot?.ram;
  const llm = snapshot?.llm;
  const ramPct = Number(ram?.yuzde);
  return <section className="feature-page monitor-workspace" aria-labelledby="monitor-title">
    <header className="feature-heading"><div><p className="eyebrow">CANLI DURUM</p><h1 id="monitor-title">Sistem izleme</h1><p>RAM, VRAM, ComfyUI ve LLM durumunu anlık takip et.</p></div>
      <button className="button" type="button" onClick={() => { void refresh(); void loadLogs(); }} disabled={loading}><RefreshCw size={15}/> Yenile</button></header>
    {error && <div className="feature-alert" role="alert"><CircleAlert size={16}/>{error}</div>}
    {loading && !snapshot ? <div className="feature-state"><LoaderCircle className="spin"/> Sistem bilgileri yükleniyor…</div> : <>
      <div className="monitor-grid">
        <article className="monitor-card"><span className="monitor-card__icon"><HardDrive size={18}/></span><span className="monitor-card__label">Bellek · RAM</span>
          <strong>{ramPct >= 0 ? `${ramPct.toFixed(0)}%` : "—"}</strong><small>{mb(ram?.bos_mb)} boş / {mb(ram?.toplam_mb)} toplam</small>
          {Number.isFinite(ramPct) && <div className="monitor-meter"><span className={ramPct >= 90 ? "is-warning" : ""} style={{width:`${Math.max(0,Math.min(100,ramPct))}%`}}/></div>}</article>
        <article className="monitor-card"><span className="monitor-card__icon"><Cpu size={18}/></span><span className="monitor-card__label">ComfyUI</span>
          <strong>{snapshot?.comfy_acik ? "Bağlı" : "Kapalı"}</strong><small>{(snapshot?.comfy_vram || []).length ? `${snapshot?.comfy_vram?.length} GPU görünür` : "GPU belleği okunamadı"}</small></article>
        <article className="monitor-card"><span className="monitor-card__icon"><Zap size={18}/></span><span className="monitor-card__label">LLM modeli</span>
          <strong>{llm?.canli ? "Çalışıyor" : "Hazır değil"}</strong><small>{llm?.calisan_model || llm?.model || "Seçili model yok"}</small></article>
        <article className="monitor-card"><span className="monitor-card__icon"><Activity size={18}/></span><span className="monitor-card__label">Bekleyen işler</span>
          <strong>{String(snapshot?.kuyruk_bekleyen ?? 0)}</strong><small>{snapshot?.aktif_is_var ? "Bir görsel/video işi çalışıyor" : "Görsel/video işi çalışmıyor"}</small></article>
      </div>
      {(snapshot?.comfy_vram || []).length > 0 && <section className="feature-panel"><div className="feature-panel__heading"><div><p className="eyebrow">GRAFİK BELLEĞİ</p><h2>GPU kullanımı</h2></div></div>
        <div className="gpu-list">{snapshot?.comfy_vram?.map((gpu, index) => { const total = Number(gpu.toplam_mb || 0), free = Number(gpu.bos_mb || 0), used = Math.max(0,total-free), percent = total ? used/total*100 : 0; return <div className="gpu-row" key={`${gpu.ad}-${index}`}><div><b>{gpu.ad || `GPU ${index+1}`}</b><span>{mb(free)} boş / {mb(total)} toplam</span></div><div className="monitor-meter"><span style={{width:`${percent}%`}}/></div></div>; })}</div>
      </section>}
    </>}
    {isAdmin ? <section className="feature-panel log-panel"><div className="feature-panel__heading"><div><p className="eyebrow">TANI</p><h2>Sistem kayıtları</h2></div>
      <label className="compact-field">Kaynak<select value={source} onChange={(e)=>setSource(e.target.value)}><option value="webui">WebUI</option><option value="comfy">ComfyUI</option><option value="llama">LLM</option><option value="setup">Kurucu</option></select></label></div>
      <pre className="log-output" aria-live="polite">{logs.length ? logs.join("\n") : "Bu kaynakta henüz log yok."}</pre></section> : <section className="feature-panel log-panel"><h2>Sistem kayıtları</h2><p className="muted">Ayrıntılı servis kayıtları yönetici hesabına açıktır. Bellek ve model durumu bu ekranda görünür.</p></section>}
  </section>;
}
