import { useCallback, useEffect, useState } from "react";
import { InstallerWorkspace } from "../features/installer/InstallerWorkspace";
import { ChatWorkspace } from "../features/chat/ChatWorkspace";
import { GalleryWorkspace, type GalleryReference, type GalleryTab } from "../features/gallery/GalleryWorkspace";
import { PromptStudio, type PromptResult } from "../features/prompts/PromptStudio";
import { MonitorWorkspace } from "../features/system/MonitorWorkspace";
import { QueueWorkspace } from "../features/system/QueueWorkspace";
import { ProductionWorkspace } from "../features/production/ProductionWorkspace";
import { AdminWorkspace, SettingsWorkspace } from "../features/settings/SettingsWorkspace";
import { productionApi, type JsonRecord, type ProductionApi, getWebUiPort, type WebUiPort } from "../lib/api/client";
import { WorkspaceShell, type WorkspaceSection } from "./WorkspaceShell";

type SessionPhase = "checking" | "setup" | "login" | "vault" | "ready" | "error";
interface AppProps { api?: ProductionApi; port?: WebUiPort; }
interface SessionUser { name: string; role: string; }

function initialSection(): WorkspaceSection {
  const value = new URLSearchParams(window.location.search).get("section");
  const allowed: WorkspaceSection[] = ["production", "studio", "queue", "library", "archive", "prompts", "chat", "monitor", "settings", "admin"];
  return allowed.includes(value as WorkspaceSection) ? value as WorkspaceSection : "production";
}

export function App({ api = productionApi, port = getWebUiPort() }: AppProps) {
  return port === 8192 ? <InstallerWorkspace api={api}/> : <MainWorkspace api={api}/>;
}

