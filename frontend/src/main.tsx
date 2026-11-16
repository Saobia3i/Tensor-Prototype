import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { api, getTheme, hasToken, setTheme, setToken } from "./api";
import { MeCtx, ToastHost, go, useRoute, type Me } from "./ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Queue from "./pages/Queue";
import Engagements from "./pages/Engagements";
import EngagementPage from "./pages/Engagement";
import EvidencePage from "./pages/Evidence";
import { SnapshotList, SnapshotNew, SnapshotDetail } from "./pages/Snapshots";
import QuestionnairePage from "./pages/Questionnaire";
import Library from "./pages/Library";
import ExecutiveReport from "./pages/Report";

const saved = getTheme();
if (saved) document.documentElement.dataset.theme = saved;

function ThemeToggle() {
  const [t, setT] = useState<"dark" | "light">(saved || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  return <button className="btn sm ghost" onClick={() => { const n = t === "dark" ? "light" : "dark"; setTheme(n); setT(n); }}>{t === "dark" ? "☀ Light" : "☾ Dark"} mode</button>;
}

function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const r = useRoute();
  const sec = r[0] || "";
  const staff = me.is_staff;
  const roles = [...new Set(me.memberships.map((m) => m.role))];
  const links: [string, string][] = staff
    ? [["", "Dashboard"], ["queue", "Review queue"], ["engagements", "Engagements"], ["snapshots", "Readiness snapshots"], ["library", "Control library"]]
    : [["engagements", roles.includes("auditor") ? "Engagements (read-only)" : "My compliance program"]];
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand"><span className="dot" />Tensor Ops</div>
        <nav className="nav">
          {links.map(([k, l]) => <a key={k} href={`#/${k}`} className={sec === k ? "active" : ""}>{l}</a>)}
        </nav>
        <div className="who">
          <b>{me.name}</b>{me.email}<br />
          <span className="muted">{staff ? (me.expert_title || "Tensor staff") : roles.join(", ").replace(/_/g, " ")}</span><br />
          <div className="row" style={{ marginTop: 8, gap: 6 }}><ThemeToggle />
            <button className="btn sm" onClick={() => { setToken(null); go("/login"); location.reload(); }}>Sign out</button></div>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}

function Routes({ me }: { me: Me }) {
  const r = useRoute();
  const [a, b, c] = r;
  if (!me.is_staff && (a === undefined || a === "")) { go("/engagements"); return null; }
  switch (a) {
    case undefined: case "": return <Dashboard />;
    case "queue": return <Queue />;
    case "engagements": return b ? <EngagementPage id={b} tab={c} /> : <Engagements />;
    case "evidence": return <EvidencePage id={b} />;
    case "snapshots": return b === "new" ? <SnapshotNew /> : b ? <SnapshotDetail id={b} /> : <SnapshotList />;
    case "questionnaires": return <QuestionnairePage id={b} />;
    case "library": return <Library />;
    case "report": return <ExecutiveReport id={b} />;
    default: return <p>Not found.</p>;
  }
}

function App() {
  const r = useRoute();
  const [me, setMe] = useState<Me | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!hasToken()) { setMe(null); return; }
    api.get<Me>("/v1/me").then(setMe).catch(() => setMe(null));
  }, [n, r[0] === "login"]);
  if (r[0] === "login" || !hasToken()) return <Login onIn={() => { setN((x) => x + 1); go("/"); }} />;
  if (!me) return <p style={{ padding: 40 }} className="muted">Loading…</p>;
  return <MeCtx.Provider value={me}><Shell me={me}><Routes me={me} /></Shell></MeCtx.Provider>;
}

createRoot(document.getElementById("root")!).render(<StrictMode><ToastHost><App /></ToastHost></StrictMode>);
