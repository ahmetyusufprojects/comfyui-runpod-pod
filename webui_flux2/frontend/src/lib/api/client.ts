export type WebUiPort = 8189 | 8192;

export type JsonRecord = Record<string, unknown>;

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
    credentials: "same-origin",
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body?.hata === "string" ? body.hata : `Sunucu HTTP ${response.status} yanıtı verdi.`);
  return body as T;
}

export interface ModelInstance {
  id: string; family: string; name: string; group?: string; engine?: string; ready: boolean;
  primary_file?: string;
}
export interface ProductionApi {
  setup(signal?: AbortSignal): Promise<JsonRecord>;
  me(signal?: AbortSignal): Promise<JsonRecord>;
  vault(signal?: AbortSignal): Promise<JsonRecord>;
  status(signal?: AbortSignal): Promise<JsonRecord>;
  settings(signal?: AbortSignal): Promise<JsonRecord>;
  post<T = JsonRecord>(path: string, data: JsonRecord): Promise<T>;
  get<T = JsonRecord>(path: string, signal?: AbortSignal): Promise<T>;
}
export const productionApi: ProductionApi = {
  setup: (signal) => getJson("/api/kurulum-durumu", signal),
  me: (signal) => getJson("/api/ben", signal),
  vault: (signal) => getJson("/api/kasa/durum", signal),
  status: (signal) => getJson("/api/durum", signal),
  settings: (signal) => getJson("/api/ayarlar", signal),
  post: (path, data) => requestJson(path, { method: "POST", body: JSON.stringify(data) }),
  get: (path, signal) => getJson(path, signal),
};

export interface MainHealth {
  hazir: boolean;
  pid?: number;
}

export interface SetupHealth {
  hazir: boolean;
  hata: boolean;
  parolasiz: boolean;
  storage_mode?: string;
  storage_persistent?: boolean;
}

export type HealthStatus = MainHealth | SetupHealth;

export interface StatusApi {
  health(port: WebUiPort, signal?: AbortSignal): Promise<HealthStatus>;
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
    signal,
  });
  if (!response.ok) throw new Error(`Sunucu HTTP ${response.status} yanıtı verdi.`);
  return (await response.json()) as T;
}

export const statusApi: StatusApi = {
  health(port, signal) {
    const path = port === 8192 ? "/api/setup-state" : "/api/sunucu-hazir";
    return getJson<HealthStatus>(path, signal);
  },
};

export function getWebUiPort(location: Pick<Location, "port"> & Partial<Pick<Location, "search" | "hostname">> = window.location): WebUiPort {
  if (location.port === "8192") return 8192;
  if (location.port === "8189") return 8189;
  if (location.hostname?.match(/-8192\.proxy\.runpod\.net$/i)) return 8192;
  if (location.hostname?.match(/-8189\.proxy\.runpod\.net$/i)) return 8189;
  const surface = new URLSearchParams(location.search ?? "").get("surface");
  if (surface === "setup") return 8192;
  if (surface === "main") return 8189;
  return import.meta.env.VITE_TARGET_PORT === "8192" ? 8192 : 8189;
}
