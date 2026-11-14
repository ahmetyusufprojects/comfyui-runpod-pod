import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "./App";
import type { ProductionApi } from "../lib/api/client";

afterEach(() => cleanup());

function makeApi(overrides: Partial<ProductionApi> = {}): ProductionApi {
  const getMock = vi.fn(async (path: string) => path === "/api/isler" ? { isler: [] } : {});
  const postMock = vi.fn(async () => ({}));
  const base: ProductionApi = {
    setup: vi.fn().mockResolvedValue({ kurulu: true }),
    me: vi.fn().mockResolvedValue({ kullanici: "sentetik-user", rol: "uye" }),
    vault: vi.fn().mockResolvedValue({ acik: true }),
    status: vi.fn().mockResolvedValue({ model_instances: [], llm_models: [] }),
    settings: vi.fn().mockResolvedValue({ ayarlar: {} }),
    get: getMock as unknown as ProductionApi["get"],
    post: postMock as unknown as ProductionApi["post"],
  };
  return { ...base, ...overrides };
}

describe("React çalışma alanı yönlendirmesi", () => {
  it("8189'da ilk hesap kurulumunu açar", async () => {
    const api = makeApi({ setup: vi.fn().mockResolvedValue({ kurulu: false }) });
    render(<App api={api} port={8189}/>);
    expect(await screen.findByRole("heading", { name: "İlk hesabını oluştur" })).toBeInTheDocument();
    expect(screen.getByLabelText("Kullanıcı adı")).toBeInTheDocument();
  });

  it("açık kasayla ana çalışma alanını ve gezinmeyi yükler", async () => {
    const api = makeApi();
    render(<App api={api} port={8189}/>);
    expect(await screen.findByRole("navigation", { name: "Çalışma alanı" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Üretim alanı" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "İş kuyruğu" }));
    expect(await screen.findByRole("heading", { name: "İş kuyruğu" })).toBeInTheDocument();
    expect(api.get).toHaveBeenCalledWith("/api/isler", expect.any(AbortSignal));
  });

  it("8192'de kurucu arayüzünü açar ve parolasız kurulumu gösterir", async () => {
    const api = makeApi({
      get: vi.fn(async (path: string) => {
        if (path === "/api/setup-state") return { hazir: true, parolasiz: true };
        if (path === "/api/pod-kurulum") return { gruplar: [], sirada: [] };
        return {};
      }) as unknown as ProductionApi["get"],
    });
    render(<App api={api} port={8192}/>);
    expect(await screen.findByRole("heading", { name: "Kurulum ve sistem yönetimi" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Model indirmeleri" })).toBeInTheDocument();
    expect(screen.getByText("Parolasız yönetici")).toBeInTheDocument();
  });
});
