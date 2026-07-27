import { useEffect, useState } from "react";
import { ApiError, AuditRow, Usage as UsageT, api } from "../api";

function Meter({ used, cap }: { used: number; cap: number }) {
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  return (
    <div className="meter">
      <span className={pct >= 100 ? "over" : ""} style={{ width: `${pct}%` }} />
    </div>
  );
}

function kb(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function Usage() {
  const [usage, setUsage] = useState<UsageT | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        setUsage(await api.get<UsageT>("/usage"));
        setAudit(await api.get<AuditRow[]>("/audit"));
      } catch (err) { setError(err instanceof ApiError ? err.message : "failed to load"); }
    })();
  }, []);

  if (error) return <p className="error">{error}</p>;
  if (!usage) return <p className="muted">Loading...</p>;

  return (
    <div>
      <div className="card">
        <h2>Usage</h2>
        <div className="stat-grid">
          <div>
            <div className="spread"><span className="muted">Questions (month)</span><strong>{usage.questions.used} / {usage.questions.cap}</strong></div>
            <Meter used={usage.questions.used} cap={usage.questions.cap} />
          </div>
          <div>
            <div className="spread"><span className="muted">Spend (24h)</span><strong>${usage.spend.used_usd.toFixed(4)} / ${usage.spend.budget_usd.toFixed(2)}</strong></div>
            <Meter used={usage.spend.used_usd} cap={usage.spend.budget_usd} />
          </div>
          <div>
            <div className="spread"><span className="muted">Documents</span><strong>{usage.storage.docs} / {usage.storage.doc_cap}</strong></div>
            <Meter used={usage.storage.docs} cap={usage.storage.doc_cap} />
          </div>
          <div>
            <div className="spread"><span className="muted">Storage</span><strong>{kb(usage.storage.bytes)} / {kb(usage.storage.byte_cap)}</strong></div>
            <Meter used={usage.storage.bytes} cap={usage.storage.byte_cap} />
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Top questions this month</h2>
        {usage.top_queries.length === 0 ? (
          <p className="muted">No questions yet.</p>
        ) : (
          <ul>
            {usage.top_queries.map((q, i) => (
              <li key={i}><span className="pill">{q.count}</span> {q.question}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <h2>Recent activity</h2>
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead><tr><th>When</th><th>Actor</th><th>Action</th></tr></thead>
            <tbody>
              {audit.map((a, i) => (
                <tr key={i}>
                  <td className="muted">{new Date(a.created_at).toLocaleString()}</td>
                  <td>{a.actor ?? "system"}</td>
                  <td><code>{a.action}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
