import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, ArrowUpRight, CircleAlert, Layers3, RefreshCw, Server } from "lucide-react";
import { Button } from "../components/ui/Button";
import { StatusBadge, type StatusTone } from "../components/ui/StatusBadge";
import { getWebUiPort, statusApi, type HealthStatus, type StatusApi, type WebUiPort } from "../lib/api/client";
import { ProductionWorkspace } from "../features/production/ProductionWorkspace";
import { productionApi, type ProductionApi } from "../lib/api/client";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; status: HealthStatus };

interface AppProps {
  api?: StatusApi;
  port?: WebUiPort;
  pollMs?: number;
  production?: ProductionApi;
}

function surfaceUrl(target: WebUiPort): string {
  const { protocol, hostname, port } = window.location;
  if (port !== "8189" && port !== "8192") {
    const query = new URLSearchParams(window.location.search);
    query.set("surface", target === 8192 ? "setup" : "main");
    return `${window.location.pathname}?${query.toString()}`;
  }
  const runpodHost = hostname.match(/^(.*)-\d+\.proxy\.runpod\.net$/i);
  if (runpodHost) return `${protocol}//${runpodHost[1]}-${target}.proxy.runpod.net/?ui=next`;
  return `${protocol}//${hostname}:${target}/?ui=next`;
}

function legacyUrl(): string {
  return `${window.location.origin}/`;
}

function statusTone(state: LoadState): StatusTone {
  if (state.kind === "loading") return "loading";
  if (state.kind === "error") return "error";
  if (!state.status.hazir) return "loading";
  return "ready";
}

function statusLabel(state: LoadState): string {
  if (state.kind === "loading") return "Bağlanıyor";
  if (state.kind === "error") return "Bağlantı sorunu";
  return state.status.hazir ? "Hazır" : "Hazırlanıyor";
}

export function App({ api = statusApi, production = productionApi, port = getWebUiPort(), pollMs = 15_000 }: AppProps) {
  if (port === 8189) return <ProductionWorkspace api={production} />;
  return <StatusApp api={api} port={port} pollMs={pollMs} />;
}

