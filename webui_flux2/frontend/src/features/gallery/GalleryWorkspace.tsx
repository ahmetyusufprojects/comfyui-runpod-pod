import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Image as ImageIcon, LoaderCircle, Video, X } from "lucide-react";
import type { JsonRecord, ProductionApi } from "../../lib/api/client";
import "./GalleryWorkspace.css";

export type GalleryTab = "archive" | "library" | "history";
type Source = { tur?: string; ad?: string };
type GalleryView = "izgara" | "akis";
type MediaItem = {
  hash?: string;
  ad?: string;
  kayit?: string;
  dosya?: string;
  url: string;
  thumb?: string;
  video_poster_url?: string | null;
  tur?: string;
  tarih?: string;
  boyut?: number[] | null;
  bayt?: number | null;
  dosya_bayt?: number | null;
  kaynak_sayisi?: number;
  kaynaklar?: Source[];
  prompt?: string;
  model?: string;
  width?: number;
  height?: number;
  yildiz?: boolean;
  album?: boolean;
  album_member?: boolean;
  album_id?: string;
  album_index?: number;
  album_count?: number;
  dosyalar?: string[];
  orijinal?: { boyut?: number[] | null; bayt?: number | null } | null;
  bicim?: string | null;
  mod?: string | null;
};

interface GalleryResponse {
  kayitlar?: MediaItem[];
  fotograflar?: MediaItem[];
  toplam?: number;
  benzersiz?: number;
}

export interface GalleryReference {
  url: string;
  name?: string;
  width?: number;
  height?: number;
}

export interface GalleryWorkspaceProps {
  api: Pick<ProductionApi, "get" | "post">;
  initialTab?: GalleryTab;
  onUseReference?: (reference: GalleryReference) => void | Promise<void>;
}

