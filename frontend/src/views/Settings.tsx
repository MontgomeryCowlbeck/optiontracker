import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ApiKey, Fill, JournalSettings, TagT, Trade, TTAccount } from "../api/types";
import { TradeEditor } from "../components/TradeEditor";
import { Empty, Modal, Skeleton, TAG_COLORS, TagComposer } from "../components/ui";
import { useNarrow } from "../lib/device";
import { timeShort } from "../lib/format";
import { useAuth, useToast } from "../state/store";


/* Repair queue — fills the auto-tracker couldn't place. Normally empty. */
function FillRepair() {
  const toast = useToast();
  const { accountEpoch } = useAuth();
  const [fills, setFills] = useState<Fill[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editTrade, setEditTrade] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const { fills } = await api<{ fills: Fill[] }>("/journal/fills");
      setFills(fills || []);
      setSelected(new Set());
    } catch (e) {
      toast((e as Error).message, "err");
      setFills([]);
    }
  }, [toast]);

  useEffect(() => {
    setFills(null);
    load();
    const on = () => load();
    window.addEventListener("pt:synced", on);
    return () => window.removeEventListener("pt:synced", on);
  }, [load, accountEpoch]);

  const dismiss = async (id: number) => {
    try {
      await api(`/journal/fills/${id}/dismiss`, { method: "POST" });
      toast("Fill dismissed");
      await load();
      window.dispatchEvent(new CustomEvent("pt:synced")); // refresh the nav badge
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const group = async () => {
    try {
      const trade = await api<Trade>("/journal/trades", {
        method: "POST",
        body: { fill_ids: [...selected] },
      });
      toast("Trade created from repaired fills");
      await load();
      window.dispatchEvent(new CustomEvent("pt:synced"));
      setEditTrade(trade.id);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (fills === null) return <Skeleton h={60} />;
  if (!fills.length) {
    return (
      <p className="muted small" style={{ margin: 0 }}>
        Repair queue is empty — the tracker placed every fill. It stays that way unless a fill closes
        legs across several trades.
      </p>
    );
  }

  return (
    <div className="stack">
      <div className="row">
        <span className="badge warn">
          {fills.length} fill{fills.length === 1 ? "" : "s"} the tracker couldn't place
        </span>
        {selected.size > 0 && (
          <button className="btn btn-primary btn-sm" onClick={group}>
            Group as trade ({selected.size})
          </button>
        )}
      </div>
      <div className="card tight">
        {fills.map((f) => (
          // label, not div: the 18px checkbox alone is no touch target — the
          // whole row toggles selection (the Dismiss button opts out below)
          <label key={f.id} className="fill-row" style={{ cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={selected.has(f.id)}
              onChange={() => {
                const s = new Set(selected);
                if (s.has(f.id)) s.delete(f.id);
                else s.add(f.id);
                setSelected(s);
              }}
              aria-label="select fill"
            />
            <div className="desc">
              <span className="action">{f.action}</span>
              <span>
                {f.underlying} {f.option_type} {f.strike} · {f.expiration}
              </span>{" "}
              <span className="badge">{f.is_opening ? "open" : "close"}</span>
              <div className="when num">
                {timeShort(f.executed_at)} · qty {f.quantity} @ {f.price}
              </div>
            </div>
            <button
              className="btn btn-ghost btn-sm"
              onClick={(e) => {
                e.preventDefault(); // don't toggle the row's checkbox
                dismiss(f.id);
              }}
            >
              Dismiss
            </button>
          </label>
        ))}
      </div>
      {editTrade != null && (
        <Modal wide onClose={() => setEditTrade(null)}>
          <TradeEditor tradeId={editTrade} onClose={() => setEditTrade(null)} onChanged={() => load()} />
        </Modal>
      )}
    </div>
  );
}

function TagManager() {
  const toast = useToast();
  const { accountEpoch } = useAuth();
  const [tags, setTags] = useState<TagT[] | null>(null);
  const [editing, setEditing] = useState<TagT | null>(null);

  const load = useCallback(async () => {
    try {
      const { tags } = await api<{ tags: TagT[] }>("/journal/tags");
      setTags(tags || []);
    } catch (e) {
      toast((e as Error).message, "err");
      setTags([]);
    }
  }, [toast]);

  useEffect(() => {
    setTags(null);
    load();
  }, [load, accountEpoch]);

  const add = async (name: string, color: string) => {
    try {
      await api("/journal/tags", { method: "POST", body: { name, color } });
      await load();
    } catch (e) {
      toast((e as Error).message, "err");
      throw e; // keep the composer open with the draft intact
    }
  };

  if (tags === null) return <Skeleton h={80} />;

  return (
    <>
      <div className="chips">
        {tags.map((t) => (
          <button
            key={t.id}
            className="chip"
            style={{ color: t.color, background: t.color + "26", borderColor: t.color + "66" }}
            onClick={() => setEditing(t)}
            title="Edit tag"
          >
            {t.name}
          </button>
        ))}
        <TagComposer onCreate={add} />
      </div>
      {editing ? (
        <TagEditor tag={editing} onClose={() => setEditing(null)} onChanged={load} />
      ) : null}
    </>
  );
}

/* Edit one tag: rename, recolor, or delete — all in-app (native confirm()
   silently no-ops in standalone mobile contexts, so delete is two-tap here). */
function TagEditor({ tag, onClose, onChanged }: { tag: TagT; onClose: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [name, setName] = useState(tag.name);
  const [color, setColor] = useState(tag.color);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await api(`/journal/tags/${tag.id}`, { method: "PUT", body: { name: name.trim(), color } });
      toast("Tag saved");
      onClose();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const del = async () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    try {
      await api(`/journal/tags/${tag.id}`, { method: "DELETE" });
      toast("Tag deleted");
      onClose();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "err");
      setBusy(false);
    }
  };

  return (
    // NOT dismissable: it holds an unsaved rename, and on mobile the backdrop is
    // the whole area above the bottom sheet — a mis-tap must not eat the draft.
    <Modal title={`Edit tag — ${tag.name}`} onClose={onClose}>
      <label className="field">
        <span>Name</span>
        <input
          autoFocus
          value={name}
          autoComplete="off"
          enterKeyHint="done"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
        />
      </label>
      <div className="field" style={{ marginTop: 10 }}>
        <span>Color</span>
        <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 4 }}>
          {TAG_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              className={"swatch" + (c === color ? " on" : "")}
              style={{ background: c }}
              aria-label={`color ${c}`}
              onClick={() => setColor(c)}
            />
          ))}
        </div>
      </div>
      <div className="modal-actions">
        <button className="btn btn-danger" style={{ marginRight: "auto" }} onClick={del} disabled={busy}>
          {armed ? "Tap again to delete everywhere" : "Delete"}
        </button>
        <button className="btn btn-ghost" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={save} disabled={busy || !name.trim()}>
          Save
        </button>
      </div>
    </Modal>
  );
}

