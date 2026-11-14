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
