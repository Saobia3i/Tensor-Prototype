import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

// ---------- hash router
export function useRoute(): string[] {
  const [h, setH] = useState(location.hash || "#/");
  useEffect(() => {
    const f = () => setH(location.hash || "#/");
    addEventListener("hashchange", f);
    return () => removeEventListener("hashchange", f);
  }, []);
  return h.replace(/^#\/?/, "").split("/").filter(Boolean);
}
export const go = (p: string) => { location.hash = p.startsWith("#") ? p : `#${p}`; };

// ---------- data hook
export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setError(null);
    api.get<T>(path).then((d) => live && setData(d)).catch((e) => live && setError(e.message));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, n, ...deps]);
  const reload = useCallback(() => setN((x) => x + 1), []);
  return { data, error, reload, setData };
}

// ---------- session context
export type Me = {
  id: string; email: string; name: string; is_staff: boolean; can_sign: boolean; expert_title: string | null; mfa: boolean;
  memberships: { tenant_id: string; tenant_name: string; tenant_status: string; role: string; permissions: string[] }[];
};
export const MeCtx = createContext<Me | null>(null);
export const useMe = () => useContext(MeCtx)!;
export function can(me: Me, tenantId: string, perm: string) {
  return !!me.memberships.find((m) => m.tenant_id === tenantId)?.permissions.includes(perm);
}

// ---------- toast
const ToastCtx = createContext<(m: string) => void>(() => {});
export function ToastHost({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (msg) { const t = setTimeout(() => setMsg(null), 3800); return () => clearTimeout(t); } }, [msg]);
  return <ToastCtx.Provider value={setMsg}>{children}{msg && <div className="toast" role="status">{msg}</div>}</ToastCtx.Provider>;
}
export const useToast = () => useContext(ToastCtx);

/** Wrap an async action: shows errors as toasts, runs onDone on success. */
export function useAction() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (fn: () => Promise<unknown>, ok?: string, onDone?: () => void) => {
    setBusy(true);
    try { await fn(); if (ok) toast(ok); onDone?.(); } catch (e: any) { toast(e.message || "Something went wrong"); } finally { setBusy(false); }
  }, [toast]);
  return { busy, run };
}

// ---------- small components
const TONE: Record<string, string> = {
  approved: "ok", signed_off: "ok", released: "ok", active: "ok", verified_closed: "ok", succeeded: "ok", edited: "ok",
  evidence_collected: "info", ready_for_signoff: "info", proposed: "info", drafted: "info", in_progress: "info", signed: "ok",
  sent: "info", won: "ok", in_review: "info", supported: "ok", partially_supported: "warn", client_approved: "info", published: "ok",
  expert_review: "warn", needs_information: "warn", needs_info: "warn", gap: "warn", open: "warn", blocked: "warn", degraded: "warn",
  draft: "warn", partial: "warn", ready_for_review: "info", prospect: "warn", submitted: "info", insufficient_evidence: "warn",
  rejected: "bad", rejected_security: "bad", failed: "bad", error: "bad", revoked: "bad", duplicate: "bad", expired: "bad",
  high: "bad", critical: "bad", medium: "warn", lost: "bad", deleted: "bad",
};
export function Pill({ s }: { s: string | null | undefined }) {
  if (!s) return null;
  return <span className={`pill ${TONE[s] || ""}`}>{s.replace(/_/g, " ")}</span>;
}
export function Stat({ k, v, n }: { k: string; v: ReactNode; n?: ReactNode }) {
  return <div className="card stat"><div className="k">{k}</div><div className="v">{v}</div>{n && <div className="n">{n}</div>}</div>;
}
export function Tabs({ tabs, on, set }: { tabs: [string, string][]; on: string; set: (t: string) => void }) {
  return <div className="tabs" role="tablist">{tabs.map(([k, l]) =>
    <button key={k} role="tab" aria-selected={on === k} className={on === k ? "on" : ""} onClick={() => set(k)}>{l}</button>)}</div>;
}
export function Err({ e }: { e: string | null }) { return e ? <div className="err">{e}</div> : null; }
export function Loading({ what = "Loading" }: { what?: string }) { return <p className="muted">{what}…</p>; }
export const money = (v: number | null | undefined) => v == null ? "—" : `$${Math.round(v).toLocaleString()}`;
export const pct = (v: number | null | undefined) => v == null ? "—" : `${Math.round(v * 100)}%`;
export const dt = (s: string | null | undefined) => s ? new Date(s).toLocaleString() : "—";
export const d = (s: string | null | undefined) => s ? s.slice(0, 10) : "—";
export function Bar({ v }: { v: number }) { return <div className="bar"><div style={{ width: `${Math.max(0, Math.min(100, v))}%` }} /></div>; }
