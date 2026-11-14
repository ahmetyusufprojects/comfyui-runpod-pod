import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PromptStudio } from "./PromptStudio";
import type { ProductionApi } from "../../lib/api/client";

afterEach(() => { cleanup(); localStorage.clear(); });

function fakeApi(overrides: Partial<ProductionApi> = {}): ProductionApi {
  return {
    setup: vi.fn().mockResolvedValue({}),
    me: vi.fn().mockResolvedValue({ kullanici: "synthetic-user" }),
    vault: vi.fn().mockResolvedValue({}),
    status: vi.fn().mockResolvedValue({}),
    settings: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockResolvedValue({}),
    get: vi.fn().mockImplementation(async (path: string) => {
      if (path === "/api/durum") return { llm_models: [{ id: "llm-test", ad: "Synthetic LLM", hazir: true }] };
      if (path === "/api/promptlar") return { promptlar: [] };
      if (path.startsWith("/api/prompt-gecmisi")) return { kayitlar: [] };
      if (path === "/api/isler") return { isler: [] };
      return {};
    }),
    ...overrides,
  };
}

describe("Prompt Studio kayıtları ve yeniden bağlanma", () => {
  it("yüklenen kayıtları üretim değerleriyle uygular ve /api/promptlar/sil ile siler", async () => {
    const onApply = vi.fn();
    const api = fakeApi({
      get: vi.fn().mockImplementation(async (path: string) => {
        if (path === "/api/durum") return { llm_models: [{ id: "llm-test", ad: "Synthetic LLM", hazir: true }] };
        if (path === "/api/promptlar") return { promptlar: [{ ad: "Kare portre", prompt: "Soft portrait", negative: "blur", model: "krea2", steps: 24, width: 896, height: 1152 }] };
        if (path.startsWith("/api/prompt-gecmisi")) return { kayitlar: [] };
        if (path === "/api/isler") return { isler: [] };
        return {};
      }),
    });
    render(<PromptStudio api={api} onApply={onApply}/>);
    expect(await screen.findByText("Kare portre")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Uygula" }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({
      positive: "Soft portrait", negative: "blur", model: "krea2", steps: 24, width: 896, height: 1152,
    }));
    fireEvent.click(screen.getByRole("button", { name: "Kare portre kaydını sil" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/promptlar/sil", { ad: "Kare portre" }));
    await waitFor(() => expect(screen.queryByText("Kare portre")).not.toBeInTheDocument());
  });

  it("kayıt yaparken parent'ın verdiği üretim alanlarını kullanır", async () => {
    const post = vi.fn().mockImplementation(async (path: string) => path === "/api/prompt-uret" ? { is_id: "new-prompt" } : {});
    const get = vi.fn().mockImplementation(async (path: string) => {
      if (path === "/api/durum") return { llm_models: [{ id: "llm-test", ad: "Synthetic LLM", hazir: true }] };
      if (path === "/api/promptlar") return { promptlar: [] };
      if (path.startsWith("/api/prompt-gecmisi")) return { kayitlar: [] };
      if (path === "/api/isler") return { isler: [] };
      if (path === "/api/is/new-prompt") return { durum: "bitti", sonuc: { positive: "A tiny meadow", negative: "text" } };
      return {};
    });
    const api = fakeApi({ post, get });
    render(<PromptStudio api={api} presetDefaults={{ model: "krea2-instance", workflow: "krea2", steps: 31, cfg: 3.4, sampler: "euler", width: 1216, height: 832, max_side: 1536, loras: [{ id: "soft-light", weight: 0.8 }] }}/>);
    await waitFor(() => expect(screen.getByLabelText("Prompt LLM modeli")).toHaveValue("llm-test"));
    fireEvent.change(screen.getByLabelText("İsteğin"), { target: { value: "meadow" } });
    fireEvent.click(screen.getByRole("button", { name: "＋ Sıraya ekle" }));
    fireEvent.click(screen.getByRole("button", { name: /Kuyruğu başlat/ }));
    await screen.findByDisplayValue("A tiny meadow");
    expect(post).toHaveBeenCalledWith("/api/prompt-uret", {
      mod: "fikir", metin: "meadow", dil: "en", yaraticilik: 0.7, gorseller: [],
      model_prompt_profile: "auto", llm_model: "llm-test", bbox_mode: false,
    });
    fireEvent.change(screen.getByLabelText("Prompt kayıt adı"), { target: { value: "Test preset" } });
    fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/promptlar", {
      ad: "Test preset", prompt: "A tiny meadow", negative: "text", model: "krea2-instance", workflow: "krea2",
      steps: 31, cfg: 3.4, sampler: "euler", width: 1216, height: 832, max_side: 1536, loras: [{ id: "soft-light", weight: 0.8 }],
    }));
  });

  it("remount sonrasında active llm_prompt işine bağlanır ve localStorage id'sini temizler", async () => {
    localStorage.setItem("llm_is_llm_prompt_synthetic-user", "restored-task");
    const api = fakeApi({
      get: vi.fn().mockImplementation(async (path: string) => {
        if (path === "/api/durum") return { llm_models: [] };
        if (path === "/api/promptlar") return { promptlar: [] };
        if (path.startsWith("/api/prompt-gecmisi")) return { kayitlar: [] };
        if (path === "/api/isler") return { isler: [{ is_id: "restored-task", tur: "llm_prompt", durum: "calisiyor" }] };
        if (path === "/api/is/restored-task") return { durum: "bitti", sonuc: { positive: "Restored prompt" } };
        return {};
      }),
    });
    render(<PromptStudio api={api}/>);
    expect(await screen.findByText("Restored prompt")).toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem("llm_is_llm_prompt_synthetic-user")).toBeNull());
  });
});
