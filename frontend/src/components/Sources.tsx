import { ChangeEvent, useEffect, useState } from "react";
import { ApiError, DocumentRow, api } from "../api";

export function Sources({ isAdmin }: { isAdmin: boolean }) {
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try { setDocs(await api.get<DocumentRow[]>("/documents")); }
    catch (err) { setError(err instanceof ApiError ? err.message : "failed to load"); }
  }

  useEffect(() => { refresh(); }, []);

  async function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (!files.length) return;
    setBusy(true); setError("");
    try {
      const documents = await Promise.all(
        files.map(async (f) => ({ path: f.name, content: await f.text(), acl: ["public-to-org"] })),
      );
      await api.post("/sources/folder", { documents });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "upload failed");
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  async function remove(id: string) {
    if (!confirm("Delete this document and its chunks?")) return;
    try { await api.del(`/documents/${id}`); await refresh(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "delete failed"); }
  }

  async function editAcl(doc: DocumentRow) {
    const next = prompt(
      "Comma-separated principals (public-to-org, user:<id>, group:<id>)",
      doc.acl.join(", "),
    );
    if (next === null) return;
    const acl = next.split(",").map((s) => s.trim()).filter(Boolean);
    try { await api.patch(`/documents/${doc.id}/acl`, { acl }); await refresh(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "acl update failed"); }
  }

  return (
    <div>
      {isAdmin && (
        <div className="card">
          <h2>Upload documents</h2>
          <p className="muted">Text files are captured and embedded by the worker. Run the worker to process the queue.</p>
          <input type="file" multiple accept=".txt,.md,.csv,.json" onChange={onUpload} disabled={busy} />
        </div>
      )}

      <div className="card">
        <div className="spread">
          <h2>Documents ({docs.length})</h2>
          <button onClick={refresh}>Refresh</button>
        </div>
        {error && <p className="error">{error}</p>}
        {docs.length === 0 ? (
          <p className="muted">No documents yet.</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr><th>Path</th><th>Status</th><th>Chunks</th><th>PII</th><th>ACL</th>{isAdmin && <th></th>}</tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td><code>{d.path}</code></td>
                    <td><span className={`pill ${d.status}`}>{d.status}</span></td>
                    <td>{d.chunk_count}</td>
                    <td>{d.pii_types.length ? <span className="pill pii">{d.pii_types.join(", ")}</span> : <span className="muted">none</span>}</td>
                    <td className="muted">{d.acl.join(", ")}</td>
                    {isAdmin && (
                      <td>
                        <div className="row">
                          <button onClick={() => editAcl(d)}>ACL</button>
                          <button className="danger" onClick={() => remove(d.id)}>Delete</button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
