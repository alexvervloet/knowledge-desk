import { useEffect, useState } from "react";
import { Me, api, clearToken, getToken } from "./api";
import { Login } from "./components/Login";
import { Ask } from "./components/Ask";
import { Sources } from "./components/Sources";
import { Members } from "./components/Members";
import { Usage } from "./components/Usage";
import { Account } from "./components/Account";

type Tab = "ask" | "sources" | "members" | "usage" | "account";

export function App() {
  const [me, setMe] = useState<Me | null | "loading">("loading");
  const [tab, setTab] = useState<Tab>("ask");

  async function loadMe() {
    if (!getToken()) { setMe(null); return; }
    try {
      setMe(await api.get<Me>("/me"));
    } catch {
      clearToken();
      setMe(null);
    }
  }

  useEffect(() => { loadMe(); }, []);

  async function logout() {
    try { await api.post("/auth/logout"); } catch { /* token may already be gone */ }
    clearToken();
    setMe(null);
    setTab("ask");
  }

  if (me === "loading") return <div className="app"><p className="muted">Loading...</p></div>;
  if (me === null) return <Login onAuthed={loadMe} />;

  const isAdmin = me.role === "owner" || me.role === "admin";
  const tabs: [Tab, string][] = [["ask", "Ask"], ["sources", "Sources"]];
  if (isAdmin) tabs.push(["members", "Members"], ["usage", "Usage"]);
  tabs.push(["account", "Account"]);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Knowledge Desk</span>
        <span className="who">
          {me.email} · <span className="pill">{me.role}</span>{" "}
          <button onClick={logout} style={{ marginLeft: "0.5rem" }}>Log out</button>
        </span>
      </header>

      <nav className="tabs">
        {tabs.map(([id, label]) => (
          <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === "ask" && <Ask />}
      {tab === "sources" && <Sources isAdmin={isAdmin} />}
      {tab === "members" && isAdmin && <Members me={me} />}
      {tab === "usage" && isAdmin && <Usage />}
      {tab === "account" && <Account me={me} />}
    </div>
  );
}
