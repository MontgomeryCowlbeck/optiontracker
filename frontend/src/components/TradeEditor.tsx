/* Full journal editor for one trade — used inline (Logbook) and in a modal (Inbox). */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Edge, Fill, TagT, TradeDetail } from "../api/types";
import { money, num, pct, strategyLabel, timeShort } from "../lib/format";
import { useToast } from "../state/store";
import { AutoTextarea, ConvictionPicker, Pnl, Skeleton, TagComposer } from "./ui";

const EXIT_REASONS = ["target", "stop", "time", "scratch", "panic", "reversed", "discretionary", "expired", "other"];
const EMOTIONS = ["calm", "disciplined", "fomo", "revenge", "bored", "panic"];
const THESIS = ["yes", "no", "partial"];
const REGIMES = ["range", "trend", "chop"];

interface Draft {
  edge_id: number | null;
  no_edge: boolean;
  is_system: boolean | null;
  conviction: number | null;
  regime_read: string | null;
  thesis_worked: string | null;
  exit_reason: string | null;
  emotional_state: string | null;
  why_entered: string;
  reflection: string;
  planned_risk: string; // input text; parsed on save
  assignment_intent: boolean;
}

export function TradeEditor({
  tradeId,
  onClose,
  onChanged,
}: {
  tradeId: number;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [trade, setTrade] = useState<TradeDetail | null>(null);
  const [mechs, setMechs] = useState<Edge[]>([]);
  const [allTags, setAllTags] = useState<TagT[]>([]);
  const [tagIds, setTagIds] = useState<Set<number>>(new Set());
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [attachMode, setAttachMode] = useState(false);
  const [attachCandidates, setAttachCandidates] = useState<Fill[]>([]);
  const [attachSel, setAttachSel] = useState<Set<number>>(new Set());
  const [showDetail, setShowDetail] = useState(false); // fills/greeks tables — collapsed so the journal fields are reachable without scrolling
  const [ungroupArmed, setUngroupArmed] = useState(false); // native confirm() silently no-ops in standalone mobile — two-tap instead
  const [closeArmed, setCloseArmed] = useState(false); // Close discards the draft — arm it when dirty
  const initialRef = useRef<string>(""); // snapshot of the loaded draft, for the dirty check

  const load = useCallback(async () => {
    const [t, m, tg] = await Promise.all([
      api<TradeDetail>(`/journal/trades/${tradeId}`),
      api<{ edges: Edge[] }>("/journal/edges"),
      api<{ tags: TagT[] }>("/journal/tags"),
    ]);
    setTrade(t);
    setMechs(m.edges || []);
    setAllTags(tg.tags || []);
    setTagIds(new Set((t.tags || []).map((x) => x.id)));
    const d: Draft = {
      edge_id: t.edge_id ?? null,
      no_edge: !!t.no_edge,
      is_system: t.is_system == null ? null : !!t.is_system,
      conviction: t.conviction ?? null,
      regime_read: t.regime_read ?? null,
      thesis_worked: t.thesis_worked ?? null,
      exit_reason: t.exit_reason ?? null,
      emotional_state: t.emotional_state ?? null,
      why_entered: t.why_entered ?? "",
      reflection: t.reflection ?? "",
      planned_risk: t.planned_risk != null ? String(t.planned_risk) : "",
      assignment_intent: !!t.assignment_intent,
    };
    setDraft(d);
    initialRef.current = JSON.stringify(d);
  }, [tradeId]);

  useEffect(() => {
    load().catch((e) => toast((e as Error).message, "err"));
  }, [load, toast]);

  if (!trade || !draft) return <Skeleton h={220} />;

  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });
  const dirty = JSON.stringify(draft) !== initialRef.current;
  /* Close discards the draft (tags save instantly, the rest doesn't) — when
     dirty, require a second tap instead of silently eating the edits. */
  const requestClose = () => {
    if (dirty && !closeArmed) {
      setCloseArmed(true);
      toast("Unsaved changes — tap again to discard, or Save");
      window.setTimeout(() => setCloseArmed(false), 4000);
      return;
    }
    onClose();
  };
  const plannedRisk = parseFloat(draft.planned_risk);
  const liveR =
    trade.status === "closed" && trade.realized_pnl != null && plannedRisk > 0
      ? trade.realized_pnl / plannedRisk
      : null;

  const save = async () => {
    setSaving(true);
    try {
      await api(`/journal/trades/${tradeId}`, {
        method: "PUT",
        body: {
          edge_id: draft.edge_id,
          no_edge: draft.no_edge,
          is_system: draft.is_system,
          conviction: draft.conviction,
          regime_read: draft.regime_read,
          thesis_worked: draft.thesis_worked,
          exit_reason: draft.exit_reason,
          emotional_state: draft.emotional_state,
          why_entered: draft.why_entered || null,
          reflection: draft.reflection || null,
          planned_risk: draft.planned_risk.trim() === "" ? null : plannedRisk,
          assignment_intent: draft.assignment_intent,
        },
      });
      toast("Saved");
      onClose();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSaving(false);
    }
  };

  const ungroup = async () => {
    if (!ungroupArmed) {
      setUngroupArmed(true);
      toast("Ungroup deletes the trade; its fills go to the repair queue in Settings. Tap again to confirm.");
      window.setTimeout(() => setUngroupArmed(false), 5000);
      return;
    }
    try {
      await api(`/journal/trades/${tradeId}`, { method: "DELETE" });
      toast("Ungrouped — fills moved to the repair queue");
      window.dispatchEvent(new CustomEvent("pt:synced"));
      onClose();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  /* Tags apply instantly (optimistic PUT, rolled back on error) — no Save needed. */
  const persistTags = async (next: Set<number>, prev: Set<number>) => {
    setTagIds(next);
    try {
      await api(`/journal/trades/${tradeId}/tags`, { method: "PUT", body: { tag_ids: [...next] } });
      onChanged();
    } catch (e) {
      setTagIds(prev);
      toast((e as Error).message, "err");
    }
  };

  const toggleTag = (id: number) => {
    const next = new Set(tagIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    persistTags(next, tagIds);
  };

  /* Create a tag in the shared pool and apply it to this trade in one motion. */
  const createTag = async (name: string, color: string) => {
    try {
      const t = await api<TagT>("/journal/tags", { method: "POST", body: { name, color } });
      setAllTags((cur) => [...cur, t]);
      persistTags(new Set([...tagIds, t.id]), tagIds);
    } catch (e) {
      toast((e as Error).message, "err");
      throw e; // keep the composer open with the draft intact
    }
  };

  const openAttach = async () => {
    try {
      const { fills } = await api<{ fills: Fill[] }>("/journal/fills");
      const candidates = fills.filter((f) => f.underlying === trade.underlying);
      if (!candidates.length) {
        toast("No matching inbox fills");
        return;
      }
      setAttachCandidates(candidates);
      setAttachSel(new Set());
      setAttachMode(true);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const attach = async () => {
    if (!attachSel.size) {
      toast("Select at least one fill");
      return;
    }
    try {
      await api(`/journal/trades/${tradeId}/fills`, { method: "POST", body: { fill_ids: [...attachSel] } });
      toast("Attached");
      setAttachMode(false);
      await load();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const entryG = (trade.greeks || []).find((g) => g.snapshot_type === "entry");
  const exitG = (trade.greeks || []).find((g) => g.snapshot_type === "exit");

  if (attachMode) {
    return (
      <div>
        <h2>Attach fills · {trade.underlying}</h2>
        <div className="card tight">
          {attachCandidates.map((f) => (
            <label key={f.id} className="fill-row" style={{ cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={attachSel.has(f.id)}
                onChange={() => {
                  const s = new Set(attachSel);
                  if (s.has(f.id)) s.delete(f.id);
                  else s.add(f.id);
                  setAttachSel(s);
                }}
              />
              <span className="desc">
                <span className="action num">{f.action}</span> <strong>{f.underlying}</strong> {f.option_type}{" "}
                {f.strike} · {f.expiration} · {timeShort(f.executed_at)}
              </span>
            </label>
          ))}
        </div>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={() => setAttachMode(false)}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={attach}>
            Attach {attachSel.size ? `(${attachSel.size})` : ""}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {trade.underlying || "?"}
        {trade.direction ? <span className="badge">{strategyLabel(trade.direction)}</span> : null}
        {trade.is_0dte ? <span className="badge">0DTE</span> : null}
        <span className="badge">{trade.status}</span>
      </h2>
      <p className="muted small" style={{ margin: "2px 0 8px" }}>
        {trade.strikes || ""} · exp {trade.expiration || "—"}
        {trade.dte_at_entry != null ? ` · ${trade.dte_at_entry} DTE at entry` : ""}
        {trade.quantity != null ? ` · qty ${trade.quantity}` : ""}
        {trade.status === "closed" ? (
          <>
            {" · "}
            <Pnl v={trade.realized_pnl} pctVal={trade.realized_pnl_pct} />
          </>
        ) : null}
        {trade.fees_total != null ? ` · fees ${money(trade.fees_total, { sign: false })}` : ""}
      </p>
      {(entryG || exitG) && (
        <p className="muted small" style={{ margin: "0 0 8px" }}>
          Δ entry {entryG?.delta != null ? num(Number(entryG.delta)) : "—"} → exit{" "}
          {exitG?.delta != null ? num(Number(exitG.delta)) : "—"}
        </p>
      )}

      {trade.fills?.length || trade.greeks?.length ? (
        <button
          type="button"
          className="chip"
          style={{ margin: "0 0 8px" }}
          onClick={() => setShowDetail(!showDetail)}
        >
          {showDetail ? "▾ hide" : "▸ show"}{" "}
          {[
            trade.fills?.length ? `${trade.fills.length} fill${trade.fills.length === 1 ? "" : "s"}` : null,
            trade.greeks?.length ? "greeks" : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </button>
      ) : null}

      {showDetail && trade.fills?.length ? (
        <div className="card tight" style={{ margin: "8px 0" }}>
          <div className="table-wrap">
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Action</th>
                  <th className="n">Qty</th>
                  <th>Leg</th>
                  <th className="n">Price</th>
                  <th>Executed</th>
                </tr>
              </thead>
              <tbody>
                {trade.fills.map((f) => (
                  <tr key={f.id}>
                    <td className="num">{f.action}</td>
                    <td className="n">{f.quantity}</td>
                    <td>
                      {f.option_type} {f.strike} · {f.expiration} {f.is_opening ? "(open)" : "(close)"}
                    </td>
                    <td className="n">{f.price}</td>
                    <td>{timeShort(f.executed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {showDetail && trade.greeks?.length ? (
        <div className="card tight" style={{ margin: "8px 0" }}>
          <div className="table-wrap">
            <table className="mini-table">
              <thead>
                <tr>
                  <th>Snapshot</th>
                  <th className="n">Δ</th>
                  <th className="n">Γ</th>
                  <th className="n">Θ</th>
                  <th className="n">Vega</th>
                  <th className="n">IV</th>
                  <th>Captured</th>
                </tr>
              </thead>
              <tbody>
                {trade.greeks.map((g) => (
                  <tr key={g.id}>
                    <td>{String(g.snapshot_type ?? "")}</td>
                    <td className="n">{g.delta != null ? num(Number(g.delta)) : "—"}</td>
                    <td className="n">{g.gamma != null ? num(Number(g.gamma), 3) : "—"}</td>
                    <td className="n">{g.theta != null ? num(Number(g.theta)) : "—"}</td>
                    <td className="n">{g.vega != null ? num(Number(g.vega)) : "—"}</td>
                    <td className="n">
                      {g.implied_volatility != null ? pct(Number(g.implied_volatility) * 100) : "—"}
                    </td>
                    <td>{timeShort(g.captured_at as string)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="field-grid">
        <label className="field">
          <span>Edge</span>
          <select
            value={draft.no_edge ? "none" : draft.edge_id ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "none") set({ edge_id: null, no_edge: true });
              else set({ edge_id: v ? Number(v) : null, no_edge: false });
            }}
          >
            <option value="">— not set —</option>
            <option value="none">no edge — a declared one-off</option>
            {mechs.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Sleeve</span>
          <select
            value={draft.is_system == null ? "" : draft.is_system ? "1" : "0"}
            onChange={(e) => set({ is_system: e.target.value === "" ? null : e.target.value === "1" })}
          >
            <option value="">—</option>
            <option value="1">system</option>
            <option value="0">discretionary</option>
          </select>
        </label>
        <div className="field">
          <span>Conviction</span>
          <ConvictionPicker value={draft.conviction} onChange={(v) => set({ conviction: v })} />
        </div>
        <label className="field">
          <span>Regime read</span>
          <select value={draft.regime_read ?? ""} onChange={(e) => set({ regime_read: e.target.value || null })}>
            <option value="">—</option>
            {REGIMES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Did the thesis work?</span>
          <select
            value={draft.thesis_worked ?? ""}
            onChange={(e) => set({ thesis_worked: e.target.value || null })}
          >
            <option value="">—</option>
            {THESIS.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Exit reason</span>
          <select value={draft.exit_reason ?? ""} onChange={(e) => set({ exit_reason: e.target.value || null })}>
            <option value="">—</option>
            {EXIT_REASONS.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Emotional state</span>
          <select
            value={draft.emotional_state ?? ""}
            onChange={(e) => set({ emotional_state: e.target.value || null })}
          >
            <option value="">—</option>
            {EMOTIONS.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        {/* div, not label: the shortcut chips below would otherwise activate the
            label and pop the numeric keyboard on every tap */}
        <div className="field">
          <span>
            Planned risk $
            {liveR != null ? (
              <>
                {" "}
                → <b className={liveR >= 0 ? "pnl win" : "pnl loss"}>{num(liveR)}R</b>
              </>
            ) : null}
          </span>
          <input
            inputMode="decimal"
            enterKeyHint="done"
            placeholder={draft.assignment_intent ? "optional — capital committed instead" : "max planned loss"}
            value={draft.planned_risk}
            onChange={(e) => set({ planned_risk: e.target.value })}
          />
          {trade.defined_risk != null && parseFloat(draft.planned_risk) !== trade.defined_risk ? (
            <button
              type="button"
              className="chip"
              style={{ alignSelf: "flex-start", marginTop: 4 }}
              title="defined-risk max loss, computed from the legs (width − credit)"
              onClick={() => set({ planned_risk: String(trade.defined_risk) })}
            >
              use width − credit: {money(trade.defined_risk, { sign: false })}
            </button>
          ) : null}
          {trade.two_x_credit != null && parseFloat(draft.planned_risk) !== trade.two_x_credit ? (
            <button
              type="button"
              className="chip"
              style={{ alignSelf: "flex-start", marginTop: 4 }}
              title="manage-at-2×-credit-loss rule of thumb — the planned risk for undefined-risk short premium"
              onClick={() => set({ planned_risk: String(trade.two_x_credit) })}
            >
              use 2× credit: {money(trade.two_x_credit, { sign: false })}
            </button>
          ) : null}
        </div>
        <div className="field" style={{ justifyContent: "center" }}>
          <span>Assignment is the plan</span>
          <label className="row" style={{ gap: 8, alignItems: "center", cursor: "pointer", minHeight: 40 }}>
            <input
              type="checkbox"
              style={{ width: 18, height: 18, minHeight: 0 }}
              checked={draft.assignment_intent}
              onChange={(e) => set({ assignment_intent: e.target.checked })}
            />
            <span className="muted small">
              CSP sold wanting the shares — getting assigned is a fill, not a loss.
              Risk reads as capital committed (strike × 100).
            </span>
          </label>
        </div>
      </div>

      <h3 style={{ margin: "12px 0 6px" }}>
        Tags <span className="muted small" style={{ fontWeight: 400 }}>— tap to apply/remove, saved instantly</span>
      </h3>
      <div className="chips">
        {allTags.map((t) => {
          const on = tagIds.has(t.id);
          return (
            <button
              key={t.id}
              type="button"
              className={"chip" + (on ? " on" : "")}
              style={on ? { color: t.color, background: t.color + "26", borderColor: t.color } : { borderColor: t.color + "66" }}
              onClick={() => toggleTag(t.id)}
            >
              {t.name}
            </button>
          );
        })}
        <TagComposer onCreate={createTag} />
      </div>
      {allTags.length === 0 ? (
        <p className="muted small" style={{ margin: "6px 0 0" }}>
          Your tag pool is empty — hit “+ new tag”, name it, pick a color. Tags are shared across all
          trades and applied here with a tap.
        </p>
      ) : null}

      <label className="field" style={{ marginTop: 10 }}>
        <span>Why entered</span>
        <AutoTextarea minRows={3} value={draft.why_entered} onChange={(e) => set({ why_entered: e.target.value })} />
      </label>
      <label className="field" style={{ marginTop: 8 }}>
        <span>Reflection</span>
        <AutoTextarea minRows={3} value={draft.reflection} onChange={(e) => set({ reflection: e.target.value })} />
      </label>

      <div className="modal-actions">
        <button className="btn btn-danger" style={{ marginRight: "auto" }} onClick={ungroup}>
          {ungroupArmed ? "Tap again" : "Ungroup"}
        </button>
        {trade.status === "open" && (
          <button className="btn btn-ghost" onClick={openAttach}>
            Attach closing fills
          </button>
        )}
        <button className="btn btn-ghost" onClick={requestClose}>
          {closeArmed ? "Discard changes" : "Close"}
        </button>
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
