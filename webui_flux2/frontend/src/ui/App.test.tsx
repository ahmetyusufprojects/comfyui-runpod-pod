import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { App } from "./App";
import type { StatusApi } from "../lib/api/client";

afterEach(() => cleanup());

describe("port bazlı sistem durumu", () => {
  it("yükleme durumunu gösterir", () => {
    const api: StatusApi = { health: () => new Promise(() => undefined) };
    render(<App api={api} port={8189} />);
    expect(screen.getByText("Sunucudan durum alınıyor…")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "WebUI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Durumu yenile" })).toBeDisabled();
    expect(within(screen.getByRole("navigation", { name: "Uygulama bölümleri" }))
      .getByRole("link", { name: /Ana WebUI/ })).toHaveAttribute("aria-current", "page");
  });

  it("8189 sağlık yanıtını hazır görünümüne bağlar", async () => {
    const api: StatusApi = { health: vi.fn().mockResolvedValue({ hazir: true, pid: 17 }) };
    render(<App api={api} port={8189} pollMs={60_000} />);
    expect(await screen.findByText("Bağlantı kuruldu")).toBeInTheDocument();
    expect(screen.getByText("WebUI çalışıyor ve durum bilgisi güncel.")).toBeInTheDocument();
    expect(api.health).toHaveBeenCalledWith(8189, expect.any(AbortSignal));
  });

  it("8192 setup yanıtındaki storage durumunu gösterir", async () => {
    const api: StatusApi = {
      health: vi.fn().mockResolvedValue({ hazir: true, hata: false, parolasiz: true, storage_mode: "ephemeral", storage_persistent: false }),
    };
    render(<App api={api} port={8192} pollMs={60_000} />);
    expect(await screen.findByText("Depolama: ephemeral · geçici")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Kurucu" })).toBeInTheDocument();
    expect(api.health).toHaveBeenCalledWith(8192, expect.any(AbortSignal));
  });

  it("API hatasında tekrar deneme düğmesini gösterir", async () => {
    const api: StatusApi = { health: vi.fn().mockRejectedValue(new Error("Sunucu HTTP 503 yanıtı verdi.")) };
    render(<App api={api} port={8189} pollMs={60_000} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Sunucu HTTP 503 yanıtı verdi.");
    expect(screen.getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
  });
});
