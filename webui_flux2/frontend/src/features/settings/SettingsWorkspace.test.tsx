import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsWorkspace } from "./SettingsWorkspace";
import type { ProductionApi } from "../../lib/api/client";

afterEach(() => { cleanup(); });

function fakeApi(overrides: Partial<ProductionApi> = {}): ProductionApi {
  return {
    setup: vi.fn().mockResolvedValue({}), me: vi.fn().mockResolvedValue({}), vault: vi.fn().mockResolvedValue({ bekleme_dk: 30 }),
    status: vi.fn().mockResolvedValue({ llm_models: [{ id: "llm-test", ad: "Synthetic LLM", hazir: true, mtp_dosyasi: "mtp.gguf", gorsel_dosyasi: "mmproj.gguf", tuning_defaults: { context: 4096 } }] }),
    settings: vi.fn().mockResolvedValue({ ayarlar: { llm_model: "llm-test", llm_ctx: 8192 }, llm_varsayilanlar: { sistem: "Prompt default", sohbet: "Chat default", mod: { fikir: "Fikir default", gorsel: "Görsel default", birlestir: "Birleştir default", ceviri: "Çeviri default", sohbet: "Sohbet default" } } }),
    post: vi.fn().mockResolvedValue({ ayarlar: {} }), get: vi.fn().mockResolvedValue({}),
    ...overrides,
  } as ProductionApi;
}

describe("React ayarları", () => {
  it("genel bağlamı ve gelişmiş LLM ayarlarını mevcut API sözleşmesiyle kaydeder", async () => {
    const api = fakeApi();
    render(<SettingsWorkspace api={api} userName="synthetic-user"/>);
    fireEvent.click(await screen.findByRole("button", { name: "LLM" }));
    fireEvent.change(screen.getByLabelText("Genel bağlam sınırı"), { target: { value: "12000" } });
    fireEvent.change(screen.getByLabelText("Modele özel bağlam"), { target: { value: "6144" } });
    fireEvent.change(screen.getByLabelText("Düşünme modu"), { target: { value: "off" } });
    fireEvent.change(screen.getByLabelText("K cache türü"), { target: { value: "q8_0" } });
    fireEvent.change(screen.getByLabelText(/MTP taslak tokeni/), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Ayarları kaydet" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/ayarlar", expect.objectContaining({
      llm_model: "llm-test", llm_ctx: 12000,
      llm_model_tuning: { "llm-test": expect.objectContaining({ context: 6144, reasoning: "off", cache_type_k: "q8_0", mtp_draft_tokens: 5 }) },
    })));
  });

  it("varsayılan sistem promptlarını özel kullanıcı talimatı gibi kaydetmez", async () => {
    const api = fakeApi();
    render(<SettingsWorkspace api={api} userName="synthetic-user"/>);
    fireEvent.click(await screen.findByRole("button", { name: "Promptlar" }));
    fireEvent.click(screen.getByRole("button", { name: "Tercihleri kaydet" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/ayarlar", expect.objectContaining({
      llm_sistem: "", llm_sohbet_sistem: "",
      llm_mod: { fikir: "", gorsel: "", birlestir: "", ceviri: "", sohbet: "" },
    })));
  });
});