function MainWorkspace({ api }: { api: ProductionApi }) {
  const [phase, setPhase] = useState<SessionPhase>("checking");
  const [user, setUser] = useState<SessionUser | null>(null);
  const [vaultMissing, setVaultMissing] = useState(false);
  const [vaultRecover, setVaultRecover] = useState(false);
  const [error, setError] = useState("");
  const [section, setSection] = useState<WorkspaceSection>(initialSection);
  const [visited, setVisited] = useState<Set<WorkspaceSection>>(() => new Set(["production", initialSection()]));
  const [studioInput, setStudioInput] = useState("");
  const [studioNegative, setStudioNegative] = useState("");
  const [studioEditMode, setStudioEditMode] = useState(false);
  const [studioPreset, setStudioPreset] = useState<JsonRecord>({});
  const [productionPreset, setProductionPreset] = useState<JsonRecord>({});
  const [studioSession, setStudioSession] = useState(0);
  const [referenceFile, setReferenceFile] = useState<File | null>(null);

  const checkVault = useCallback(async (sessionUser: SessionUser) => {
    const vault = await api.vault();
    setUser(sessionUser);
    if (vault.acik) setPhase("ready");
    else {
      setVaultMissing(vault.kasa_var === false);
      setVaultRecover(vault.kurtarma === true);
      setPhase("vault");
    }
  }, [api]);

  const refreshSession = useCallback(async () => {
    setPhase("checking"); setError("");
    try {
      const install = await api.setup();
      if (install.kurulu === false) { setPhase("setup"); return; }
      try {
        const me = await api.me();
        const sessionUser = { name: String(me.kullanici || "Kullanıcı"), role: String(me.rol || "uye") };
        await checkVault(sessionUser);
      } catch (reason) {
        const message = reason instanceof Error ? reason.message.toLocaleLowerCase("tr") : "";
        if (message.includes("giris gerekli") || message.includes("giriş gerekli") || message.includes("401")) setPhase("login");
        else throw reason;
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "WebUI durum bilgisi alınamadı.");
      setPhase("error");
    }
  }, [api, checkVault]);

  const navigate = useCallback((next: WorkspaceSection) => {
    if (next === "admin" && user?.role !== "admin") return;
    setSection(next);
    setVisited((current) => new Set(current).add(next));
    const url = new URL(window.location.href);
    url.searchParams.set("section", next);
    window.history.replaceState({}, "", url);
  }, [user?.role]);

  useEffect(() => { void refreshSession(); }, [refreshSession]);
  useEffect(() => {
    if (phase === "ready" && user?.role !== "admin" && section === "admin") navigate("production");
  }, [phase, user?.role, section, navigate]);

  const submitAuth = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const name = String(form.get("username") || "").trim();
    const password = String(form.get("password") || "");
    const repeat = String(form.get("password-repeat") || "");
    setError("");
    try {
      if (phase === "setup") {
        if (password !== repeat) throw new Error("Parolalar eşleşmiyor.");
        await api.post("/api/kurulum", { kullanici: name, sifre: password });
        const result = await api.post<JsonRecord>("/api/giris", { kullanici: name, sifre: password });
        await checkVault({ name: String(result.kullanici || name), role: String(result.rol || "uye") });
      } else if (phase === "login") {
        const result = await api.post<JsonRecord>("/api/giris", { kullanici: name, sifre: password });
        await checkVault({ name: String(result.kullanici || name), role: String(result.rol || "uye") });
      } else if (phase === "vault") {
        if (vaultRecover) await api.post("/api/kasa/kurtar", { parola: password });
        else if (vaultMissing) {
          if (password !== repeat) throw new Error("Kasa parolaları eşleşmiyor.");
          await api.post("/api/kasa/olustur", { parola: password, parola2: repeat, riski_anladim: form.get("risk") === "on" });
        } else await api.post("/api/kasa/ac", { parola: password });
        setPhase("ready");
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "İşlem tamamlanamadı."); }
    finally { formElement.reset(); }
  };

  const lock = async () => {
    setError("");
    try { await api.post("/api/kasa/kapat", {}); await api.post("/api/cikis", {}); setUser(null); setPhase("login"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Kasa kilitlenemedi."); }
  };

  const useReference = async (reference: GalleryReference) => {
    try {
      const response = await fetch(reference.url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error(`Fotoğraf alınamadı (HTTP ${response.status}).`);
      const blob = await response.blob();
      setReferenceFile(new File([blob], reference.name || "referans-gorsel", { type: blob.type || "image/jpeg" }));
      navigate("production");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Referans fotoğraf kullanılamadı."); }
  };

  const applyPrompt = (result: PromptResult) => {
    setStudioInput(String(result.positive || result.positive_tr || ""));
    setStudioNegative(String(result.negative || ""));
    const preset = { ...studioPreset };
    for (const key of ["model", "workflow", "steps", "cfg", "sampler", "width", "height", "max_side", "max_olcek", "video_model_instance", "video_profile", "video_max_side", "suresn", "fps", "image_model", "model_instance", "loras"]) {
      if (result[key] !== undefined && result[key] !== null && result[key] !== "") preset[key] = result[key];
    }
    setProductionPreset(preset);
    navigate("production");
  };

  const openPromptStudio = (prompt: string, editMode: boolean, negative: string, presetDefaults: JsonRecord = {}) => {
    setStudioInput(prompt);
    setStudioNegative(negative);
    setStudioEditMode(editMode);
    setStudioPreset(presetDefaults);
    setStudioSession((value) => value + 1);
    navigate("studio");
  };

  if (phase !== "ready" || !user) return <AuthScreen phase={phase} error={error} vaultMissing={vaultMissing} vaultRecover={vaultRecover} onSubmit={submitAuth} onRetry={() => void refreshSession()}/>;

  return <WorkspaceShell active={section} onNavigate={navigate} userName={user.name} isAdmin={user.role === "admin"} onLock={() => void lock()}>
    {visited.has("production") && <div hidden={section !== "production"}><ProductionWorkspace api={api} embedded initialPrompt={studioInput} initialNegative={studioNegative} initialPreset={productionPreset} referenceFile={referenceFile} onOpenPromptStudio={openPromptStudio}/></div>}
    {visited.has("studio") && <div hidden={section !== "studio"}><PromptStudio key={`studio-${studioSession}`} api={api} initialPrompt={studioInput} initialNegative={studioNegative} editMode={studioEditMode} presetDefaults={studioPreset} onApply={applyPrompt}/></div>}
    {visited.has("chat") && <div hidden={section !== "chat"}><ChatWorkspace api={api} onPromptFromChat={(prompt) => { setStudioInput(prompt); setStudioNegative(""); setStudioEditMode(false); setStudioPreset(productionPreset); setStudioSession((value) => value + 1); navigate("studio"); }}/></div>}
    {visited.has("library") && <div hidden={section !== "library"}><GalleryWorkspace api={api} initialTab={"library" satisfies GalleryTab} onUseReference={useReference}/></div>}
    {visited.has("archive") && <div hidden={section !== "archive"}><GalleryWorkspace api={api} initialTab={"archive" satisfies GalleryTab} onUseReference={useReference}/></div>}
    {visited.has("prompts") && <div hidden={section !== "prompts"}><PromptStudio key="prompt-history" api={api} onApply={applyPrompt}/></div>}
    {visited.has("queue") && <div hidden={section !== "queue"}><QueueWorkspace api={api}/></div>}
    {visited.has("monitor") && <div hidden={section !== "monitor"}><MonitorWorkspace api={api} isAdmin={user.role === "admin"}/></div>}
    {visited.has("settings") && <div hidden={section !== "settings"}><SettingsWorkspace api={api} userName={user.name}/></div>}
    {section === "admin" && user.role === "admin" && <AdminWorkspace api={api} />}
    {error && <div className="workspace-global-error" role="alert">{error}<button onClick={() => setError("")} aria-label="Uyarıyı kapat">×</button></div>}
  </WorkspaceShell>;
}

function AuthScreen({ phase, error, vaultMissing, vaultRecover, onSubmit, onRetry }: {
  phase: SessionPhase; error: string; vaultMissing: boolean; vaultRecover: boolean;
  onSubmit: (event: React.FormEvent<HTMLFormElement>) => void; onRetry: () => void;
}) {
  if (phase === "checking") return <main className="auth-page"><section className="auth-card" role="status"><span className="spinner" aria-hidden="true"/><h1>WebUI kontrol ediliyor</h1><p className="auth-copy">Hesap ve şifreli kasa durumu alınıyor.</p></section></main>;
  if (phase === "error") return <main className="auth-page"><section className="auth-card"><span className="auth-mark">M</span><p className="eyebrow">MOODY STUDIO</p><h1>Bağlantı kurulamadı</h1><p className="auth-copy">{error || "WebUI hazırlık yanıtı vermedi."}</p><button className="primary-button" onClick={onRetry}>Yeniden dene</button></section></main>;
  const setup = phase === "setup", login = phase === "login", vault = phase === "vault";
  const title = setup ? "İlk hesabını oluştur" : login ? "Tekrar hoş geldin" : vaultRecover ? "Kasayı kurtar" : vaultMissing ? "Kasa oluştur" : "Kasayı aç";
  const copy = setup ? "Kullanıcı adını ve giriş parolanı belirle. Ardından ayrı kasa parolanı oluştur." : login ? "WebUI hesabınla giriş yap." : vaultRecover ? "Tamamlanmamış kasa kurtarma işlemini sürdür." : vaultMissing ? "Şifreli dosyaların için yeni bir kasa parolası belirle." : "Şifreli çalışma alanına erişmek için kasa parolanı gir.";
  return <main className="auth-page"><form className="auth-card" onSubmit={(event) => void onSubmit(event)}>
    <span className="auth-mark">M</span><p className="eyebrow">MOODY STUDIO</p><h1>{title}</h1><p className="auth-copy">{copy}</p>
    {(setup || login) && <label>Kullanıcı adı<input name="username" autoComplete="username" required autoFocus/></label>}
    <label>{vault ? vaultMissing ? "Yeni kasa parolası" : "Kasa parolası" : "Giriş parolası"}<input name="password" type="password" autoComplete={login || (vault && !vaultMissing) ? "current-password" : "new-password"} required autoFocus={!setup && !login}/></label>
    {(setup || (vault && vaultMissing)) && <label>{vault ? "Kasa parolasını doğrula" : "Giriş parolasını doğrula"}<input name="password-repeat" type="password" autoComplete="new-password" required/></label>}
    {vault && vaultMissing && <label className="auth-check"><input type="checkbox" name="risk" required/> Kasa parolamı kaybetmemem gerektiğini anladım.</label>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <button className="primary-button">Devam et</button>
  </form></main>;
}
