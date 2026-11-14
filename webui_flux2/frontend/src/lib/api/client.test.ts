import { afterEach, describe, expect, it, vi } from "vitest";
import { getWebUiPort, statusApi } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("status API client", () => {
  it("8189 için ana sağlık uç noktasını çağırır", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ hazir: true, pid: 42 }) });
    vi.stubGlobal("fetch", fetchMock);
    await expect(statusApi.health(8189)).resolves.toEqual({ hazir: true, pid: 42 });
    expect(fetchMock).toHaveBeenCalledWith("/api/sunucu-hazir", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("8192 için kurucu sağlık uç noktasını çağırır", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ hazir: true, hata: false, parolasiz: true }) });
    vi.stubGlobal("fetch", fetchMock);
    await expect(statusApi.health(8192)).resolves.toMatchObject({ hazir: true, hata: false });
    expect(fetchMock).toHaveBeenCalledWith("/api/setup-state", expect.any(Object));
  });

  it("tarayıcı portu ve geliştirme yönlendirme parametresini algılar", () => {
    expect(getWebUiPort({ port: "8192" })).toBe(8192);
    expect(getWebUiPort({ port: "8189" })).toBe(8189);
    expect(getWebUiPort({ port: "5173", search: "?surface=setup" })).toBe(8192);
    expect(getWebUiPort({ port: "", hostname: "abc-8192.proxy.runpod.net" })).toBe(8192);
    expect(getWebUiPort({ port: "", hostname: "abc-8189.proxy.runpod.net" })).toBe(8189);
  });
});
