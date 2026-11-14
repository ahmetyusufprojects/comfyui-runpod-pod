import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { LoaderCircle, Square, X } from "lucide-react";
import type { JsonRecord, ProductionApi } from "../../lib/api/client";
import "./ChatWorkspace.css";

type ChatRole = "user" | "assistant";
type ChatImage = { name: string; dataUrl: string };
type ChatMessage = { role: ChatRole; content: string; images?: string[]; thinking?: string; requestExtra?: JsonRecord };
type SavedChat = { id: string; baslik?: string; mesajlar?: ChatMessage[]; guncelleme?: string };
type ModelOption = { id: string; ad: string; hazir?: boolean; gorsel_hazir?: boolean };
type JobState = "queued" | "running" | "stopping" | "done" | "error" | "cancelled";
type ActiveJob = { id: string; state: JobState; message: string };
type LibraryImage = { ad: string; url: string; thumb?: string };

const MAX_IMAGES = 6;
const MAX_IMAGE_BYTES = 6 * 1024 * 1024;
const PROMPT_CONVERSION_INSTRUCTION = 'Bu sohbette konuşulan son konuyu, fotoğraf üretimi için kullanılacak ayrıntılı ve uzun bir üretim promptuna dönüştür. Konunun özünü ve kullanıcının niyetini koru; sohbette geçen ayrıntıları prompta taşı. Prompt İngilizce, tek paragraf ve üretmeye hazır olsun; sahne, kompozisyon, kamera, ışık, renk paleti, malzeme/doku, stil ve ruh hali ayrıntılarını içersin. Promptun bittiği yerde ayrı bir satırda yalnızca "Aspect ratio: Genişlik:Yükseklik" biçiminde, bu sahne için önerdiğin en uygun en-boy oranını yaz (örn. Aspect ratio: 16:9). Başka açıklama ekleme.';

interface Props {
  api: ProductionApi;
  initialModel?: string;
  onPromptFromChat?: (prompt: string) => void;
}

export function messageText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.filter((part) => part && typeof part.text === "string").map((part) => part.text).join("\n");
  return "";
}

export function restoreSavedMessage(message: ChatMessage & { resim?: string }): ChatMessage {
  const content = message.content as unknown;
  const imageParts = Array.isArray(content) ? content.flatMap((part) => {
    if (!part || typeof part !== "object") return [];
    const item = part as { type?: unknown; image_url?: { url?: unknown } };
    const url = item.type === "image_url" ? item.image_url?.url : undefined;
    return typeof url === "string" ? [url] : [];
  }) : [];
  return { ...message, content: messageText(content), images: imageParts.length ? imageParts : typeof message.resim === "string" ? [message.resim] : message.images };
}

export function acceptedChatFiles(files: FileList | File[], alreadyAttached: number) {
  const accepted: File[] = [];
  const errors: string[] = [];
  let slots = Math.max(0, MAX_IMAGES - alreadyAttached);
  for (const file of Array.from(files)) {
    if (!file.type.startsWith("image/")) continue;
    if (file.size > MAX_IMAGE_BYTES) { errors.push(`${file.name}: Görsel 6 MB sınırını aşıyor.`); continue; }
    if (slots <= 0) { errors.push("Bir mesaja en fazla 6 görsel eklenebilir."); continue; }
    accepted.push(file); slots -= 1;
  }
  return { accepted, errors };
}

function apiMessages(messages: ChatMessage[]) {
  return messages.slice(-40).map((message) => ({ role: message.role, content: message.images?.length ? [
    { type: "text", text: message.content },
    ...message.images.map((url) => ({ type: "image_url", image_url: { url } })),
  ] : message.content }));
}

function titleFor(chat: SavedChat): string {
  if (chat.baslik?.trim()) return chat.baslik.trim();
  return messageText(chat.mesajlar?.find((message) => message.role === "user")?.content).slice(0, 54) || "Yeni sohbet";
}

