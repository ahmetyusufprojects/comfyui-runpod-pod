import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ProductionApi } from "../../lib/api/client";
import { GalleryWorkspace } from "./GalleryWorkspace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function fakeApi(overrides: Partial<ProductionApi> = {}): ProductionApi {
  return {
    setup: vi.fn().mockResolvedValue({ kurulu: true }),
    me: vi.fn().mockResolvedValue({}),
    vault: vi.fn().mockResolvedValue({ acik: true }),
    status: vi.fn().mockResolvedValue({}),
    settings: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({ silindi: true, silinen: 1 }),
    get: vi.fn().mockImplementation((path: string) => {
      if (path.startsWith("/api/arsiv")) return Promise.resolve({ toplam: 2, kayitlar: [
        { hash: "a".repeat(64), ad: "first.png", url: "/api/arsiv/gorsel/first", thumb: "/api/arsiv/thumb/first", boyut: [800, 600], bayt: 1000, kaynak_sayisi: 2, kaynaklar: [{ tur: "gecmis", ad: "sample.json" }] },
        { hash: "b".repeat(64), ad: "second.png", url: "/api/arsiv/gorsel/second", thumb: "/api/arsiv/thumb/second", boyut: [600, 800], bayt: 2000 },
      ] });
      if (path === "/api/kutuphane") return Promise.resolve({ fotograflar: [{ ad: "library.png", url: "/kutuphane/library.png", thumb: "/kutuphane/library.png?thumb=1", boyut: [512, 512], bayt: 500 }] });
      if (path.includes("album_id=")) return Promise.resolve({ toplam: 2, kayitlar: [
        { kayit: "album.json", dosya: "one.png", url: "/gecmis/one.png", prompt: "album prompt", tur: "goruntu", width: 512, height: 512, album_id: "c".repeat(64), album_index: 0 },
        { kayit: "album.json", dosya: "two.png", url: "/gecmis/two.png", prompt: "album prompt", tur: "goruntu", width: 512, height: 512, album_id: "c".repeat(64), album_index: 1 },
      ] });
      if (path.startsWith("/api/gecmis")) return Promise.resolve({ toplam: 1, kayitlar: [{ kayit: "album.json", dosya: "cover.png", url: "/gecmis/cover.png", prompt: "A two-image set", tur: "goruntu", album: true, album_id: "c".repeat(64), album_count: 2, album_index: 0 }] });
      return Promise.resolve({});
    }),
    ...overrides,
  };
}

