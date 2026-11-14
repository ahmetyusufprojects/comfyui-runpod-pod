import { useCallback, useEffect, useState } from "react";
import { Ban, CircleAlert, LoaderCircle, RefreshCw, Trash2, Workflow } from "lucide-react";
import type { JsonRecord } from "../../lib/api/client";
import "./SystemWorkspace.css";

interface Api { get<T = JsonRecord>(path: string, signal?: AbortSignal): Promise<T>; post<T = JsonRecord>(path: string, data: JsonRecord): Promise<T>; }
interface Job {
  is_id: string; durum: string; sira?: number; ilerleme?: JsonRecord; hata?: string;
  gecen?: number; prompt?: string; tur?: string; model?: string; is_tipi?: string; seed?: number;
}

const activeStates = new Set(["kuyrukta", "calisiyor", "durduruluyor"]);
const stateLabel: Record<string, string> = { kuyrukta: "Sırada", calisiyor: "Çalışıyor", durduruluyor: "Durduruluyor", bitti: "Tamamlandı", hata: "Hata", durduruldu: "Durduruldu" };

export function QueueWorkspace({ api }: { api: Api }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await api.get<{ isler?: Job[] }>("/api/isler", signal);
      if (!signal?.aborted) { setJobs(Array.isArray(result.isler) ? result.isler : []); setError(""); }
    } catch (value) {
      if (!signal?.aborted) setError(value instanceof Error ? value.message : "İş listesi alınamadı.");
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => { void refresh(); setNow(Date.now()); }, 1500);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [refresh]);

  const cancel = async (id: string) => {
    setBusy(id); setError("");
    try { await api.post("/api/is/iptal", { is_id: id }); await refresh(); }
    catch (value) { setError(value instanceof Error ? value.message : "İş durdurulamadı."); }
    finally { setBusy(null); }
  };

  const clearWaiting = async () => {
    setBusy("clear"); setError("");
    try { await api.post("/api/kuyruk/temizle", {}); await refresh(); }
    catch (value) { setError(value instanceof Error ? value.message : "Bekleyen işler temizlenemedi."); }
    finally { setBusy(null); }
  };

  const active = jobs.filter((job) => activeStates.has(job.durum));
  const waiting = active.filter((job) => job.durum === "kuyrukta");
  return <section className="feature-page queue-workspace" aria-labelledby="queue-title">
    <header className="feature-heading"><div><p className="eyebrow">ARKA PLAN İŞLERİ</p><h1 id="queue-title">İş kuyruğu</h1><p>İşler sayfa değiştirsen de sunucuda sürer.</p></div>
      <div className="feature-heading__actions"><button className="button" type="button" onClick={() => void refresh()} disabled={loading}><RefreshCw size={15}/> Yenile</button>
        <button className="button button--danger" type="button" onClick={() => void clearWaiting()} disabled={!waiting.length || busy === "clear"}><Trash2 size={15}/> Bekleyenleri temizle</button></div></header>
    {error && <div className="feature-alert" role="alert"><CircleAlert size={16}/>{error}</div>}
    <div className="feature-stat-row" aria-label="Kuyruk özeti">
      <div className="feature-stat"><span>Çalışan ve sıradaki</span><b>{active.length}</b></div>
      <div className="feature-stat"><span>Bekleyen</span><b>{waiting.length}</b></div>
      <div className="feature-stat"><span>Son yenileme</span><b>{new Date(now).toLocaleTimeString()}</b></div>
    </div>
    {loading && !jobs.length ? <div className="feature-state"><LoaderCircle className="spin"/> Kuyruk yükleniyor…</div> : !jobs.length ?
      <div className="feature-empty"><Workflow size={25}/><h2>Kuyruk boş</h2><p>Üretim, sohbet veya prompt isteği gönderdiğinde burada görünür.</p></div> :
      <div className="job-list">{jobs.map((job) => {
        const progress = job.ilerleme || {};
        const raw = Number(progress.oran ?? 0);
        const ratio = Math.max(0, Math.min(1, raw > 1 ? raw / 100 : raw));
        const running = activeStates.has(job.durum);
        const duration = Number(job.gecen ?? 0);
        const elapsed = running ? `${Math.max(duration, 0).toFixed(0)} sn` : `${duration.toFixed(0)} sn`;
        return <article className="job-card" key={job.is_id}>
          <div className="job-card__head"><span className={`state-pill state-pill--${job.durum}`}>{stateLabel[job.durum] || job.durum}</span>
            {job.durum === "kuyrukta" && <span className="muted">Sıra {job.sira || "—"}</span>}
            <span className="job-card__elapsed">{elapsed}</span>
            {running && <button className="button button--danger button--small" type="button" onClick={() => void cancel(job.is_id)} disabled={busy === job.is_id}><Ban size={14}/>{busy === job.is_id ? "Durduruluyor…" : "Durdur"}</button>}
          </div>
          <h2>{job.prompt || job.is_tipi || "İş"}</h2>
          <div className="job-card__meta"><span>{job.is_tipi || job.tur || "İş"}</span>{job.model && <span>{job.model}</span>}{job.seed != null && <span>Seed {job.seed}</span>}</div>
          {running && <div className="job-progress-inline" role="status"><div className="job-progress-inline__track"><span style={{ width: `${ratio * 100}%` }}/></div><span>{progress.calisan ? String(progress.calisan) : `${Math.round(ratio * 100)}%`}</span></div>}
          {job.hata && <p className="job-card__error" role="alert">{job.hata}</p>}
        </article>;
      })}</div>}
  </section>;
}
