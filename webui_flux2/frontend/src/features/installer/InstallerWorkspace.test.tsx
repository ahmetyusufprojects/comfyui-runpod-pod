import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InstallerWorkspace, type InstallerApi } from "./InstallerWorkspace";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
});
