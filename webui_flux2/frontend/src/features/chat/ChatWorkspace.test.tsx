import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ProductionApi } from "../../lib/api/client";
import { acceptedChatFiles, ChatWorkspace, estimateContextTokens, restoreSavedMessage } from "./ChatWorkspace";

afterEach(() => cleanup());

function fakeApi(overrides: Partial<ProductionApi> = {}): ProductionApi {
  return {
    setup: vi.fn().mockResolvedValue({}), me: vi.fn().mockResolvedValue({}), vault: vi.fn().mockResolvedValue({}),
    status: vi.fn().mockResolvedValue({}), settings: vi.fn().mockResolvedValue({}),
    post: vi.fn().mockImplementation((path: string) => {
      if (path === "/api/sohbetler") return Promise.resolve({ sohbetler: [{ id: "chat-1", mesajlar: [{ role: "user", content: "Bir sahne" }] }] });
      if (path.startsWith("/api/sohbetler?id=")) return Promise.resolve({ id: "chat-1", mesajlar: [
        { role: "user", content: [{ type: "text", text: "Bir sahne" }, { type: "image_url", image_url: { url: "data:image/png;base64,AA==" } }] },
        { role: "assistant", content: "Eski cevap" },
      ] });
      return Promise.resolve({ is_id: "job-1" });
    }),
    get: vi.fn().mockImplementation((path: string) => path === "/api/isler" ? Promise.resolve({ isler: [] }) : Promise.resolve({ durum: "calisiyor" })),
    ...overrides,
  };
}

describe("React sohbet alanı", () => {
  it("kayıtlı sohbet açıldığında multimodal içerikteki görseli geri getirir", async () => {
    const api = fakeApi();
    render(<ChatWorkspace api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Bir sahne" }));
    expect(await screen.findByAltText("Eklenen görsel 1")).toHaveAttribute("src", "data:image/png;base64,AA==");
  });

  it("sohbet modeli seçimini yenilemeler arasında saklar", async () => {
    const api = fakeApi({ get: vi.fn().mockImplementation((path: string) => path === "/api/durum" ? Promise.resolve({ llm_models: [
      { id: "llm-a", ad: "Model A", hazir: true }, { id: "llm-b", ad: "Model B", hazir: true },
    ] }) : path === "/api/isler" ? Promise.resolve({ isler: [] }) : Promise.resolve({})) });
    render(<ChatWorkspace api={api}/>);
    const model = await screen.findByLabelText("Sohbet LLM modeli");
    fireEvent.change(model, { target: { value: "llm-b" } });
    expect(localStorage.getItem("sohbet_llm_model")).toBe("llm-b");
    expect(estimateContextTokens([{ role: "user", content: "abcd" }])).toBe(49);
    expect(screen.getByText("tahmini token")).toBeInTheDocument();
  });

  it("asistan yanıtını kopyalama eylemi sunar", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const api = fakeApi();
    render(<ChatWorkspace api={api}/>);
    fireEvent.click(await screen.findByRole("button", { name: "Bir sahne" }));
    fireEvent.click(await screen.findByRole("button", { name: "⧉ Kopyala" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Eski cevap"));
  });

  it("6 MiB üstü görseli reddeder ve altı görsel sınırını birleştirerek uygular", () => {
    const tooLarge = new File([new Uint8Array(6 * 1024 * 1024 + 1)], "large.png", { type: "image/png" });
    const accepted = new File(["x"], "ok.png", { type: "image/png" });
    const result = acceptedChatFiles([tooLarge, accepted], 5);
    expect(result.accepted).toEqual([accepted]);
    expect(result.errors.join(" ")).toContain("6 MB");
    expect(acceptedChatFiles([accepted], 6).accepted).toHaveLength(0);
  });

  it("yeniden açıldığında devam eden LLM sohbet işine bağlanır", async () => {
    const api = fakeApi({ get: vi.fn().mockImplementation((path: string) => {
      if (path === "/api/isler") return Promise.resolve({ isler: [{ tur: "llm_chat", durum: "calisiyor", is_id: "recovered-job" }] });
      if (path === "/api/is/recovered-job") return Promise.resolve({ durum: "bitti", sonuc: { yanit: "Devam eden cevap" } });
      return Promise.resolve({});
    }) });
    render(<ChatWorkspace api={api} />);
    expect(await screen.findByText("Devam eden cevap")).toBeInTheDocument();
  });

  it("sohbetten prompt üretimini legacy ek_talimat alanıyla kuyruğa verir", async () => {
    const api = fakeApi();
    render(<ChatWorkspace api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Bir sahne" }));
    fireEvent.click(await screen.findByRole("button", { name: "Sohbetten prompt üret" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/sohbet", expect.objectContaining({
      mesajlar: expect.any(Array), ek_talimat: expect.stringContaining("Aspect ratio"), sohbet_id: "chat-1",
    })));
  });

  it("asistan yanıtını önceki mesajlardan yeniden üretir", async () => {
    const api = fakeApi();
    render(<ChatWorkspace api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Bir sahne" }));
    fireEvent.click(await screen.findByRole("button", { name: "↻ Yeniden üret" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/sohbet", expect.objectContaining({
      sohbet_id: "chat-1", mesajlar: [expect.objectContaining({ role: "user" })],
    })));
  });

  it("kaydedilmiş image_url parçalarını metinden ayrı şekilde ayıklar", () => {
    expect(restoreSavedMessage({ role: "user", content: [
      { type: "text", text: "bak" }, { type: "image_url", image_url: { url: "data:image/jpeg;base64,AA" } },
    ] as unknown as string })).toMatchObject({ content: "bak", images: ["data:image/jpeg;base64,AA"] });
  });
});