function readImages(files: FileList | File[] | null): Promise<ChatImage[]> {
  if (!files?.length) return Promise.resolve([]);
  const selected = Array.from(files).filter((file) => file.type.startsWith("image/")).slice(0, 6);
  return Promise.all(selected.map((file) => new Promise<ChatImage>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, dataUrl: String(reader.result) });
    reader.onerror = () => reject(new Error(`${file.name} okunamadı.`));
    reader.readAsDataURL(file);
  })));
}

function mapJobStatus(value: unknown): JobState {
  if (value === "kuyrukta") return "queued";
  if (value === "calisiyor") return "running";
  if (value === "bitti") return "done";
  if (value === "hata") return "error";
  if (value === "durduruldu") return "cancelled";
  return "running";
}

export function ChatWorkspace({ api, initialModel }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [savedChats, setSavedChats] = useState<SavedChat[]>([]);
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null);
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [modelId, setModelId] = useState(initialModel ?? "");
  const [draft, setDraft] = useState("");
  const [images, setImages] = useState<ChatImage[]>([]);
  const [activeJob, setActiveJob] = useState<ActiveJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [imageBusy, setImageBusy] = useState(false);
  const [library, setLibrary] = useState<LibraryImage[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const history = useMemo(() => savedChats.filter((chat) => titleFor(chat).toLocaleLowerCase("tr").includes(search.toLocaleLowerCase("tr"))), [savedChats, search]);

  const loadChats = useCallback(async () => {
    const data = await api.post<JsonRecord>("/api/sohbetler", {});
    setSavedChats(Array.isArray(data.sohbetler) ? data.sohbetler as SavedChat[] : []);
  }, [api]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([api.get<JsonRecord>("/api/durum"), api.post<JsonRecord>("/api/sohbetler", {})])
      .then(([status, chats]) => {
        if (!alive) return;
        const options = Array.isArray(status.llm_models) ? status.llm_models as ModelOption[] : [];
        setModelOptions(options);
        setModelId((current) => initialModel || current || options.find((model) => model.hazir)?.id || options[0]?.id || "");
        setSavedChats(Array.isArray(chats.sohbetler) ? chats.sohbetler as SavedChat[] : []);
        setError("");
      })
      .catch((reason: unknown) => { if (alive) setError(reason instanceof Error ? reason.message : "Sohbetler yüklenemedi."); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [api, initialModel]);

  useEffect(() => {
    let alive = true;
    api.get<JsonRecord>("/api/isler").then((data) => {
      if (!alive) return;
      const jobs = Array.isArray(data.isler) ? data.isler as Array<JsonRecord> : [];
      const job = jobs.filter((item) => item.tur === "llm_chat" && (item.durum === "kuyrukta" || item.durum === "calisiyor")).at(-1);
      if (typeof job?.is_id !== "string") return;
      setMessages((current) => current.length && current.at(-1)?.role === "assistant" ? current : [...current, { role: "assistant", content: "" }]);
      setBusy(true);
      setActiveJob({ id: job.is_id, state: mapJobStatus(job.durum), message: "Devam eden sohbet işi yeniden bağlandı" });
    }).catch(() => { /* Best-effort recovery; chat remains usable if discovery is unavailable. */ });
    return () => { alive = false; };
  }, [api]);

  const newChat = () => {
    if (busy) return;
    setSelectedChatId(null);
    setMessages([]);
    setDraft("");
    setImages([]);
    setError("");
    setNotice("");
  };

  const selectChat = async (id: string) => {
    if (busy) return;
    setLoading(true);
    setError("");
    try {
      const chat = await api.post<SavedChat>(`/api/sohbetler?id=${encodeURIComponent(id)}`, {});
      setSelectedChatId(chat.id);
      setMessages((chat.mesajlar ?? []).map((message) => restoreSavedMessage(message as ChatMessage & { resim?: string })));
      setNotice("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sohbet açılamadı.");
    } finally {
      setLoading(false);
    }
  };

  const deleteChat = async (id: string) => {
    setError("");
    try {
      await api.post("/api/sohbet/sil", { id });
      if (selectedChatId === id) newChat();
      await loadChats();
      setNotice("Sohbet silindi.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sohbet silinemedi.");
    }
  };

  const cancelJob = async () => {
    if (!activeJob) return;
    setActiveJob({ ...activeJob, state: "stopping", message: "Durdurma isteği gönderiliyor…" });
    try {
      await api.post("/api/is/iptal", { is_id: activeJob.id });
      setActiveJob((job) => job ? { ...job, state: "stopping", message: "Durduruluyor…" } : null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sohbet durdurulamadı.");
      setActiveJob((job) => job ? { ...job, state: "running", message: "Yanıt bekleniyor" } : null);
    }
  };

  const enqueueChat = async (chatMessages: ChatMessage[], extra: JsonRecord = {}, chatId = selectedChatId) => {
    const queued = await api.post<JsonRecord>("/api/sohbet", {
      mesajlar: apiMessages(chatMessages), yaraticilik: 0.7,
      llm_model: modelId || undefined, sohbet_id: chatId || undefined, ...extra,
    });
    if (typeof queued.is_id !== "string") throw new Error("Sunucu iş numarası döndürmedi.");
    setActiveJob({ id: queued.is_id, state: "queued", message: queued.sira ? `Kuyrukta · sıra ${queued.sira}` : "Yanıt bekleniyor" });
  };

  useEffect(() => {
    if (!activeJob || !["queued", "running", "stopping"].includes(activeJob.state)) return;
    let alive = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const result = await api.get<JsonRecord>(`/api/is/${encodeURIComponent(activeJob.id)}`);
        if (!alive) return;
        const state = mapJobStatus(result.durum);
        const partial = result.sonuc && typeof result.sonuc === "object" ? result.sonuc as JsonRecord : {};
        if (state === "running" && typeof partial.parca === "string") {
          setMessages((current) => current.map((message, index) => index === current.length - 1 && message.role === "assistant"
            ? { ...message, content: partial.parca as string, thinking: typeof partial.dusunme === "string" ? partial.dusunme : message.thinking }
            : message));
        }
        if (state === "done") {
          const answer = String(partial.yanit ?? "");
          const reasoning = typeof partial.reasoning_content === "string" ? partial.reasoning_content : undefined;
          setMessages((current) => current.map((message, index) => index === current.length - 1 && message.role === "assistant"
            ? { ...message, content: answer, thinking: reasoning }
            : message));
          setSelectedChatId(typeof partial.sohbet_id === "string" ? partial.sohbet_id : selectedChatId);
          setBusy(false);
          setActiveJob(null);
          void loadChats();
          return;
        }
        if (state === "error" || state === "cancelled") {
          const message = state === "cancelled" ? "Yanıt durduruldu." : String(result.hata ?? "Sohbet yanıtı alınamadı.");
          setMessages((current) => current.map((item, index) => index === current.length - 1 && item.role === "assistant"
            ? { ...item, content: message }
            : item));
          setBusy(false);
          setActiveJob(null);
          return;
        }
        const progress = result.ilerleme && typeof result.ilerleme === "object" ? result.ilerleme as JsonRecord : {};
        const phase = String(progress.calisan ?? (state === "queued" ? "Kuyrukta bekliyor" : "Yanıt hazırlanıyor"));
        setActiveJob((job) => job?.id === activeJob.id ? { ...job, state, message: phase } : job);
      } catch (reason) {
        if (!alive) return;
        setError(reason instanceof Error ? reason.message : "İş durumu okunamadı.");
      } finally {
        if (alive) timer = window.setTimeout(() => void poll(), 700);
      }
    };
    void poll();
    return () => { alive = false; if (timer) window.clearTimeout(timer); };
  }, [activeJob?.id, activeJob?.state, api, loadChats, selectedChatId]);

  const send = async () => {
    if (busy || (!draft.trim() && !images.length)) return;
    setBusy(true);
    setError("");
    setNotice("");
    const userText = draft.trim();
    const nextMessages: ChatMessage[] = [...messages, { role: "user", content: userText, images: images.map((image) => image.dataUrl) }];
    setMessages([...nextMessages, { role: "assistant", content: "" }]);
    setDraft("");
    setImages([]);
    try {
      await enqueueChat(nextMessages);
    } catch (reason) {
      setBusy(false);
      setMessages((current) => current.slice(0, -1));
      setError(reason instanceof Error ? reason.message : "Sohbet isteği gönderilemedi.");
    }
  };

  const convertChatToPrompt = async () => {
    if (busy || !messages.length) return;
    setBusy(true); setError(""); setNotice("");
    setMessages((current) => [...current, { role: "assistant", content: "", requestExtra: { ek_talimat: PROMPT_CONVERSION_INSTRUCTION } }]);
    try { await enqueueChat(messages, { ek_talimat: PROMPT_CONVERSION_INSTRUCTION }); }
    catch (reason) {
      setBusy(false); setMessages((current) => current.slice(0, -1));
      setError(reason instanceof Error ? reason.message : "Prompt dönüşümü başlatılamadı.");
    }
  };

  const regenerate = async (index: number) => {
    if (busy || messages[index]?.role !== "assistant") return;
    const before = messages.slice(0, index);
    if (!before.length) return;
    setBusy(true); setError(""); setMessages([...before, { role: "assistant", content: "" }]);
    try { await enqueueChat(before, messages[index].requestExtra ?? {}, index === messages.length - 1 ? selectedChatId : null); }
    catch (reason) {
      setBusy(false); setMessages(before);
      setError(reason instanceof Error ? reason.message : "Yanıt yeniden üretilemedi.");
    }
  };

  const handleFiles = async (files: FileList | File[] | null) => {
    if (!files?.length) return;
    const { accepted, errors } = acceptedChatFiles(files, images.length);
    if (errors.length) setError(errors.join(" "));
    if (!accepted.length) return;
    setImageBusy(true);
    try {
      const next = await readImages(accepted);
      setImages((current) => [...current, ...next].slice(0, MAX_IMAGES));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Görsel eklenemedi.");
    } finally {
      setImageBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const openLibrary = async () => {
    const opening = !libraryOpen;
    setLibraryOpen(opening);
    if (!opening) return;
    try {
      const data = await api.get<JsonRecord>("/api/kutuphane");
      setLibrary(Array.isArray(data.fotograflar) ? data.fotograflar as LibraryImage[] : []);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Kütüphane yüklenemedi."); }
  };

  const selectLibraryImage = async (image: LibraryImage) => {
    if (images.length >= MAX_IMAGES) { setError("Bir mesaja en fazla 6 görsel eklenebilir."); return; }
    setImageBusy(true);
    try {
      const response = await fetch(image.url, { credentials: "same-origin" });
      if (!response.ok) throw new Error("Kütüphane görseli yüklenemedi.");
      const blob = await response.blob();
      if (blob.size > MAX_IMAGE_BYTES) throw new Error("Görsel 6 MB sınırını aşıyor.");
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error("Görsel okunamadı.")); reader.readAsDataURL(blob);
      });
      setImages((current) => current.length < MAX_IMAGES ? [...current, { name: image.ad, dataUrl }] : current);
      setLibraryOpen(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Kütüphane görseli yüklenemedi."); }
    finally { setImageBusy(false); }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if ((event.clipboardData.getData("text/plain") || "").trim()) return;
    const files = Array.from(event.clipboardData.items).map((item) => item.getAsFile()).filter((file): file is File => !!file && file.type.startsWith("image/"));
    if (!files.length) return;
    event.preventDefault();
    void handleFiles(files);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault(); setDragging(false);
    void handleFiles(event.dataTransfer.files);
  };

  return <section className="chat-workspace" aria-labelledby="chat-title">
    <aside className={`chat-sidebar${historyOpen ? " chat-sidebar--open" : ""}`} aria-label="Sohbet geçmişi">
      <div className="chat-sidebar__head"><div><p className="eyebrow">ÇALIŞMA ALANI</p><h2>Sohbetler</h2></div><button className="chat-icon-btn chat-sidebar__close" onClick={() => setHistoryOpen(false)} aria-label="Sohbet listesini kapat"><X size={17}/></button></div>
      <button className="chat-new" onClick={newChat} disabled={busy}>＋ Yeni sohbet</button>
      <label className="chat-search"><span className="sr-only">Sohbet ara</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Sohbet ara…" /></label>
      <div className="chat-list" aria-live="polite">
        {loading && <p className="chat-empty">Sohbetler yükleniyor…</p>}
        {!loading && history.length === 0 && <p className="chat-empty">{search ? "Eşleşen sohbet yok." : "Henüz kaydedilmiş sohbet yok."}</p>}
        {history.map((chat) => <div className={`chat-list-item${selectedChatId === chat.id ? " chat-list-item--active" : ""}`} key={chat.id}>
          <button className="chat-list-item__open" onClick={() => { void selectChat(chat.id); setHistoryOpen(false); }} disabled={busy}>
            <span aria-hidden="true">◌</span><span>{titleFor(chat)}</span>
          </button>
          <button className="chat-icon-btn chat-list-item__delete" onClick={() => void deleteChat(chat.id)} aria-label={`${titleFor(chat)} sohbetini sil`} disabled={busy}>×</button>
        </div>)}
      </div>
      <div className="chat-sidebar__foot">Sohbetler bu kullanıcı kasasına kaydedilir.</div>
    </aside>

    <div className="chat-main">
      <header className="chat-heading">
        <button className="chat-icon-btn chat-history-toggle" onClick={() => setHistoryOpen(true)} aria-label="Sohbet geçmişini aç">☰</button>
        <div><p className="eyebrow">METİN VE GÖRSELLE ÇALIŞ</p><h1 id="chat-title">Sohbet stüdyosu</h1></div>
        <label className="chat-model"><span>LLM</span><select aria-label="Sohbet LLM modeli" value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={busy}>
          {!modelOptions.length && <option value="">Model bilgisi yükleniyor</option>}
          {modelOptions.map((model) => <option key={model.id} value={model.id}>{model.ad}{model.hazir ? "" : " · kurulu değil"}</option>)}
        </select></label>
      </header>

      {error && <div className="chat-alert" role="alert">{error}</div>}
      {notice && <p className="chat-notice" role="status">{notice}</p>}

      <div className="chat-transcript" aria-live="polite" aria-label="Sohbet mesajları">
        {messages.length === 0 && <div className="chat-welcome"><span aria-hidden="true">✳</span><p className="eyebrow">SOHBET STÜDYOSU</p><h2>Bir fikirle başlayalım</h2><p>Model, görsel referanslarını ve mesaj geçmişini birlikte kullanabilir.</p><div className="chat-suggestions"><button onClick={() => setDraft("Bu fikri daha ayrıntılı anlat: ")}>Bir fikri geliştir</button><button onClick={() => setDraft("Bu görselde neler görüyorsun?")}>Bir görseli incele</button></div></div>}
        {messages.map((message, index) => <article className={`chat-message chat-message--${message.role}`} key={`${index}-${message.role}`}>
          <div className="chat-message__avatar" aria-hidden="true">{message.role === "assistant" ? "✳" : "S"}</div>
          <div className="chat-message__body"><div className="chat-message__label">{message.role === "assistant" ? "Asistan" : "Sen"}</div>
            {message.images?.map((image, imageIndex) => <img className="chat-message__image" src={image} alt={`Eklenen görsel ${imageIndex + 1}`} key={imageIndex}/>)}
            {message.role === "assistant" && index === messages.length - 1 && busy && !message.content
              ? <div className="chat-thinking"><LoaderCircle size={15} className="chat-spinner"/> Yanıt hazırlanıyor…</div>
              : <p className="chat-message__text">{message.content}</p>}
            {message.thinking && <details className="chat-thinking-details"><summary>Düşünme kaydı</summary><p>{message.thinking}</p></details>}
            {message.role === "assistant" && message.content && !busy && <button className="chat-message__retry" onClick={() => void regenerate(index)}>↻ Yeniden üret</button>}
          </div>
        </article>)}
        {activeJob && <div className="chat-job-state" role="status"><LoaderCircle size={14} className="chat-spinner"/>{activeJob.message}</div>}
      </div>

      <div className={`chat-composer-wrap${dragging ? " chat-composer-wrap--dragging" : ""}`} onDragEnter={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); setDragging(true); } }} onDragOver={(event) => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }} onDrop={onDrop}>
        {images.length > 0 && <div className="chat-attachments" aria-label="Eklenecek görseller">{images.map((image, index) => <div className="chat-attachment" key={`${image.name}-${index}`}>
          <img src={image.dataUrl} alt=""/><span>{image.name}</span><button className="chat-icon-btn" onClick={() => setImages((current) => current.filter((_, itemIndex) => itemIndex !== index))} aria-label={`${image.name} görselini kaldır`}><X size={14}/></button>
        </div>)}</div>}
        {libraryOpen && <div className="chat-library" role="dialog" aria-label="Kütüphaneden görsel seç">{library.length ? library.map((item) => <button key={item.ad} onClick={() => void selectLibraryImage(item)} disabled={imageBusy || images.length >= MAX_IMAGES}><img src={item.thumb || item.url} alt=""/><span>{item.ad}</span></button>) : <p>Kütüphanede görsel yok.</p>}</div>}
        {dragging && <div className="chat-drop-hint">Görselleri mesaja eklemek için bırak</div>}
        <div className="chat-composer"><label className="sr-only" htmlFor="chat-message">Mesaj</label><textarea id="chat-message" rows={2} placeholder="Mesajını yaz…" value={draft} onChange={(event) => setDraft(event.target.value)} onPaste={onPaste} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} disabled={busy}/>
          <div className="chat-composer__actions"><input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={(event) => void handleFiles(event.target.files)}/>
            <button className="chat-icon-btn" onClick={() => fileInput.current?.click()} aria-label="Sohbete görsel ekle" disabled={busy || imageBusy || images.length >= MAX_IMAGES}>＋</button>
            {images.length > 0 && <span className="chat-composer__hint">{images.length}/{MAX_IMAGES} görsel</span>}
            <button className="chat-text-action" onClick={() => void openLibrary()} disabled={busy || imageBusy || images.length >= MAX_IMAGES}>Kütüphaneden ekle</button>
            <button className="chat-text-action" onClick={() => void convertChatToPrompt()} disabled={busy || !messages.length}>Sohbetten prompt üret</button>
            {busy ? <button className="chat-send chat-send--stop" onClick={() => void cancelJob()} disabled={!activeJob || activeJob.state === "stopping"} aria-label="Yanıtı durdur"><Square size={14} fill="currentColor"/> Durdur</button> : <button className="chat-send" onClick={() => void send()} disabled={(!draft.trim() && !images.length) || imageBusy}>↑ Gönder</button>}
          </div>
        </div>
      </div>
    </div>
    {historyOpen && <button className="chat-scrim" aria-label="Sohbet geçmişini kapat" onClick={() => setHistoryOpen(false)}/>}
  </section>;
}
