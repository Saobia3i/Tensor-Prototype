import { useEffect, useRef, useState } from "react";
import { api, authHeader } from "../api";
import { Pill, go } from "../ui";

const STEPS = ["Uploaded", "Quarantined", "Malware scan", "Extracted", "AI proposal"];
const FINAL = new Set(["proposed", "expert_review", "approved", "needs_information", "rejected_security", "duplicate", "failed", "rejected"]);
const ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md,.json,.log,.yaml,.yml,.png,.jpg,.jpeg";

type Item = { key: string; file: File; progress: number; id?: string; status: string; error?: string; detail?: any };

function stepOf(it: Item): { done: number; bad: boolean } {
  const s = it.status;
  if (s === "error" || s === "rejected_security" || s === "failed" || s === "duplicate") {
    const at = s === "rejected_security" ? 2 : s === "failed" ? 3 : s === "duplicate" ? 2 : 0;
    return { done: at, bad: true };
  }
  const map: Record<string, number> = { queued: 0, uploading: 0, awaiting_upload: 0, quarantined: 1, scanning: 2, extracted: 4, proposed: 5, expert_review: 5, approved: 5, needs_information: 5 };
  return { done: map[s] ?? 0, bad: false };
}

function putWithProgress(url: string, file: File, onProgress: (p: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", url);
    Object.entries(authHeader()).forEach(([k, v]) => x.setRequestHeader(k, v));
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((100 * e.loaded) / e.total));
    x.onload = () => (x.status < 300 ? resolve() : reject(new Error(JSON.parse(x.responseText || "{}").detail || `HTTP ${x.status}`)));
    x.onerror = () => reject(new Error("network error"));
    x.send(file);
  });
}

export default function Uploader({ engagementId, onChange, requestId }: { engagementId: string; onChange?: () => void; requestId?: string }) {
  const [items, setItems] = useState<Item[]>([]);
  const [over, setOver] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  const live = useRef(items);
  live.current = items;
  const upd = (key: string, patch: Partial<Item>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));

  const add = async (files: FileList | File[]) => {
    const fresh = [...files].map((f) => ({ key: `${f.name}-${f.size}-${Math.random()}`, file: f, progress: 0, status: "queued" }));
    setItems((xs) => [...fresh, ...xs]);
    for (const it of fresh) {
      try {
        upd(it.key, { status: "uploading" });
        const s = await api.post("/v1/evidence/upload-sessions", {
          engagement_id: engagementId, title: it.file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "), filename: it.file.name,
          size_bytes: it.file.size, declared_mime: it.file.type || null, request_id: requestId || null,
        });
        upd(it.key, { id: s.evidence_id });
        await putWithProgress(`${s.upload_url}?token=${encodeURIComponent(s.upload_token)}`, it.file, (p) => upd(it.key, { progress: p }));
        upd(it.key, { status: "quarantined", progress: 100 });
      } catch (e: any) {
        upd(it.key, { status: "error", error: e.message });
      }
    }
    onChange?.();
  };

  // Poll the pipeline for items still in flight.
  useEffect(() => {
    const t = setInterval(async () => {
      const pending = live.current.filter((x) => x.id && !FINAL.has(x.status) && x.status !== "error" && x.status !== "uploading");
      for (const it of pending) {
        try {
          const ev = await api.get(`/v1/evidence/${it.id}`);
          upd(it.key, { status: ev.status, error: ev.error || undefined, detail: ev });
          if (FINAL.has(ev.status)) onChange?.();
        } catch { /* keep polling */ }
      }
    }, 1500);
    return () => clearInterval(t);
  }, [onChange]);

  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className={`drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files.length) add(e.dataTransfer.files); }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Drop evidence files here</div>
        <div className="small muted" style={{ margin: "4px 0 10px" }}>Several at once is fine · PDF, DOCX, XLSX, CSV, TXT, MD, JSON, PNG, JPG · max 25 MB each.
          Each file is quarantined, malware-scanned and fingerprinted (SHA-256) before anyone reads it; then the AI drafts a mapping for an expert.</div>
        <button className="btn primary" onClick={() => ref.current?.click()}>Browse files</button>
        <input ref={ref} type="file" multiple hidden accept={ACCEPT} onChange={(e) => { if (e.target.files?.length) add(e.target.files); e.target.value = ""; }} />
      </div>
      {items.length > 0 && <table style={{ marginTop: 12 }}>
        <thead><tr><th>File</th><th style={{ width: 300 }}>Pipeline</th><th>Status</th><th>AI draft</th></tr></thead>
        <tbody>{items.map((it) => {
          const { done, bad } = stepOf(it);
          const out = it.detail?.artifacts?.[0]?.output;
          return <tr key={it.key} className={it.id && FINAL.has(it.status) ? "click" : ""} onClick={() => it.id && FINAL.has(it.status) && go(`/evidence/${it.id}`)}>
            <td><b className="small">{it.file.name}</b><br /><span className="faint small">{it.file.size < 1024 ? `${it.file.size} B` : `${Math.round(it.file.size / 1024)} KB`}{it.status === "uploading" ? ` · ${it.progress}%` : ""}</span></td>
            <td><div className="pipeline">{STEPS.map((s, i) => <div key={s} title={s} className={`s ${i < done ? "done" : ""} ${i === done && !bad && !FINAL.has(it.status) ? "active" : ""} ${bad && i === done ? "bad" : ""}`} />)}</div>
              <div className="faint" style={{ fontSize: 11, marginTop: 3 }}>{bad ? (it.error || it.status) : STEPS[Math.min(done, 4)]}</div></td>
            <td><Pill s={it.status === "error" ? "error" : it.status} /></td>
            <td className="small">{out ? <><Pill s={out.decision} />{out.candidate_controls?.length ? <span className="faint"> {out.candidate_controls.map((c: any) => c.control_code).slice(0, 3).join(", ")}</span> : null}
              {it.detail?.artifacts?.[0]?.warnings?.length ? <><br /><span style={{ color: "var(--warn)" }}>⚠ {it.detail.artifacts[0].warnings[0]}</span></> : null}</>
              : it.detail && it.detail.can_review === false && FINAL.has(it.status) ? <span className="faint">sent to Tensor experts</span> : <span className="faint">—</span>}</td>
          </tr>;
        })}</tbody>
      </table>}
    </div>
  );
}