function ApiKeys() {
  const toast = useToast();
  const { accountEpoch } = useAuth();
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [name, setName] = useState("");
  const [freshKey, setFreshKey] = useState<string | null>(null);
  const narrow = useNarrow(640);

  const load = useCallback(async () => {
    try {
      const r = await api<{ api_keys: ApiKey[] }>("/auth/api-keys");
      setKeys(r.api_keys || []);
    } catch (e) {
      toast((e as Error).message, "err");
      setKeys([]);
    }
  }, [toast]);

  useEffect(() => {
    setKeys(null);
    setFreshKey(null);
    load();
  }, [load, accountEpoch]);

  const create = async () => {
    if (!name.trim()) return;
    try {
      const r = await api<ApiKey & { key: string }>("/auth/api-keys", {
        method: "POST",
        body: { name: name.trim() },
      });
      setFreshKey(r.key);
      setName("");
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  /* Two-tap revoke — native confirm() silently no-ops in standalone mobile. */
  const [armedRevoke, setArmedRevoke] = useState<number | null>(null);
  const revoke = async (k: ApiKey) => {
    if (armedRevoke !== k.id) {
      setArmedRevoke(k.id);
      toast(`Anything using “${k.name}” stops working. Tap again to revoke.`);
      window.setTimeout(() => setArmedRevoke((cur) => (cur === k.id ? null : cur)), 5000);
      return;
    }
    setArmedRevoke(null);
    try {
      await api(`/auth/api-keys/${k.id}`, { method: "DELETE" });
      toast("Key revoked");
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (keys === null) return <Skeleton h={60} />;

  return (
    <div className="stack">
      {freshKey && (
        <div>
          <div className="key-once num">{freshKey}</div>
          <div className="row" style={{ gap: 8 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() =>
                navigator.clipboard
                  ?.writeText(freshKey)
                  .then(() => toast("Key copied"))
                  .catch(() => toast("Copy failed — long-press to select", "err"))
              }
            >
              Copy key
            </button>
            <p className="muted small" style={{ margin: 0 }}>
              shown once and never again
            </p>
          </div>
        </div>
      )}
      {keys.filter((k) => k.is_active).length ? (
        narrow ? (
          // stacked rows: in the table the Revoke button was the 4th column of
          // an off-screen horizontal scroller
          <div className="stack" style={{ gap: 0 }}>
            {keys
              .filter((k) => k.is_active)
              .map((k) => (
                <div key={k.id} className="row" style={{ borderTop: "1px solid var(--grid)", padding: "8px 0", gap: 8 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ color: "var(--ink)", fontWeight: 600 }}>{k.name}</div>
                    <div className="muted small num">
                      created {k.created_at ? timeShort(k.created_at) : "—"} · last used{" "}
                      {k.last_used_at ? timeShort(k.last_used_at) : "never"}
                    </div>
                  </div>
                  <button
                    className={"btn btn-sm " + (armedRevoke === k.id ? "btn-danger" : "btn-ghost")}
                    onClick={() => revoke(k)}
                  >
                    {armedRevoke === k.id ? "Tap again" : "Revoke"}
                  </button>
                </div>
              ))}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Created</th>
                  <th>Last used</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {keys
                  .filter((k) => k.is_active)
                  .map((k) => (
                    <tr key={k.id}>
                      <td>{k.name}</td>
                      <td>{k.created_at ? timeShort(k.created_at) : "—"}</td>
                      <td>{k.last_used_at ? timeShort(k.last_used_at) : "never"}</td>
                      <td className="n">
                        <button
                          className={"btn btn-sm " + (armedRevoke === k.id ? "btn-danger" : "btn-ghost")}
                          onClick={() => revoke(k)}
                        >
                          {armedRevoke === k.id ? "Tap again" : "Revoke"}
                        </button>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <p className="muted small" style={{ margin: 0 }}>
          No active keys. Keys give dashboards and scripts read access without your password.
        </p>
      )}
      <div className="row">
        <input
          placeholder="key name (e.g. framely)"
          style={{ maxWidth: 240 }}
          value={name}
          autoComplete="off"
          enterKeyHint="done"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && create()}
        />
        <button className="btn btn-ghost btn-sm" onClick={create} disabled={!name.trim()}>
          Create key
        </button>
      </div>
    </div>
  );
}

function PasswordChange() {
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const change = async () => {
    setBusy(true);
    try {
      await api("/auth/password", {
        method: "PUT",
        body: { current_password: cur, new_password: next },
      });
      toast("Password changed");
      setCur("");
      setNext("");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="row" style={{ alignItems: "flex-end" }}>
      <label className="field" style={{ maxWidth: 220 }}>
        <span>Current password</span>
        <input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
      </label>
      <label className="field" style={{ maxWidth: 220 }}>
        <span>New password (min 8)</span>
        <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      </label>
      <button className="btn btn-ghost" onClick={change} disabled={busy || !cur || next.length < 8}>
        Change
      </button>
    </div>
  );
}

export function Settings() {
  const toast = useToast();
  const { accountEpoch, accounts, activeAccount, switchAccount, refreshAccounts, logout } = useAuth();
  const [settings, setSettings] = useState<JournalSettings | null>(null);
  const [ttAccounts, setTtAccounts] = useState<TTAccount[] | null>(null);
  const [syncFrom, setSyncFrom] = useState("");
  const [provisioning, setProvisioning] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await api<JournalSettings>("/journal/settings");
      setSettings(s);
      setSyncFrom(s.sync_from_date || "2026-07-14");
    } catch {
      setSettings({});
      setSyncFrom("2026-07-14");
    }
    try {
      const t = await api<{ accounts: TTAccount[] }>("/journal/tt-accounts");
      setTtAccounts(t.accounts || []);
    } catch {
      setTtAccounts([]);
    }
  }, []);

  useEffect(() => {
    setSettings(null);
    load();
  }, [load, accountEpoch]);

  const provision = async () => {
    setProvisioning(true);
    try {
      const res = await api<{ accounts: { id: number; name: string }[] }>("/journal/accounts/provision", {
        method: "POST",
      });
      await refreshAccounts();
      toast(`${res.accounts.length} account${res.accounts.length === 1 ? "" : "s"} ready — pick one in the top bar`);
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setProvisioning(false);
    }
  };

  const saveSyncFrom = async () => {
    try {
      await api("/journal/settings", { method: "PUT", body: { sync_from_date: syncFrom || null } });
      toast("Settings saved");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (settings === null) return <Skeleton h={110} n={3} />;

  return (
    <div>
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      {/* five long stacked sections — jump links save a phone's worth of scrolling */}
      <div className="row" style={{ gap: 6, marginBottom: 12 }}>
        {[
          ["st-tt", "Tastytrade"],
          ["st-acct", "Account"],
          ["st-repair", "Fill repair"],
          ["st-tags", "Tags"],
          ["st-keys", "API keys"],
        ].map(([id, label]) => (
          <button
            key={id}
            className="badge-btn"
            onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}
          >
            {label}
          </button>
        ))}
      </div>

      <h3 className="section-title" id="st-tt">Tastytrade</h3>
      <div className="card stack">
        <p className="muted small" style={{ margin: 0 }}>
          One isolated journal per Tastytrade account — trades, edges, notes, and analytics never cross.
          Provisioning binds each journal to an account; the binding is fixed on purpose.
        </p>
        <div className="row">
          <span>
            This journal pulls from{" "}
            <strong className="num" style={{ color: "var(--ink)" }}>
              {settings.tt_account_number || "— not set —"}
            </strong>
          </span>
        </div>
        {ttAccounts && ttAccounts.length > 0 && (
          <div className="muted small">
            Tastytrade sees: {ttAccounts.map((a) => a.number).join(", ")}
          </div>
        )}
        <div className="row">
          <button className="btn btn-ghost btn-sm" onClick={provision} disabled={provisioning}>
            {provisioning ? "Provisioning…" : "Sync my Tastytrade accounts"}
          </button>
        </div>
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field" style={{ maxWidth: 200 }}>
            <span>Sync fills from</span>
            <input type="date" value={syncFrom} onChange={(e) => setSyncFrom(e.target.value)} />
          </label>
          <button className="btn btn-ghost btn-sm" onClick={saveSyncFrom}>
            Save
          </button>
          <span className="muted small">journal epoch is 2026-07-14 — no history before it is pulled</span>
        </div>
      </div>

      <h3 className="section-title" id="st-acct">Journal account</h3>
      <div className="card stack">
        {accounts.length > 1 ? (
          <div className="row">
            <label className="field" style={{ maxWidth: 260 }}>
              <span>Active journal</span>
              <select value={activeAccount ?? ""} onChange={(e) => switchAccount(Number(e.target.value))}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : (
          <p className="muted small" style={{ margin: 0 }}>
            One journal account. Provision above if you trade multiple Tastytrade accounts.
          </p>
        )}
        <PasswordChange />
        <div className="row">
          <button className="btn btn-danger btn-sm" onClick={logout}>
            Log out
          </button>
        </div>
      </div>

      <h3 className="section-title" id="st-repair">Fill repair</h3>
      <div className="card">
        <FillRepair />
      </div>

      <h3 className="section-title" id="st-tags">Tags</h3>
      <div className="card">
        <p className="muted small" style={{ marginTop: 0 }}>
          One shared pool of colored labels. Apply them by opening a trade (tap its row in
          Positions or the Logbook) and toggling the chips in the editor. Here you manage the pool
          itself — tap a tag to rename, recolor, or delete it (renames and recolors follow through
          to every trade carrying it).
        </p>
        <TagManager />
      </div>

      <h3 className="section-title" id="st-keys">API keys</h3>
      <div className="card">
        <ApiKeys />
      </div>

      {!settings.tt_account_number && (
        <div style={{ marginTop: 16 }}>
          <Empty hint="Provision above, set the sync date, then Sync from the top bar — trades build themselves from your fills.">
            Not pulling anything yet.
          </Empty>
        </div>
      )}
    </div>
  );
}