describe("React galeri", () => {
  it("arşivi sayfalı yükler, seçim kipinde çoklu kaldırma isteği gönderir", async () => {
    const api = fakeApi();
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<GalleryWorkspace api={api} />);

    expect(await screen.findByRole("heading", { name: "Galeri" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "first.png ayrıntılarını aç" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Seç" }));
    fireEvent.click(screen.getByRole("button", { name: "first.png seç" }));
    fireEvent.click(screen.getByRole("button", { name: "second.png seç" }));
    fireEvent.click(screen.getByRole("button", { name: "Arşivden kaldır" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/arsiv/sil-coklu", { hashes: ["a".repeat(64), "b".repeat(64)] }));
  });

  it("arşivde sayfa değiştirirken önceki seçimleri korur", async () => {
    const firstHash = "a".repeat(64), secondHash = "b".repeat(64);
    const api = fakeApi({
      get: vi.fn().mockImplementation(async (path: string) => {
        if (path.startsWith("/api/arsiv?")) {
          const page = new URLSearchParams(path.split("?")[1]).get("sayfa");
          return page === "2"
            ? { toplam: 25, kayitlar: [{ hash: secondHash, ad: "page-two.png", url: "/page-two.png" }] }
            : { toplam: 25, kayitlar: [{ hash: firstHash, ad: "page-one.png", url: "/page-one.png" }] };
        }
        if (path === "/api/ayarlar") return {};
        return {};
      }),
    });
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<GalleryWorkspace api={api}/>);

    fireEvent.click(await screen.findByRole("button", { name: "Seç" }));
    fireEvent.click(await screen.findByRole("button", { name: "page-one.png seç" }));
    fireEvent.click(screen.getByRole("button", { name: "Sonraki sayfa" }));
    fireEvent.click(await screen.findByRole("button", { name: "page-two.png seç" }));
    expect(screen.getByText("2 görsel seçildi")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Arşivden kaldır" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/arsiv/sil-coklu", { hashes: [firstHash, secondHash] }));
  });

  it("kütüphane görselini açıp referans olarak aktarır", async () => {
    const api = fakeApi();
    const useReference = vi.fn();
    render(<GalleryWorkspace api={api} initialTab="library" onUseReference={useReference} />);

    fireEvent.click(await screen.findByRole("button", { name: "library.png ayrıntılarını aç" }));
    fireEvent.click(screen.getByRole("button", { name: "Üretimde kullan" }));

    expect(useReference).toHaveBeenCalledWith({ url: "/kutuphane/library.png", name: "library.png", width: 512, height: 512 });
  });

  it("albüm kartından sayfalı albüm görünümüne geçer", async () => {
    const api = fakeApi();
    render(<GalleryWorkspace api={api} initialTab="history" />);

    fireEvent.click(await screen.findByRole("button", { name: /A two-image set/ }));
    expect(await screen.findByRole("button", { name: "one.png ayrıntılarını aç" })).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("album_id="));
    expect(screen.getByRole("heading", { name: "A two-image set" })).toBeInTheDocument();
  });

  it("üretim geçmişi kaydını eski silme API sözleşmesiyle kaldırır", async () => {
    const api = fakeApi();
    vi.stubGlobal("confirm", vi.fn(() => true));
    api.get = vi.fn(async (path: string) => {
      if (path === "/api/ayarlar") return {};
      if (path.startsWith("/api/gecmis")) return { toplam: 1, kayitlar: [
        { kayit: "synthetic-job.json", dosya: "result.png", url: "/gecmis/result.png", prompt: "synthetic prompt", tur: "goruntu" },
      ] };
      return {};
    }) as ProductionApi["get"];
    render(<GalleryWorkspace api={api} initialTab="history"/>);
    fireEvent.click(await screen.findByRole("button", { name: "result.png ayrıntılarını aç" }));
    fireEvent.click(screen.getByRole("button", { name: "Geçmişten sil" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/gecmis/sil", { kayit: "synthetic-job.json" }));
  });

  it("üretim geçmişi görünüm tercihini kullanıcı ayarlarında saklar", async () => {
    const api = fakeApi();
    api.get = vi.fn(async (path: string) => {
      if (path === "/api/ayarlar") return { ayarlar: { ui: { history_view: "izgara" } } };
      if (path.startsWith("/api/gecmis")) return { toplam: 0, kayitlar: [] };
      return {};
    }) as ProductionApi["get"];
    render(<GalleryWorkspace api={api} initialTab="history"/>);
    fireEvent.click(await screen.findByRole("button", { name: "Akış" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/ayarlar", { ui: { history_view: "akis" } }));
  });

  it("albümün son görselinden sonraki albümün ilk görseline geçer", async () => {
    const firstId = "a".repeat(64), secondId = "b".repeat(64);
    const api = fakeApi({
      get: vi.fn().mockImplementation(async (path: string) => {
        if (path === "/api/ayarlar") return {};
        if (path.includes(`album_id=${firstId}`)) return { toplam: 1, kayitlar: [
          { kayit: "first.json", dosya: "first.png", url: "/gecmis/first.png", prompt: "First album", tur: "goruntu", album_id: firstId },
        ] };
        if (path.includes(`album_id=${secondId}`)) return { toplam: 1, kayitlar: [
          { kayit: "second.json", dosya: "second.png", url: "/gecmis/second.png", prompt: "Second album", tur: "goruntu", album_id: secondId },
        ] };
        if (path.startsWith("/api/gecmis")) return { toplam: 2, kayitlar: [
          { kayit: "first.json", dosya: "first.png", url: "/gecmis/first.png", prompt: "First album", tur: "goruntu", album: true, album_id: firstId, album_count: 1, album_index: 0 },
          { kayit: "second.json", dosya: "second.png", url: "/gecmis/second.png", prompt: "Second album", tur: "goruntu", album: true, album_id: secondId, album_count: 1, album_index: 1 },
        ] };
        return {};
      }),
    });
    render(<GalleryWorkspace api={api} initialTab="history"/>);
    fireEvent.click(await screen.findByRole("button", { name: /First album/ }));
    fireEvent.click(await screen.findByRole("button", { name: "first.png ayrıntılarını aç" }));
    fireEvent.click(screen.getByRole("button", { name: "Sonraki öğe" }));
    expect(await screen.findByRole("heading", { name: "second.png" })).toBeInTheDocument();
  });
});