function StatusApp({ api, port, pollMs }: Required<Pick<AppProps, "api" | "port" | "pollMs">>) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const label = port === 8192 ? "Kurucu" : "WebUI";
  const status = state.kind === "ready" ? state.status : undefined;
  const storageMode = status && "storage_mode" in status ? status.storage_mode : undefined;
  const storageIsPersistent = status && "storage_persistent" in status ? status.storage_persistent : undefined;

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setState((current) => current.kind === "ready" ? current : { kind: "loading" });
    try {
      const result = await api.health(port, signal);
      if (!signal?.aborted) setState({ kind: "ready", status: result });
    } catch (error) {
      if (!signal?.aborted) {
        setState({ kind: "error", message: error instanceof Error ? error.message : "Durum alınamadı." });
      }
    }
  }, [api, port]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(), pollMs);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refresh, pollMs, attempt]);

  const navItems = useMemo(() => [
    { port: 8189 as const, title: "Ana WebUI", icon: Server },
    { port: 8192 as const, title: "Kurucu", icon: Layers3 },
  ], []);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">İçeriğe geç</a>
      <aside className="sidebar" aria-label="Ana menü">
        <a className="brand" href={surfaceUrl(port)} aria-label="ComfyUI WebUI ana sayfa">
          <span className="brand__mark" aria-hidden="true"><Activity size={19} /></span>
          <span><strong>ComfyUI</strong><small>RunPod WebUI</small></span>
        </a>
        <div className="nav-caption">ÇALIŞMA ALANI</div>
        <nav className="surface-nav" aria-label="Uygulama bölümleri">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = port === item.port;
            return (
              <a
                key={item.port}
                className={`nav-link${active ? " nav-link--active" : ""}`}
                href={surfaceUrl(item.port)}
                aria-current={active ? "page" : undefined}
              >
                <Icon size={17} aria-hidden="true" />
                <span>{item.title}</span>
                {active && <span className="nav-link__port">:{item.port}</span>}
              </a>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="sidebar-footer__label">Bağlı yüzey</span>
          <span className="sidebar-footer__value">{label} · {port}</span>
        </div>
      </aside>

      <main id="main-content" className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>RunPod</span><span aria-hidden="true">/</span><strong>{label}</strong></div>
          <div className="topbar__right"><span className="topbar__environment"><span /> Pod durumu</span></div>
        </header>

        <div className="content-wrap">
          <section className="page-heading" aria-labelledby="page-title">
            <div>
              <p className="eyebrow">SİSTEM DURUMU</p>
              <h1 id="page-title">{label}</h1>
              <p className="page-heading__copy">Servis bağlantısını ve başlangıç durumunu buradan kontrol et.</p>
            </div>
            <Button onClick={() => setAttempt((value) => value + 1)}
              aria-label="Durumu yenile" disabled={state.kind === "loading"}>
              <RefreshCw size={16} aria-hidden="true" /> Yenile
            </Button>
          </section>

          <section className="status-panel" aria-labelledby="status-title">
            <div className="status-panel__top">
              <div className="service-icon" aria-hidden="true"><Server size={20} /></div>
              <div className="status-panel__identity">
                <div className="status-panel__label">AKTİF SERVİS</div>
                <h2 id="status-title">{port === 8192 ? "Bağımsız kurucu" : "Ana WebUI"}</h2>
                <p>HTTP bağlantısı · port {port}</p>
              </div>
              <StatusBadge tone={statusTone(state)}>{statusLabel(state)}</StatusBadge>
            </div>

            <div className="status-panel__body" aria-live="polite" aria-atomic="true">
              {state.kind === "loading" && (
                <div className="state-row"><span className="spinner" aria-hidden="true" />
                  <span>Sunucudan durum alınıyor…</span></div>
              )}
              {state.kind === "error" && (
                <div className="state-message state-message--error" role="alert">
                  <CircleAlert size={18} aria-hidden="true" />
                  <div><strong>Sunucuya ulaşılamadı</strong><p>{state.message}</p></div>
                  <Button variant="ghost" onClick={() => setAttempt((value) => value + 1)}>Tekrar dene</Button>
                </div>
              )}
              {state.kind === "ready" && !state.status.hazir && (
                <div className="state-message state-message--pending" role="status">
                  <span className="spinner" aria-hidden="true" />
                  <div><strong>Sistem hazırlanıyor</strong><p>Hazır olduğunda bu ekran kendini günceller.</p></div>
                </div>
              )}
              {state.kind === "ready" && state.status.hazir && (
                <div className="state-message state-message--ready" role="status">
                  <span className="ready-check" aria-hidden="true">✓</span>
                  <div><strong>Bağlantı kuruldu</strong><p>{label} çalışıyor ve durum bilgisi güncel.</p></div>
                  {port === 8192 && storageMode && (
                    <span className="storage-chip">Depolama: {storageMode}{storageIsPersistent === false ? " · geçici" : ""}</span>
                  )}
                </div>
              )}
            </div>

            <div className="status-panel__foot">
              <span><span className="pulse-dot" aria-hidden="true" /> Otomatik yenileme açık</span>
              <span>15 saniyede bir</span>
            </div>
          </section>

          <section className="quick-links" aria-labelledby="quick-links-title">
            <div className="section-heading">
              <div><p className="eyebrow">HIZLI ERİŞİM</p><h2 id="quick-links-title">Uygulama yüzeyleri</h2></div>
              <p>İki servis ayrı portlarda çalışır.</p>
            </div>
            <div className="surface-grid">
              {navItems.map((item) => {
                const Icon = item.icon;
                return (
                  <a className="surface-card" href={surfaceUrl(item.port)} key={item.port}>
                    <span className="surface-card__icon"><Icon size={18} aria-hidden="true" /></span>
                    <span className="surface-card__copy"><strong>{item.title}</strong><small>HTTP · {item.port}</small></span>
                    <ArrowUpRight size={16} className="surface-card__arrow" aria-hidden="true" />
                  </a>
                );
              })}
            </div>
          </section>

          <footer className="page-footer">
            <span>Yeni React arayüzü önizlemesi. Üretim, arşiv ve sohbet mevcut WebUI'de çalışmaya devam ediyor.</span>
            <a href={legacyUrl()}>Mevcut arayüzü aç <ArrowUpRight size={14} aria-hidden="true" /></a>
          </footer>
        </div>
      </main>
    </div>
  );
}
