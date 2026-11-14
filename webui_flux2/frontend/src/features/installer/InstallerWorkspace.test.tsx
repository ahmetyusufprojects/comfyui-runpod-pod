import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InstallerWorkspace, type InstallerApi } from "./InstallerWorkspace";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear(); });

function fakeApi(): InstallerApi {
  const get = vi.fn(async (path: string) => {
    if (path === "/api/setup-state") return { hazir: true, parolasiz: true, storage_persistent: false };
    if (path === "/api/pod-kurulum") return { gruplar: [] };
    if (path === "/api/system-settings") return {
      ayarlar: {}, gruplar: [], lora_durum: [],
      model_families: [{ id: "chat_model", name: "Sohbet modeli" }],
      model_instances: [{
        id: "chat_model", family: "chat_model", name: "Mevcut model", ready: false,
        primary_file: "main.gguf",
        files: [
          { name: "main.gguf", optional: false },
          { name: "mtp.gguf", optional: true },
          { name: "mmproj.gguf", optional: true },
        ],
      }],
    };
    return {};
  });
  const post = vi.fn(async (_path: string, _data: Record<string, unknown>) => ({ saved: true }));
  return { get, post } as unknown as InstallerApi;
}

describe("React kurucu alanı", () => {
  it("LoRA listesini yalnızca kurulu görsel model aileleriyle sınırlar", async () => {
    const api = fakeApi();
    api.get = vi.fn(async (path: string) => {
      if (path === "/api/setup-state") return { hazir: true, parolasiz: true };
      if (path === "/api/pod-kurulum") return { gruplar: [{ id: "zimage", ad: "Z-Image" }, { id: "krea2", ad: "Krea 2" }] };
      if (path === "/api/system-settings") return {
        ayarlar: { loras: [
          { id: "lora-z", name: "Z LoRA", model: "z_image", enabled: true },
          { id: "lora-flux", name: "Flux LoRA", model: "flux3", enabled: true },
        ] },
        gruplar: [{ id: "zimage", label: "Z-Image", files: [] }, { id: "krea2", label: "Krea 2", files: [] }],
        lora_durum: [], model_families: [], model_instances: [],
      };
      return {};
    }) as unknown as InstallerApi["get"];
    render(<InstallerWorkspace api={api}/>);
    await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" });
    fireEvent.click(screen.getByRole("button", { name: "Genel ayarlar" }));
    fireEvent.click(await screen.findByRole("button", { name: "LoRA" }));
    expect(await screen.findByText("Z LoRA")).toBeInTheDocument();
    expect(screen.queryByText("Flux LoRA")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Z-Image" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Krea 2" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Flux 3 Klein" })).not.toBeInTheDocument();
  });

  it("yeni yedek parolasında sekiz karakter şartını gösterir", async () => {
    const api = fakeApi();
    render(<InstallerWorkspace api={api}/>);
    await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" });
    fireEvent.click(screen.getByRole("button", { name: "Yedekler" }));
    const password = (await screen.findAllByLabelText("Yedek parolası"))[0];
    expect(password).toHaveAttribute("minLength", "8");
    expect(screen.getByRole("button", { name: "Şifreli dosyayı indir" })).toBeDisabled();
    fireEvent.change(password, { target: { value: "12345678" } });
    expect(screen.getByRole("button", { name: "Şifreli dosyayı indir" })).toBeEnabled();
  });

  it("isteğe bağlı model dosyalarını seçime göre API'ye gönderir", async () => {
    const api = fakeApi();
    render(<InstallerWorkspace api={api}/>);
    await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" });
    fireEvent.click(screen.getByRole("button", { name: "Genel ayarlar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Modeller" }));
    fireEvent.change(await screen.findByLabelText("Model ailesi"), { target: { value: "chat_model" } });
    fireEvent.change(screen.getByLabelText("main.gguf"), { target: { value: "https://huggingface.co/team/model/resolve/main/model.gguf" } });
    fireEvent.click(screen.getByLabelText("mmproj.gguf dosyasını ekle (isteğe bağlı)"));
    fireEvent.change(screen.getByLabelText("mmproj.gguf bağlantısı"), { target: { value: "https://huggingface.co/team/model/resolve/main/mmproj.gguf" } });
    fireEvent.change(screen.getByLabelText("Görünen ad"), { target: { value: "Sentetik model" } });
    fireEvent.click(screen.getByRole("button", { name: "Model kaydı oluştur" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/system-settings", expect.objectContaining({
      islem: "model_ekle",
      family: "chat_model",
      name: "Sentetik model",
      links: {
        "main.gguf": "https://huggingface.co/team/model/resolve/main/model.gguf",
        "mmproj.gguf": "https://huggingface.co/team/model/resolve/main/mmproj.gguf",
      },
      exclude_files: ["mtp.gguf"],
    })));
  });

  it("varsayılan indirme sırasını grup seçimini değiştirmeden yeniden sıralar", async () => {
    const api = fakeApi();
    const settings = {
      ayarlar: { auto_download: true, default_download_order: ["model-a", "model-b"] },
      gruplar: [{ id: "model-a", label: "Model A", files: [] }, { id: "model-b", label: "Model B", files: [] }],
      model_families: [], model_instances: [], lora_durum: [],
    };
    api.get = vi.fn(async (path: string) => {
      if (path === "/api/setup-state") return { hazir: true, parolasiz: true };
      if (path === "/api/pod-kurulum") return { gruplar: [] };
      if (path === "/api/system-settings") return settings;
      return {};
    }) as unknown as InstallerApi["get"];
    render(<InstallerWorkspace api={api}/>);
    await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" });
    fireEvent.click(screen.getByRole("button", { name: "Genel ayarlar" }));
    await screen.findByRole("heading", { name: "Varsayılan indirme sırası" });
    fireEvent.click(screen.getByRole("button", { name: "Model A aşağı taşı" }));
    fireEvent.click(screen.getByRole("button", { name: "Varsayılanları kaydet" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/system-settings", expect.objectContaining({
      islem: "kaydet", default_download_order: ["model-b", "model-a"],
    })));
  });

  it("varsayılan bağlantıyı yükleyince alandaki adresi de yeniler", async () => {
    const api = fakeApi();
    const initial = {
      ayarlar: {}, model_families: [], model_instances: [], lora_durum: [],
      gruplar: [{ id: "krea", label: "Krea", files: [{ name: "model.safetensors", link: "https://host/current.safetensors", default_link: "https://host/default.safetensors" }] }],
    };
    const reset = { ...initial, gruplar: [{ ...initial.gruplar[0], files: [{ ...initial.gruplar[0].files[0], link: "https://host/default.safetensors" }] }] };
    api.get = vi.fn(async (path: string) => {
      if (path === "/api/setup-state") return { hazir: true, parolasiz: true };
      if (path === "/api/pod-kurulum") return { gruplar: [] };
      if (path === "/api/system-settings") return initial;
      return {};
    }) as unknown as InstallerApi["get"];
    api.post = vi.fn(async (path: string) => path === "/api/system-settings" ? reset : {}) as unknown as InstallerApi["post"];
    render(<InstallerWorkspace api={api}/>);
    await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" });
    fireEvent.click(screen.getByRole("button", { name: "Genel ayarlar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Bağlantılar" }));
    const field = await screen.findByDisplayValue("https://host/current.safetensors");
    fireEvent.click(screen.getByRole("button", { name: "Varsayılan" }));
    await waitFor(() => expect(field).toHaveValue("https://host/default.safetensors"));
  });

  it("sistem panelinde kanal bilgisini app-update durumundan okur", async () => {
    const api = fakeApi();
    api.get = vi.fn(async (path: string) => {
      if (path === "/api/setup-state") return { hazir: true, parolasiz: true };
      if (path === "/api/pod-kurulum") return { gruplar: [] };
      if (path === "/api/app-update") return { kanal: "beta", status: "idle" };
      return {};
    }) as unknown as InstallerApi["get"];
    render(<InstallerWorkspace api={api}/>);
    await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" });
    fireEvent.click(screen.getByRole("button", { name: "Genel ayarlar" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sistem" }));
    expect(await screen.findByText("Kanal: beta")).toBeInTheDocument();
  });

  it("R2 geri yüklemede yerel yükleme tokenını yeniden kullanmaz", async () => {
    const api = fakeApi();
    const backup = { id: "r2-backup-1", name: "Sentetik R2", modified: "bugün", size: 7 };
    api.get = vi.fn(async (path: string) => {
      if (path === "/api/setup-state") return { hazir: true, parolasiz: true, storage_persistent: false };
      if (path === "/api/pod-kurulum") return { gruplar: [] };
      if (path === "/api/r2-backups") return { connected: true, backups: [backup] };
      return {};
    }) as unknown as InstallerApi["get"];
    const fileToken = "a".repeat(32);
    const file = new File(["payload"], "backup.wuis.gpg", { lastModified: 123 });
    window.localStorage.setItem(`webui-react-backup-upload:${file.name}:${file.size}:${file.lastModified}`, fileToken);
    const preparedTokens: string[] = [];
    const importedTokens: string[] = [];
    const response = (body: Record<string, unknown>, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/r2-backup-prepare-restore") {
        preparedTokens.push(String(JSON.parse(String(init?.body)).token));
        return response({ token: preparedTokens.at(-1) }, 202) as Response;
      }
      if (path === "/api/backup-import") {
        const token = String(JSON.parse(String(init?.body)).token);
        importedTokens.push(token);
        return token === fileToken ? response({ hata: "Parola yanlış" }, 400) as Response : response({ restored: true }) as Response;
      }
      if (path.startsWith("/api/backup-progress?token=")) return response({ status: "complete", stage: "Hazır", bytes: 7, total: 7 }) as Response;
      return response({}) as Response;
    }));
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<InstallerWorkspace api={api}/>);
    await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" });
    fireEvent.click(screen.getByRole("button", { name: "Yedekler" }));
    fireEvent.change(await screen.findByLabelText("Şifreli yedek dosyası"), { target: { files: [file] } });
    fireEvent.change(screen.getAllByLabelText("Yedek parolası")[1], { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Bu Pod’a geri yükle" }));
    expect(await screen.findByText(/Parola yanlış/)).toBeInTheDocument();
    expect(window.localStorage.getItem(`webui-react-backup-upload:${file.name}:${file.size}:${file.lastModified}`)).toBe(fileToken);

    fireEvent.click(screen.getByRole("button", { name: "Geri yükle" }));
    fireEvent.change(screen.getAllByLabelText("Yedek parolası").at(-1)!, { target: { value: "correct-password" } });
    fireEvent.click(screen.getByRole("button", { name: "İndir ve geri yükle" }));
    await waitFor(() => expect(importedTokens).toHaveLength(2));
    expect(preparedTokens).toHaveLength(1);
    expect(importedTokens[0]).toBe(fileToken);
    expect(importedTokens[1]).toBe(preparedTokens[0]);
    expect(importedTokens[1]).not.toBe(fileToken);
    expect(window.localStorage.getItem(`webui-react-backup-upload:${file.name}:${file.size}:${file.lastModified}`)).toBe(fileToken);
  });
});
