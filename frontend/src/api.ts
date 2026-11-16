// Thin API client. The token lives in memory with a sessionStorage convenience copy (local dev only).
let token: string | null = null;
try { token = sessionStorage.getItem("tensor_token"); } catch { token = null; }

export function setToken(t: string | null) {
  token = t;
  try { t ? sessionStorage.setItem("tensor_token", t) : sessionStorage.removeItem("tensor_token"); } catch { /* ignore */ }
}
export const hasToken = () => !!token;

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function req<T>(method: string, path: string, body?: unknown, raw?: BodyInit, headers: Record<string, string> = {}): Promise<T> {
  const h: Record<string, string> = { ...headers };
  if (token) h.authorization = `Bearer ${token}`;
  if (body !== undefined) h["content-type"] = "application/json";
  const r = await fetch(path, { method, headers: h, body: body !== undefined ? JSON.stringify(body) : raw });
  if (r.status === 401) { setToken(null); location.hash = "#/login"; throw new ApiError(401, "Signed out"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) {
    const d = (data as { detail?: unknown })?.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg).join("; ") : `HTTP ${r.status}`;
    throw new ApiError(r.status, msg);
  }
  return data as T;
}

export const api = {
  get: <T = any>(p: string) => req<T>("GET", p),
  post: <T = any>(p: string, b: unknown = {}) => req<T>("POST", p, b),
  patch: <T = any>(p: string, b: unknown) => req<T>("PATCH", p, b),
  put: <T = any>(p: string, data: Blob) => req<T>("PUT", p, undefined, data),
  form: <T = any>(p: string, f: FormData) => req<T>("POST", p, undefined, f),
};

export async function uploadEvidence(engagementId: string, file: File, opts: { title?: string; requestId?: string; supersedesId?: string } = {}) {
  const s = await api.post("/v1/evidence/upload-sessions", {
    engagement_id: engagementId, title: opts.title || file.name.replace(/\.[^.]+$/, ""), filename: file.name,
    size_bytes: file.size, declared_mime: file.type || null, request_id: opts.requestId || null, supersedes_id: opts.supersedesId || null,
  });
  return api.put(`${s.upload_url}?token=${encodeURIComponent(s.upload_token)}`, file);
}

export async function openAuthed(path: string, method: "GET" | "POST" = "GET") {
  // Fetch HTML/CSV with the bearer token and open it as a blob (links can't carry headers).
  let url = path;
  if (method === "POST") url = (await api.post(path)).download_url;
  const r = await fetch(url, { headers: token ? { authorization: `Bearer ${token}` } : {} });
  if (!r.ok) throw new ApiError(r.status, await r.text());
  const blob = await r.blob();
  window.open(URL.createObjectURL(blob), "_blank", "noopener");
}

export async function fetchHtml(path: string): Promise<string> {
  const r = await fetch(path, { headers: token ? { authorization: `Bearer ${token}` } : {} });
  if (!r.ok) throw new ApiError(r.status, await r.text());
  return r.text();
}

export const authHeader = (): Record<string, string> => (token ? { authorization: `Bearer ${token}` } : {});

export function getTheme(): "dark" | "light" | null {
  try { return (localStorage.getItem("tensor_theme") as "dark" | "light" | null) || null; } catch { return null; }
}
export function setTheme(t: "dark" | "light") {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("tensor_theme", t); } catch { /* ignore */ }
}