function displaySize(value?: number | null): string {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB"];
  let amount = bytes / 1024;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${units[index]}`;
}

function dateLabel(value?: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value.replace("T", " ") : parsed.toLocaleString("tr-TR");
}

function itemTitle(item: MediaItem): string {
  return item.ad || item.dosya || item.prompt || item.kayit || item.hash?.slice(0, 12) || "Görsel";
}

function mediaUrl(item: MediaItem): string {
  if (item.tur === "video" && item.video_poster_url) return item.video_poster_url;
  return item.thumb || item.url;
}

function imageDimensions(item: MediaItem): [number, number] | null {
  const dimensions = item.boyut ?? (item.width && item.height ? [item.width, item.height] : null);
  if (!dimensions || dimensions.length < 2 || !Number(dimensions[0]) || !Number(dimensions[1])) return null;
  return [Number(dimensions[0]), Number(dimensions[1])];
}

function sourceLabel(value?: string): string {
  if (value === "ice-aktarim") return "İçe aktarılan";
  if (value === "kutuphane") return "Kütüphane";
  if (value === "gecmis") return "Geçmiş";
  return "Sohbet";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "İstek tamamlanamadı.";
}

function queryString(values: Record<string, string | number | boolean | undefined>): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined) params.set(key, String(value));
  });
  return params.toString();
}

async function fileAsImage(file: File): Promise<{ mime: string; veri: string; ad: string }> {
  const encoded = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error(`${file.name} okunamadı.`));
    reader.readAsDataURL(file);
  });
  const [header, data] = encoded.split(",", 2);
  const mime = header.match(/^data:([^;,]+)/)?.[1] || file.type || "image/png";
  if (!data) throw new Error(`${file.name} içeriği okunamadı.`);
  return { mime, veri: data, ad: file.name };
}

function Pagination({ page, total, perPage, label, onChange }: {
  page: number; total: number; perPage: number; label: string; onChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / perPage));
  if (pages <= 1) return null;
  const start = Math.max(1, Math.min(page - 2, pages - 4));
  const visible = Array.from({ length: Math.min(5, pages - start + 1) }, (_, index) => start + index);
  return <nav className="gallery-pagination" aria-label={`${label} sayfaları`}>
    <button type="button" className="gallery-button gallery-button--quiet" disabled={page <= 1} onClick={() => onChange(page - 1)} aria-label="Önceki sayfa">‹</button>
    {visible.map((number) => <button key={number} type="button" className={`gallery-button gallery-page-number${number === page ? " is-active" : ""}`} aria-current={number === page ? "page" : undefined} onClick={() => onChange(number)}>{number}</button>)}
    <button type="button" className="gallery-button gallery-button--quiet" disabled={page >= pages} onClick={() => onChange(page + 1)} aria-label="Sonraki sayfa">›</button>
    <span className="gallery-pagination__total">{page} / {pages}</span>
  </nav>;
}

function GalleryViewButtons({ value, onChange, label }: { value: GalleryView; onChange: (value: GalleryView) => void; label: string }) {
  return <div className="gallery-view-toggle" role="group" aria-label={`${label} görünümü`}>
    <button type="button" className={value === "izgara" ? "is-active" : ""} aria-pressed={value === "izgara"} onClick={() => onChange("izgara")}>Izgara</button>
    <button type="button" className={value === "akis" ? "is-active" : ""} aria-pressed={value === "akis"} onClick={() => onChange("akis")}>Akış</button>
  </div>;
}

function MediaCard({ item, selected, selectable, onOpen, onToggle }: {
  item: MediaItem; selected?: boolean; selectable?: boolean; onOpen: () => void; onToggle?: () => void;
}) {
  const dimensions = imageDimensions(item);
  const size = displaySize(item.bayt ?? item.dosya_bayt);
  return <article className={`gallery-card${selected ? " is-selected" : ""}`}>
    <button type="button" className="gallery-card__open" onClick={onOpen} aria-label={`${itemTitle(item)} ayrıntılarını aç`}>
      <span className="gallery-card__image">
        {item.tur === "video" ? <><img src={mediaUrl(item)} loading="lazy" alt="" /><span className="gallery-card__video" aria-label="Video"><Video size={15} /> Video</span></> : <img src={mediaUrl(item)} loading="lazy" alt="" />}
      </span>
      <span className="gallery-card__info">
        <strong title={itemTitle(item)}>{itemTitle(item)}</strong>
        <span>{[dimensions && dimensions.join(" × "), size, dateLabel(item.tarih)].filter(Boolean).join(" · ") || "Ayrıntıyı görüntüle"}</span>
      </span>
    </button>
    {selectable && onToggle && <button type="button" className="gallery-card__select" aria-pressed={!!selected} aria-label={selected ? `${itemTitle(item)} seçimini kaldır` : `${itemTitle(item)} seç`} onClick={onToggle}>{selected ? <Check size={16} /> : <span aria-hidden="true">+</span>}</button>}
  </article>;
}

function MediaDialog({ item, list, index, onClose, onStep, hasPrevious, hasNext, onUseReference, onFavorite, onRemove, removeLabel, busy }: {
  item: MediaItem; list: MediaItem[]; index: number; onClose: () => void; onStep: (delta: -1 | 1) => void | Promise<void>;
  hasPrevious: boolean; hasNext: boolean;
  onUseReference?: (reference: GalleryReference) => void | Promise<void>;
  onFavorite?: (item: MediaItem) => Promise<void>; onRemove?: (item: MediaItem) => Promise<void>; removeLabel?: string; busy?: boolean;
}) {
  const dimensions = imageDimensions(item);
  const isVideo = item.tur === "video";
  return <div className="gallery-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="gallery-dialog" role="dialog" aria-modal="true" aria-labelledby="gallery-dialog-title">
      <header className="gallery-dialog__header">
        <div><p className="gallery-eyebrow">{isVideo ? "VİDEO" : "GÖRSEL"}</p><h2 id="gallery-dialog-title">{itemTitle(item)}</h2></div>
        <button type="button" className="gallery-icon-button" aria-label="Ayrıntıları kapat" onClick={onClose}><X size={18} /></button>
      </header>
      <div className="gallery-dialog__body">
        <div className="gallery-preview">
          {isVideo ? <video controls playsInline poster={item.video_poster_url || undefined} src={item.url} /> : <img src={item.url} alt={item.prompt || itemTitle(item)} />}
        </div>
        <div className="gallery-detail">
          {item.prompt && <p className="gallery-detail__prompt">{item.prompt}</p>}
          <dl>
            {dimensions && <><dt>Boyut</dt><dd>{dimensions[0]} × {dimensions[1]}</dd></>}
            {(item.bayt != null || item.dosya_bayt != null) && <><dt>Dosya</dt><dd>{displaySize(item.bayt ?? item.dosya_bayt)}</dd></>}
            {item.bicim && <><dt>Biçim</dt><dd>{item.bicim}{item.mod ? ` · ${item.mod}` : ""}</dd></>}
            {item.tarih && <><dt>Tarih</dt><dd>{dateLabel(item.tarih)}</dd></>}
            {item.model && <><dt>Model</dt><dd>{item.model}</dd></>}
            {item.kaynak_sayisi != null && <><dt>Kopya</dt><dd>{item.kaynak_sayisi} kaynak</dd></>}
          </dl>
          {!!item.kaynaklar?.length && <div className="gallery-sources"><h3>Kaynak kayıtları</h3><ul>{item.kaynaklar.map((source, sourceIndex) => <li key={`${source.tur}-${source.ad}-${sourceIndex}`}><span>{sourceLabel(source.tur)}</span><span>{source.ad}</span></li>)}</ul><p>Arşiv kopyasını kaldırmak bu kaynak kayıtlarını silmez.</p></div>}
        </div>
      </div>
      <footer className="gallery-dialog__footer">
        <div className="gallery-dialog__navigation">
          <button type="button" className="gallery-button gallery-button--quiet" disabled={!hasPrevious || busy} onClick={() => void onStep(-1)} aria-label="Önceki öğe">‹</button>
          <span>{index + 1} / {list.length}</span>
          <button type="button" className="gallery-button gallery-button--quiet" disabled={!hasNext || busy} onClick={() => void onStep(1)} aria-label="Sonraki öğe">›</button>
        </div>
        <div className="gallery-dialog__actions">
          {!isVideo && onUseReference && <button type="button" className="gallery-button gallery-button--primary" onClick={() => void onUseReference({ url: item.url, name: itemTitle(item), width: dimensions?.[0], height: dimensions?.[1] })}>Üretimde kullan</button>}
          {onFavorite && <button type="button" className="gallery-button" aria-pressed={!!item.yildiz} disabled={busy} onClick={() => void onFavorite(item)}>{item.yildiz ? "★ Favori" : "☆ Favori"}</button>}
          {onRemove && <button type="button" className="gallery-button gallery-button--danger" disabled={busy} onClick={() => void onRemove(item)}>{removeLabel || "Kaldır"}</button>}
        </div>
      </footer>
    </section>
  </div>;
}

export function GalleryWorkspace({ api, initialTab = "archive", onUseReference }: GalleryWorkspaceProps) {
  const [tab, setTab] = useState<GalleryTab>(initialTab);
  const [archiveItems, setArchiveItems] = useState<MediaItem[]>([]);
  const [archivePage, setArchivePage] = useState(1);
  const [archiveTotal, setArchiveTotal] = useState(0);
  const [archiveQuery, setArchiveQuery] = useState("");
  const [archiveView, setArchiveView] = useState<GalleryView>("izgara");
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [archiveError, setArchiveError] = useState("");
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedHashes, setSelectedHashes] = useState<Set<string>>(() => new Set());
  const [uploadStatus, setUploadStatus] = useState("");

  const [libraryItems, setLibraryItems] = useState<MediaItem[]>([]);
  const [libraryQuery, setLibraryQuery] = useState("");
  const [libraryView, setLibraryView] = useState<GalleryView>("izgara");
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState("");

  const [historyItems, setHistoryItems] = useState<MediaItem[]>([]);
  const [historyPage, setHistoryPage] = useState(1);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyViewMode, setHistoryViewMode] = useState<GalleryView>("izgara");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [groupAlbums, setGroupAlbums] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [activeAlbum, setActiveAlbum] = useState<MediaItem | null>(null);
  const [albumItems, setAlbumItems] = useState<MediaItem[]>([]);
  const [albumPage, setAlbumPage] = useState(1);
  const [albumTotal, setAlbumTotal] = useState(0);
  const [albumLoading, setAlbumLoading] = useState(false);

  const [dialogList, setDialogList] = useState<MediaItem[] | null>(null);
  const [dialogIndex, setDialogIndex] = useState(-1);
  const [dialogBusy, setDialogBusy] = useState(false);
  const historyPerPage = 12;

  useEffect(() => {
    let active = true;
    void api.get<JsonRecord>("/api/ayarlar").then((data) => {
      if (!active) return;
      const settings = data.ayarlar && typeof data.ayarlar === "object" ? data.ayarlar as JsonRecord : {};
      const ui = settings.ui && typeof settings.ui === "object" ? settings.ui as JsonRecord : {};
      if (ui.history_view === "izgara" || ui.history_view === "akis") setHistoryViewMode(ui.history_view);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [api]);

  const loadArchive = useCallback(async (page: number, query: string, signal?: AbortSignal) => {
    setArchiveLoading(true);
    setArchiveError("");
    try {
      const data = await api.get<GalleryResponse>(`/api/arsiv?${queryString({ sayfa: page, adet: 24, q: query.trim() })}`, signal);
      const items = data.kayitlar ?? [];
      setArchiveItems(items);
      setArchiveTotal(Number(data.toplam) || 0);
      return items;
    } catch (error) {
      if (!signal?.aborted) { setArchiveItems([]); setArchiveTotal(0); setArchiveError(errorMessage(error)); }
      return [];
    } finally {
      if (!signal?.aborted) setArchiveLoading(false);
    }
  }, [api]);

  useEffect(() => {
    if (tab !== "archive") return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => void loadArchive(archivePage, archiveQuery, controller.signal), archiveQuery ? 220 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [tab, archivePage, archiveQuery, loadArchive]);

  const loadLibrary = useCallback(async () => {
    setLibraryLoading(true); setLibraryError("");
    try {
      const data = await api.get<GalleryResponse>("/api/kutuphane");
      setLibraryItems(data.fotograflar ?? []);
    } catch (error) { setLibraryError(errorMessage(error)); }
    finally { setLibraryLoading(false); }
  }, [api]);

  useEffect(() => { if (tab === "library") void loadLibrary(); }, [tab, loadLibrary]);

  const loadHistory = useCallback(async (page: number, signal?: AbortSignal) => {
    setHistoryLoading(true); setHistoryError("");
    try {
      const data = await api.get<GalleryResponse>(`/api/gecmis?${queryString({
        sayfa: page, adet: 12, q: historyQuery.trim(), favoriler: favoritesOnly ? 1 : 0, albume_gore: groupAlbums ? 1 : 0,
      })}`, signal);
      const items = data.kayitlar ?? [];
      setHistoryItems(items); setHistoryTotal(Number(data.toplam) || 0);
      return items;
    } catch (error) {
      if (!signal?.aborted) { setHistoryItems([]); setHistoryTotal(0); setHistoryError(errorMessage(error)); }
      return [];
    } finally { if (!signal?.aborted) setHistoryLoading(false); }
  }, [api, favoritesOnly, groupAlbums, historyQuery]);

  useEffect(() => {
    if (tab !== "history" || activeAlbum) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => void loadHistory(historyPage, controller.signal), historyQuery ? 220 : 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [tab, activeAlbum, historyPage, historyQuery, loadHistory]);

  const loadAlbum = useCallback(async (album: MediaItem, page: number): Promise<MediaItem[]> => {
    if (!album.album_id) return [];
    setAlbumLoading(true); setHistoryError("");
    try {
      const data = await api.get<GalleryResponse>(`/api/gecmis?${queryString({ album_id: album.album_id, sayfa: page, adet: 12 })}`);
      const items = data.kayitlar ?? [];
      setAlbumItems(items); setAlbumTotal(Number(data.toplam) || Number(album.album_count) || 0); setAlbumPage(page);
      return items;
    } catch (error) { setHistoryError(errorMessage(error)); return []; }
    finally { setAlbumLoading(false); }
  }, [api]);

  const openAlbum = useCallback(async (album: MediaItem) => {
    setActiveAlbum(album);
    const initialPage = Math.max(1, Math.floor(Number(album.album_index ?? 0) / 12) + 1);
    return loadAlbum(album, initialPage);
  }, [loadAlbum]);

  const setPage = (value: number) => {
    setArchivePage(value);
  };

  const toggleSelected = (hash?: string) => {
    if (!hash) return;
    setSelectedHashes((previous) => {
      const next = new Set(previous);
      if (next.has(hash)) next.delete(hash);
      else if (next.size < 100) next.add(hash);
      else setArchiveError("Bir seferde en fazla 100 fotoğraf seçebilirsin.");
      return next;
    });
  };

  const selectCurrentPage = () => {
    setSelectedHashes((previous) => {
      const next = new Set(previous);
      for (const item of archiveItems) {
        if (!item.hash || next.has(item.hash)) continue;
        if (next.size >= 100) { setArchiveError("Bir seferde en fazla 100 fotoğraf seçebilirsin."); break; }
        next.add(item.hash);
      }
      return next;
    });
  };

  const removeArchiveItems = async (hashes: string[]) => {
    if (!hashes.length || archiveBusy) return;
    const message = hashes.length === 1
      ? "Bu görsel yalnızca arşiv görünümünden kaldırılsın mı? Kütüphane, geçmiş ve sohbet kayıtları korunur."
      : `${hashes.length} görsel arşiv görünümünden kaldırılsın mı? Kaynak kayıtlar korunur.`;
    if (!window.confirm(message)) return;
    setArchiveBusy(true); setArchiveError("");
    try {
      if (hashes.length === 1) await api.post("/api/arsiv/sil", { hash: hashes[0] });
      else await api.post("/api/arsiv/sil-coklu", { hashes });
      setDialogList(null); setDialogIndex(-1); setSelectionMode(false); setSelectedHashes(new Set());
      const nextTotal = Math.max(0, archiveTotal - hashes.length);
      const nextPage = Math.min(archivePage, Math.max(1, Math.ceil(nextTotal / 24)));
      setArchiveTotal(nextTotal); setArchivePage(nextPage);
      await loadArchive(nextPage, archiveQuery);
    } catch (error) { setArchiveError(`Silme tamamlanamadı: ${errorMessage(error)}`); }
    finally { setArchiveBusy(false); }
  };

  const importFiles = async (files: File[]) => {
    if (!files.length) return;
    setArchiveBusy(true); setArchiveError("");
    let added = 0; let duplicates = 0; let errors: string[] = [];
    try {
      const validFiles = files.filter((file) => file.type.startsWith("image/"));
      if (validFiles.length !== files.length) errors.push("Görsel olmayan dosyalar atlandı.");
      for (let start = 0; start < validFiles.length; start += 12) {
        const batch = validFiles.slice(start, start + 12);
        setUploadStatus(`Görseller hazırlanıyor: ${Math.min(start + batch.length, validFiles.length)} / ${validFiles.length}`);
        const images = await Promise.all(batch.map(fileAsImage));
        const result = await api.post<JsonRecord>("/api/arsiv/ice-aktar", { gorseller: images });
        added += Number(result.eklenen) || 0;
        duplicates += Number(result.tekrar) || 0;
        if (Array.isArray(result.hata)) errors = errors.concat(result.hata.map(String));
      }
      setUploadStatus(`${added} yeni · ${duplicates} kopya atlandı${errors.length ? ` · ${errors.length} hata` : ""}`);
      if (errors.length) setArchiveError(errors.slice(0, 4).join(" · "));
      setArchivePage(1);
      await loadArchive(1, archiveQuery);
    } catch (error) { setArchiveError(`İçe aktarma tamamlanamadı: ${errorMessage(error)}`); }
    finally { setArchiveBusy(false); }
  };

  const removeLibraryItem = async (item: MediaItem) => {
    if (!window.confirm(`“${itemTitle(item)}” kütüphaneden silinsin mi?`)) return;
    setDialogBusy(true);
    try {
      await api.post("/api/kutuphane/sil", { ad: item.ad });
      setDialogList(null); setDialogIndex(-1);
      await loadLibrary();
    } catch (error) { setLibraryError(`Görsel silinemedi: ${errorMessage(error)}`); }
    finally { setDialogBusy(false); }
  };

  const clearLibrary = async () => {
    if (!libraryItems.length || !window.confirm("Kütüphanedeki bütün fotoğraflar ve bunların küçük/orijinal kopyaları silinsin mi?")) return;
    setLibraryLoading(true); setLibraryError("");
    try { await api.post("/api/kutuphane/temizle", {}); setDialogList(null); setDialogIndex(-1); await loadLibrary(); }
    catch (error) { setLibraryError(`Kütüphane temizlenemedi: ${errorMessage(error)}`); }
    finally { setLibraryLoading(false); }
  };

  const removeHistoryItem = async (item: MediaItem) => {
    if (!item.kayit || !window.confirm("Bu üretim geçmişi kaydı ve ona bağlı medya geçmişten silinsin mi?")) return;
    setDialogBusy(true); setHistoryError("");
    try {
      await api.post("/api/gecmis/sil", { kayit: item.kayit });
      setDialogList(null); setDialogIndex(-1);
      await loadHistory(historyPage);
    } catch (error) { setHistoryError(`Geçmiş kaydı silinemedi: ${errorMessage(error)}`); }
    finally { setDialogBusy(false); }
  };

  const changeHistoryView = (value: GalleryView) => {
    setHistoryViewMode(value);
    void api.post("/api/ayarlar", { ui: { history_view: value } }).catch(() => setHistoryError("Görünüm tercihi kaydedilemedi."));
  };

  const favoriteHistoryItem = async (item: MediaItem) => {
    if (!item.kayit) return;
    setDialogBusy(true);
    try {
      const next = !item.yildiz;
      await api.post("/api/gecmis/yildiz", { kayit: item.kayit, yildiz: next, dosya: item.dosya });
      const update = (items: MediaItem[]) => items.map((entry) => entry.kayit === item.kayit && entry.dosya === item.dosya ? { ...entry, yildiz: next } : entry);
      setHistoryItems(update); setAlbumItems(update);
      setDialogList((current) => current ? update(current) : current);
      if (favoritesOnly && !next) {
        setDialogList(null); setDialogIndex(-1);
        await loadHistory(historyPage);
      }
    } catch (error) { setHistoryError(`Favori güncellenemedi: ${errorMessage(error)}`); }
    finally { setDialogBusy(false); }
  };

  const openDialog = (list: MediaItem[], index: number) => { setDialogList(list); setDialogIndex(index); };
  const dialogItem = dialogList && dialogIndex >= 0 ? dialogList[dialogIndex] : null;
  const adjacentAlbum = (direction: -1 | 1): MediaItem | undefined => {
    if (!activeAlbum?.album_id) return undefined;
    const current = historyItems.findIndex(item => item.album_id === activeAlbum.album_id);
    if (current < 0) return undefined;
    const items = direction > 0 ? historyItems.slice(current + 1) : historyItems.slice(0, current).reverse();
    return items.find(item => item.album && item.album_id && item.album_id !== activeAlbum.album_id);
  };
  const dialogHasPrevious = Boolean(dialogIndex > 0 || (tab === "archive" && archivePage > 1)
    || (tab === "history" && (activeAlbum ? albumPage > 1 || Boolean(adjacentAlbum(-1)) || historyPage > 1 : historyPage > 1)));
  const dialogHasNext = Boolean(dialogList && dialogIndex < dialogList.length - 1
    || (tab === "archive" && archivePage < Math.ceil(archiveTotal / 24))
    || (tab === "history" && (activeAlbum
      ? albumPage < Math.ceil(albumTotal / historyPerPage) || Boolean(adjacentAlbum(1)) || historyPage < Math.ceil(historyTotal / historyPerPage)
      : historyPage < Math.ceil(historyTotal / historyPerPage))));

  const stepDialog = async (direction: -1 | 1) => {
    if (!dialogList || dialogBusy || archiveBusy) return;
    const nextIndex = dialogIndex + direction;
    if (nextIndex >= 0 && nextIndex < dialogList.length) { setDialogIndex(nextIndex); return; }
    setDialogBusy(true);
    try {
      if (tab === "archive") {
        const nextPage = archivePage + direction;
        if (nextPage < 1 || nextPage > Math.ceil(archiveTotal / 24)) return;
        const items = await loadArchive(nextPage, archiveQuery);
        if (items.length) { setArchivePage(nextPage); setDialogList(items); setDialogIndex(direction > 0 ? 0 : items.length - 1); }
        return;
      }
      if (tab !== "history") return;
      if (activeAlbum) {
        const albumPages = Math.max(1, Math.ceil(albumTotal / historyPerPage));
        const nextAlbumPage = albumPage + direction;
        if (nextAlbumPage >= 1 && nextAlbumPage <= albumPages) {
          const items = await loadAlbum(activeAlbum, nextAlbumPage);
          if (items.length) { setDialogList(items); setDialogIndex(direction > 0 ? 0 : items.length - 1); }
          return;
        }
        const samePageAlbum = adjacentAlbum(direction);
        let target = samePageAlbum;
        if (!target) {
          const nextHistoryPage = historyPage + direction;
          const pages = Math.max(1, Math.ceil(historyTotal / historyPerPage));
          if (nextHistoryPage < 1 || nextHistoryPage > pages) return;
          const topLevel = await loadHistory(nextHistoryPage);
          setHistoryPage(nextHistoryPage);
          target = direction > 0 ? topLevel.find(item => item.album && item.album_id !== activeAlbum.album_id) : [...topLevel].reverse().find(item => item.album && item.album_id !== activeAlbum.album_id);
        }
        if (target?.album_id) {
          setActiveAlbum(target);
          const targetPage = direction > 0 ? Math.max(1, Math.floor(Number(target.album_index ?? 0) / historyPerPage) + 1) : Math.max(1, Math.ceil(Number(target.album_count ?? 1) / historyPerPage));
          const items = await loadAlbum(target, targetPage);
          if (items.length) { setDialogList(items); setDialogIndex(direction > 0 ? 0 : items.length - 1); }
        }
        return;
      }
      const nextPage = historyPage + direction;
      const pages = Math.max(1, Math.ceil(historyTotal / historyPerPage));
      if (nextPage < 1 || nextPage > pages) return;
      const items = await loadHistory(nextPage);
      if (items.length) { setHistoryPage(nextPage); setDialogList(items); setDialogIndex(direction > 0 ? 0 : items.length - 1); }
    } finally { setDialogBusy(false); }
  };
  const filteredLibrary = useMemo(() => {
    const query = libraryQuery.trim().toLocaleLowerCase("tr-TR");
    return libraryItems.filter((item) => !query || itemTitle(item).toLocaleLowerCase("tr-TR").includes(query));
  }, [libraryItems, libraryQuery]);

  useEffect(() => {
    if (!dialogItem) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDialogList(null);
      if (event.key === "ArrowLeft" && dialogHasPrevious) { event.preventDefault(); void stepDialog(-1); }
      if (event.key === "ArrowRight" && dialogHasNext) { event.preventDefault(); void stepDialog(1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dialogItem, dialogHasNext, dialogHasPrevious, stepDialog]);

  const historyView = activeAlbum ? albumItems : historyItems;
  const historyViewTotal = activeAlbum ? albumTotal : historyTotal;
  const historyViewPage = activeAlbum ? albumPage : historyPage;

  return <main className="gallery-page">
    <header className="gallery-heading">
      <div><p className="gallery-eyebrow">GÖRSEL KOLEKSİYONU</p><h1>Galeri</h1><p>Üretim geçmişi, kütüphane ve arşiv içeriklerini tek yerde bul.</p></div>
      <span className="gallery-heading__count">{tab === "archive" ? `${archiveTotal} benzersiz görsel` : tab === "library" ? `${libraryItems.length} görsel` : `${historyTotal} kayıt`}</span>
    </header>

    <nav className="gallery-tabs" aria-label="Galeri bölümleri">
      {(["archive", "library", "history"] as const).map((value) => <button key={value} type="button" className={tab === value ? "is-active" : ""} aria-current={tab === value ? "page" : undefined} onClick={() => setTab(value)}>{value === "archive" ? "Arşiv" : value === "library" ? "Kütüphane" : "Geçmiş ve albümler"}</button>)}
    </nav>

    {tab === "archive" && <section aria-label="Arşiv">
      <div className="gallery-toolbar">
        <label className="gallery-search"><span className="sr-only">Arşivde ara</span><input type="search" placeholder="Ad veya tarihe göre ara…" value={archiveQuery} onChange={(event) => { setArchiveQuery(event.target.value); setArchivePage(1); setSelectedHashes(new Set()); }} /></label>
        <div className="gallery-toolbar__actions">
          <GalleryViewButtons value={archiveView} onChange={setArchiveView} label="Arşiv" />
          <label className="gallery-button gallery-button--upload">Görsel ekle<input type="file" accept="image/*" multiple disabled={archiveBusy} onChange={(event) => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void importFiles(files); }} /></label>
          <button type="button" className={`gallery-button${selectionMode ? " is-active" : ""}`} aria-pressed={selectionMode} onClick={() => { setSelectionMode((active) => !active); setSelectedHashes(new Set()); }}>{selectionMode ? "Seçimi bitir" : "Seç"}</button>
        </div>
      </div>
      {uploadStatus && <p className="gallery-status" role="status">{uploadStatus}</p>}
      {selectionMode && <div className="gallery-selection-bar"><span aria-live="polite">{selectedHashes.size} görsel seçildi</span><div><button type="button" className="gallery-button" onClick={selectCurrentPage}>Bu sayfayı seç</button><button type="button" className="gallery-button gallery-button--quiet" onClick={() => setSelectedHashes(new Set())}>Temizle</button><button type="button" className="gallery-button gallery-button--danger" disabled={!selectedHashes.size || archiveBusy} onClick={() => void removeArchiveItems(Array.from(selectedHashes))}>Arşivden kaldır</button></div></div>}
      {archiveError && <p className="gallery-alert" role="alert">{archiveError}</p>}
      {archiveLoading && <p className="gallery-loading" role="status"><LoaderCircle size={16} className="gallery-spin" /> Arşiv yükleniyor…</p>}
      {!archiveLoading && archiveItems.length === 0 && <div className="gallery-empty"><ImageIcon size={25} /><h2>{archiveQuery ? "Eşleşen görsel yok" : "Arşiv henüz boş"}</h2><p>Üretimler, kütüphane öğeleri ve eklediğin görseller burada birlikte görünür.</p></div>}
      <div className={`gallery-grid${archiveView === "akis" ? " gallery-grid--flow" : ""}`}>{archiveItems.map((item, index) => <MediaCard key={item.hash ?? `${item.ad}-${index}`} item={item} selectable={selectionMode} selected={!!item.hash && selectedHashes.has(item.hash)} onToggle={() => toggleSelected(item.hash)} onOpen={() => selectionMode ? toggleSelected(item.hash) : openDialog(archiveItems, index)} />)}</div>
      {!archiveLoading && <Pagination page={archivePage} total={archiveTotal} perPage={24} label="Arşiv" onChange={setPage} />}
    </section>}

    {tab === "library" && <section aria-label="Kütüphane">
      <div className="gallery-toolbar"><label className="gallery-search"><span className="sr-only">Kütüphanede ara</span><input type="search" placeholder="Görsel adına göre ara…" value={libraryQuery} onChange={(event) => setLibraryQuery(event.target.value)} /></label><div className="gallery-toolbar__actions"><GalleryViewButtons value={libraryView} onChange={setLibraryView} label="Kütüphane"/><button type="button" className="gallery-button gallery-button--danger" disabled={!libraryItems.length || libraryLoading} onClick={() => void clearLibrary()}>Kütüphaneyi temizle</button><button type="button" className="gallery-button" disabled={libraryLoading} onClick={() => void loadLibrary()}>Yenile</button></div></div>
      {libraryError && <p className="gallery-alert" role="alert">{libraryError}</p>}
      {libraryLoading && <p className="gallery-loading" role="status"><LoaderCircle size={16} className="gallery-spin" /> Kütüphane yükleniyor…</p>}
      {!libraryLoading && filteredLibrary.length === 0 && <div className="gallery-empty"><ImageIcon size={25} /><h2>{libraryQuery ? "Eşleşen görsel yok" : "Kütüphane henüz boş"}</h2><p>Üretimlerde kullanılan veya kaydedilen görseller burada bulunur.</p></div>}
      <div className={`gallery-grid${libraryView === "akis" ? " gallery-grid--flow" : ""}`}>{filteredLibrary.map((item, index) => <MediaCard key={`${item.ad}-${index}`} item={item} onOpen={() => openDialog(filteredLibrary, index)} />)}</div>
      <p className="gallery-footnote">Kütüphaneden silme işlemi yalnızca seçtiğin dosyayı ve onun küçük/orijinal kopyasını kaldırır.</p>
    </section>}

    {tab === "history" && <section aria-label="Üretim geçmişi">
      <div className="gallery-toolbar"><label className="gallery-search"><span className="sr-only">Geçmişte ara</span><input type="search" placeholder="Prompt, model veya tarihe göre ara…" value={historyQuery} onChange={(event) => { setHistoryQuery(event.target.value); setHistoryPage(1); }} /></label><div className="gallery-toolbar__actions"><GalleryViewButtons value={historyViewMode} onChange={changeHistoryView} label="Geçmiş"/><label className="gallery-filter"><input type="checkbox" checked={favoritesOnly} onChange={(event) => { setFavoritesOnly(event.target.checked); setHistoryPage(1); setActiveAlbum(null); }} /> Favoriler</label><label className="gallery-filter"><input type="checkbox" checked={groupAlbums} onChange={(event) => { setGroupAlbums(event.target.checked); setHistoryPage(1); setActiveAlbum(null); }} /> Albümleri grupla</label></div></div>
      {activeAlbum && <div className="gallery-album-banner"><button type="button" className="gallery-button gallery-button--quiet" onClick={() => { setActiveAlbum(null); setAlbumItems([]); }}>← Albümlere dön</button><div><h2>{activeAlbum.prompt || "Üretim albümü"}</h2><p>{albumTotal} öğe · sayfa {albumPage} / {Math.max(1, Math.ceil(albumTotal / historyPerPage))}</p></div></div>}
      {historyError && <p className="gallery-alert" role="alert">{historyError}</p>}
      {(historyLoading || albumLoading) && <p className="gallery-loading" role="status"><LoaderCircle size={16} className="gallery-spin" /> Geçmiş yükleniyor…</p>}
      {!historyLoading && !albumLoading && historyView.length === 0 && <div className="gallery-empty"><ImageIcon size={25} /><h2>{historyQuery || favoritesOnly ? "Eşleşen kayıt yok" : "Henüz üretim yok"}</h2><p>Ürettiğin görseller ve videolar burada listelenir.</p></div>}
      <div className={`gallery-grid${historyViewMode === "akis" ? " gallery-grid--flow" : ""}`}>{historyView.map((item, index) => item.album && !activeAlbum ? <button type="button" key={item.album_id ?? item.kayit ?? index} className="gallery-album-card" onClick={() => void openAlbum(item)}><span className="gallery-card__image"><img src={mediaUrl(item)} loading="lazy" alt="" /><span className="gallery-album-card__count">{item.album_count} öğe</span></span><span><strong>{item.prompt || "Üretim albümü"}</strong><small>{dateLabel(item.tarih)}</small></span></button> : <MediaCard key={`${item.kayit}-${item.dosya}-${index}`} item={item} onOpen={() => openDialog(historyView, index)} />)}</div>
      {!historyLoading && !albumLoading && <Pagination page={historyViewPage} total={historyViewTotal} perPage={historyPerPage} label={activeAlbum ? "Albüm" : "Geçmiş"} onChange={(page) => activeAlbum ? void loadAlbum(activeAlbum, page) : setHistoryPage(page)} />}
    </section>}

    {dialogItem && dialogList && <MediaDialog item={dialogItem} list={dialogList} index={dialogIndex} onClose={() => setDialogList(null)} onStep={stepDialog} hasPrevious={dialogHasPrevious} hasNext={dialogHasNext} onUseReference={onUseReference} onFavorite={tab === "history" ? favoriteHistoryItem : undefined} onRemove={tab === "archive" ? (item) => removeArchiveItems(item.hash ? [item.hash] : []) : tab === "library" ? removeLibraryItem : tab === "history" ? removeHistoryItem : undefined} removeLabel={tab === "history" ? "Geçmişten sil" : undefined} busy={dialogBusy || archiveBusy} />}
  </main>;
}
