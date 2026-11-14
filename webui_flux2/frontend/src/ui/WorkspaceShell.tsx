import { useState, type ReactNode } from "react";
import {
  Archive, Bot, Images, Layers3, LockKeyhole, MessageSquare, Monitor, MoreHorizontal,
  Settings2, Sparkles, Workflow,
} from "lucide-react";
import "./WorkspaceShell.css";

export type WorkspaceSection =
  | "production" | "studio" | "queue" | "library" | "archive"
  | "prompts" | "chat" | "monitor" | "settings" | "admin";

interface Props {
  active: WorkspaceSection;
  onNavigate: (section: WorkspaceSection) => void;
  userName: string;
  isAdmin: boolean;
  onLock: () => void;
  children: ReactNode;
}

const primaryItems = [
  { id: "production", label: "Üretim", Icon: Images },
  { id: "studio", label: "Prompt stüdyosu", Icon: Sparkles },
  { id: "chat", label: "Sohbet", Icon: MessageSquare },
  { id: "library", label: "Kütüphane", Icon: Images },
  { id: "archive", label: "Arşiv", Icon: Archive },
] as const;

const secondaryItems = [
  { id: "queue", label: "İş kuyruğu", Icon: Workflow },
  { id: "prompts", label: "Promptlarım", Icon: Bot },
  { id: "monitor", label: "Sistem durumu", Icon: Monitor },
  { id: "settings", label: "Ayarlar", Icon: Settings2 },
] as const;

export function WorkspaceShell({ active, onNavigate, userName, isAdmin, onLock, children }: Props) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = (secondaryItems as readonly {id: WorkspaceSection}[])
    .some((item) => item.id === active) || active === "admin" || active === "archive";
  const mobileItems: {id: WorkspaceSection; label: string; Icon: typeof Images}[] = [
    ...primaryItems.slice(0, 4),
    { id: "queue", label: "Daha", Icon: MoreHorizontal },
  ];

  return <div className="workspace-shell">
    <aside className="workspace-sidebar" aria-label="Ana gezinme">
      <button className="workspace-brand" type="button" onClick={() => onNavigate("production")}>
        <span className="workspace-brand__mark">M</span>
        <span><b>Moody Studio</b><small>GÖRSEL ÇALIŞMA ALANI</small></span>
      </button>
      <div className="workspace-side-label">ÇALIŞMA ALANI</div>
      <nav className="workspace-nav" aria-label="Çalışma alanı">
        {primaryItems.map(({ id, label, Icon }) => <button key={id}
          className={`workspace-nav__item${active === id ? " is-active" : ""}`}
          type="button" aria-current={active === id ? "page" : undefined}
          onClick={() => onNavigate(id)}><Icon size={17} aria-hidden="true"/><span>{label}</span></button>)}
      </nav>
      <div className="workspace-side-label workspace-side-label--secondary">YÖNETİM</div>
      <nav className="workspace-nav" aria-label="Yönetim ve durum">
        {secondaryItems.map(({ id, label, Icon }) => <button key={id}
          className={`workspace-nav__item${active === id ? " is-active" : ""}`}
          type="button" aria-current={active === id ? "page" : undefined}
          onClick={() => onNavigate(id)}><Icon size={17} aria-hidden="true"/><span>{label}</span></button>)}
        {isAdmin && <button className={`workspace-nav__item${active === "admin" ? " is-active" : ""}`}
          type="button" aria-current={active === "admin" ? "page" : undefined}
          onClick={() => onNavigate("admin")}><Layers3 size={17} aria-hidden="true"/><span>Kullanıcılar</span></button>}
      </nav>
      <div className="workspace-account">
        <span className="workspace-account__avatar" aria-hidden="true">{(userName[0] || "U").toUpperCase()}</span>
        <span className="workspace-account__name" title={userName}>{userName}</span>
        <button type="button" onClick={onLock} title="Kasayı kilitle" aria-label="Kasayı kilitle"><LockKeyhole size={15}/></button>
      </div>
      <a className="workspace-legacy-link" href="/?ui=legacy">Önceki arayüz</a>
    </aside>

    <div className="workspace-main">
      <header className="workspace-mobile-header">
        <button className="workspace-brand workspace-brand--compact" type="button" onClick={() => onNavigate("production")}>
          <span className="workspace-brand__mark">M</span><b>Moody Studio</b>
        </button>
        <span className="workspace-session">Kasa açık</span>
        <button className="workspace-lock" type="button" onClick={onLock}><LockKeyhole size={14}/> Kilitle</button>
      </header>
      <div className="workspace-content">{children}</div>
      <nav className="workspace-mobile-nav" aria-label="Mobil gezinme">
        {mobileItems.map(({ id, label, Icon }) => <button key={id}
          type="button" className={active === id || (id === "queue" && moreActive) ? "is-active" : ""}
          aria-current={active === id ? "page" : undefined}
          aria-label={id === "queue" ? "Daha fazla bölüm" : label}
          aria-expanded={id === "queue" ? moreOpen : undefined}
          onClick={() => id === "queue" ? setMoreOpen((value) => !value) : onNavigate(id)}>
          <Icon size={18} aria-hidden="true"/><span>{label}</span>
        </button>)}
      </nav>
      {moreOpen && <>
        <button className="workspace-mobile-scrim" type="button" aria-label="Bölüm menüsünü kapat" onClick={() => setMoreOpen(false)}/>
        <nav className="workspace-more-menu" aria-label="Diğer bölümler">
          {[{ id: "archive" as const, label: "Arşiv", Icon: Archive }, ...secondaryItems, ...(isAdmin ? [{ id: "admin" as const, label: "Kullanıcılar", Icon: Layers3 }] : [])].map(({ id, label, Icon }) => <button type="button" key={id} aria-current={active === id ? "page" : undefined} onClick={() => { setMoreOpen(false); onNavigate(id); }}><Icon size={17}/>{label}</button>)}
        </nav>
      </>}
    </div>
  </div>;
}
