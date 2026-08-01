import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Analytics, Edge, Mechanism } from "../api/types";
import { AutoTextarea, Empty, Modal, Pnl, Skeleton } from "../components/ui";
import { num, pct } from "../lib/format";
import { useAuth, useToast } from "../state/store";

const STATUS_BADGE: Record<string, string> = {
  validated: "good",
  developing: "warn",
  retired: "off",
};

interface EdgeStats {
  count: number;
  total_pnl: number;
  win_rate: number | null;
  expectancy: number | null;
}

function EdgeForm({
  m,
  onDone,
  onCancel,
}: {
  m: Edge | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(m?.name || "");
  const [criteria, setCriteria] = useState(m?.criteria || "");
  const [regime, setRegime] = useState(m?.regime || "");
  const [status, setStatus] = useState(m?.status || "developing");
  const [notes, setNotes] = useState(m?.notes || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!name.trim()) {
      toast("Name required", "err");
      return;
    }
    setBusy(true);
    const body = {
      name: name.trim(),
      criteria: criteria || null,
      regime: regime || null,
      status,
      notes: notes || null,
    };
    try {
      if (m) await api(`/journal/edges/${m.id}`, { method: "PUT", body });
      else await api("/journal/edges", { method: "POST", body });
      toast("Saved");
      onDone();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="stack">
        <label className="field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>Criteria (the rule)</span>
          <AutoTextarea minRows={3} value={criteria} onChange={(e) => setCriteria(e.target.value)} />
        </label>
        <div className="field-grid" style={{ margin: 0 }}>
          <label className="field">
            <span>Regime</span>
            <select value={regime} onChange={(e) => setRegime(e.target.value)}>
              {["", "range", "trend", "chop", "n/a"].map((r) => (
                <option key={r} value={r}>
                  {r || "—"}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Status</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {["developing", "validated", "retired"].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
        </div>
        <label className="field">
          <span>Notes</span>
          <AutoTextarea minRows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </div>
      <div className="modal-actions">
        <button className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={save} disabled={busy}>
          Save
        </button>
      </div>
    </>
  );
}

function MechanismForm({
  m,
  structure,
  onDone,
  onCancel,
}: {
  m: Mechanism | null;
  /** set when adding a playbook for an unplaybooked structure */
  structure?: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(m?.name || prettyStructure(structure || ""));
  const [rules, setRules] = useState(m?.rules || "");
  const [notes, setNotes] = useState(m?.notes || "");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    if (!name.trim()) {
      toast("Name required", "err");
      return;
    }
    setBusy(true);
    try {
      if (m) {
        await api(`/journal/mechanisms/${m.id}`, {
          method: "PUT",
          body: { name: name.trim(), rules: rules || null, notes: notes || null },
        });
      } else {
        await api("/journal/mechanisms", {
          method: "POST",
          body: { structure, name: name.trim(), rules: rules || null, notes: notes || null },
        });
      }
      toast("Saved");
      onDone();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="stack">
        <p className="muted small" style={{ margin: 0 }}>
          Structure <code>{m?.structure || structure}</code> — auto-detected by the
          tracker; this playbook attaches to every trade with that shape.
        </p>
        <label className="field">
          <span>Display name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        </label>
        <label className="field">
          <span>Playbook rules</span>
          <AutoTextarea minRows={5} value={rules} onChange={(e) => setRules(e.target.value)} />
        </label>
        <label className="field">
          <span>Notes</span>
          <AutoTextarea minRows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </div>
      <div className="modal-actions">
        <button className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={save} disabled={busy}>
          Save
        </button>
      </div>
    </>
  );
}

export function prettyStructure(s: string): string {
  return s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function Edges() {
  const toast = useToast();
  const { accountEpoch } = useAuth();
  const [edges, setEdges] = useState<Edge[] | null>(null);
  const [stats, setStats] = useState<Map<number, EdgeStats>>(new Map());
  const [editing, setEditing] = useState<Edge | null | "new">(null);
  const [mechs, setMechs] = useState<Mechanism[] | null>(null);
  const [orphans, setOrphans] = useState<{ structure: string; trade_count: number }[]>([]);
  const [mechEditing, setMechEditing] = useState<Mechanism | string | null>(null);

  const load = useCallback(async () => {
    try {
      const { edges: rows } = await api<{ edges: Edge[] }>("/journal/edges");
      setEdges(rows || []);
      const entries = await Promise.all(
        (rows || []).map(async (m) => {
          try {
            const a = await api<Analytics>(`/journal/analytics?edge_id=${m.id}`);
            return [m.id, {
              count: a.overall.count,
              total_pnl: a.overall.total_pnl,
              win_rate: a.overall.win_rate,
              expectancy: a.overall.expectancy,
            }] as [number, EdgeStats];
          } catch {
            return null;
          }
        }),
      );
      setStats(new Map(entries.filter(Boolean) as [number, EdgeStats][]));
    } catch (e) {
      toast((e as Error).message, "err");
      setEdges([]);
    }
    try {
      const r = await api<{ mechanisms: Mechanism[]; unplaybooked: { structure: string; trade_count: number }[] }>(
        "/journal/mechanisms",
      );
      setMechs(r.mechanisms || []);
      setOrphans(r.unplaybooked || []);
    } catch {
      setMechs([]);
    }
  }, [toast]);

  useEffect(() => {
    setEdges(null);
    load();
  }, [load, accountEpoch]);

  /* Two-tap delete — native confirm() silently no-ops in standalone mobile. */
  const [armedDel, setArmedDel] = useState<number | null>(null);
  const del = async (m: Edge) => {
    if (armedDel !== m.id) {
      setArmedDel(m.id);
      toast(`Trades keep their journal but lose the “${m.name}” link. Tap again to delete.`);
      window.setTimeout(() => setArmedDel((cur) => (cur === m.id ? null : cur)), 5000);
      return;
    }
    setArmedDel(null);
    try {
      await api(`/journal/edges/${m.id}`, { method: "DELETE" });
      toast("Deleted");
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  if (edges === null) return <Skeleton h={140} n={2} />;

  return (
    <div>
      <div className="page-header">
        <h1>Edges</h1>
        <button className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
          New edge
        </button>
      </div>
      <p className="muted small" style={{ marginTop: -8 }}>
        The hypotheses you trade — WHY a trade should pay. Every trade is measured
        against one; the structure below it is detected automatically.
      </p>

      {edges.length === 0 ? (
        <Empty hint="Define the rule first; the trades come second.">
          No edges yet. Start with the one you actually trade.
        </Empty>
      ) : (
        <div className="edge-grid">
          {edges.map((m) => {
            const s = stats.get(m.id);
            return (
              <div key={m.id} className="card edge-card">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <strong style={{ color: "var(--ink)" }}>{m.name}</strong>
                  <span className={`badge ${STATUS_BADGE[m.status] || ""}`}>
                    {m.status}
                    {m.regime ? ` · ${m.regime}` : ""}
                  </span>
                </div>
                {m.criteria ? <p className="muted small" style={{ margin: 0 }}>{m.criteria}</p> : null}
                {m.notes ? <p className="small" style={{ margin: 0, color: "var(--ink-2)" }}>{m.notes}</p> : null}
                <div className="edge-stats">
                  {s && s.count ? (
                    <>
                      <span>
                        n <b>{s.count}</b>
                      </span>
                      <span>
                        P&L <Pnl v={s.total_pnl} />
                      </span>
                      <span>
                        win <b>{pct(s.win_rate)}</b>
                      </span>
                      <span>
                        expect. <b>{s.expectancy == null ? "—" : num(s.expectancy)}</b>
                      </span>
                    </>
                  ) : (
                    <span className="muted">no closed trades yet</span>
                  )}
                </div>
                <div className="row" style={{ justifyContent: "flex-end" }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditing(m)}>
                    Edit
                  </button>
                  <button
                    className={"btn btn-sm " + (armedDel === m.id ? "btn-danger" : "btn-ghost")}
                    onClick={() => del(m)}
                  >
                    {armedDel === m.id ? "Tap again" : "Delete"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="page-header" style={{ marginTop: 28 }}>
        <h1>Mechanisms</h1>
      </div>
      <p className="muted small" style={{ marginTop: -8 }}>
        HOW a trade is built — one playbook per structure, assigned automatically
        from the tracker’s classification. Edit the rules; the assignment is never
        yours to do.
      </p>

      {mechs === null ? (
        <Skeleton h={100} n={2} />
      ) : (
        <div className="edge-grid">
          {mechs.map((m) => (
            <div key={m.id} className="card edge-card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong style={{ color: "var(--ink)" }}>{m.name}</strong>
                <span className="badge">
                  {m.structure}
                  {m.trade_count ? ` · ${m.trade_count} trade${m.trade_count === 1 ? "" : "s"}` : ""}
                </span>
              </div>
              {m.rules ? <p className="muted small" style={{ margin: 0 }}>{m.rules}</p> : null}
              {m.notes ? <p className="small" style={{ margin: 0, color: "var(--ink-2)" }}>{m.notes}</p> : null}
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setMechEditing(m)}>
                  Edit
                </button>
              </div>
            </div>
          ))}
          {orphans.map((o) => (
            <div key={o.structure} className="card edge-card" style={{ opacity: 0.8 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong style={{ color: "var(--ink)" }}>{prettyStructure(o.structure)}</strong>
                <span className="badge warn">
                  {o.structure} · {o.trade_count} trade{o.trade_count === 1 ? "" : "s"} · no playbook
                </span>
              </div>
              <p className="muted small" style={{ margin: 0 }}>
                Trades carry this structure but it has no playbook yet.
              </p>
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setMechEditing(o.structure)}>
                  Add playbook
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {editing !== null && (
        <Modal title={editing === "new" ? "New edge" : "Edit edge"} onClose={() => setEditing(null)}>
          <EdgeForm
            m={editing === "new" ? null : editing}
            onCancel={() => setEditing(null)}
            onDone={() => {
              setEditing(null);
              load();
            }}
          />
        </Modal>
      )}

      {mechEditing !== null && (
        <Modal
          title={typeof mechEditing === "string" ? "Add playbook" : "Edit playbook"}
          onClose={() => setMechEditing(null)}
        >
          <MechanismForm
            m={typeof mechEditing === "string" ? null : mechEditing}
            structure={typeof mechEditing === "string" ? mechEditing : undefined}
            onCancel={() => setMechEditing(null)}
            onDone={() => {
              setMechEditing(null);
              load();
            }}
          />
        </Modal>
      )}
    </div>
  );
}
