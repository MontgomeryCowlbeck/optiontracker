/* Open positions with live valuation. Trades are auto-detected on sync —
   this view is for watching the open book and annotating, not for grouping. */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { Edge, Position, PositionsSummary, TradeIntent } from "../api/types";
import { ConsultChat } from "../components/ConsultChat";
import { DDPanel } from "../components/DDPanel";
import { ForwardCalendar } from "../components/ForwardCalendar";
import { TradeEditor } from "../components/TradeEditor";
import { FilterPill, FilterReset, FilterSeg } from "../components/filters";
import { AutoTextarea, ConvictionPicker, Empty, Modal, Pnl, Skeleton, StatTile } from "../components/ui";
import { useNarrow } from "../lib/device";
import { conviction, dteFrom, money, num, pct, strategyLabel, tagBadgeStyle, timeShort } from "../lib/format";
import { useAuth, useToast } from "../state/store";

const REFRESH_MS = 60_000;

/* ---------------- sort / filter (client-side — the open book is small) ---------------- */

/* null-safe sort helpers: unmarked positions always sink to the bottom */
const lo = (v: number | null | undefined) => (v == null ? Infinity : v); // for ascending
const hi = (v: number | null | undefined) => (v == null ? -Infinity : v); // for descending

const SORTS: { id: string; label: string; cmp: (a: Position, b: Position) => number }[] = [
  { id: "newest", label: "Newest first", cmp: (a, b) => String(b.entry_at ?? "").localeCompare(String(a.entry_at ?? "")) },
  { id: "worst", label: "Worst P&L first", cmp: (a, b) => lo(a.unrealized_pnl) - lo(b.unrealized_pnl) },
  { id: "best", label: "Best P&L first", cmp: (a, b) => hi(b.unrealized_pnl) - hi(a.unrealized_pnl) },
  { id: "pt", label: "Closest to 50% PT", cmp: (a, b) => hi(b.pct_of_credit) - hi(a.pct_of_credit) },
  { id: "dte", label: "Nearest expiry", cmp: (a, b) => String(a.expiration ?? "9999").localeCompare(String(b.expiration ?? "9999")) },
  { id: "risk", label: "Biggest max loss", cmp: (a, b) => hi(b.max_loss) - hi(a.max_loss) },
  { id: "ticker", label: "Ticker A–Z", cmp: (a, b) => String(a.underlying ?? "").localeCompare(String(b.underlying ?? "")) },
];

/* Profit-% buckets over the server's pct_of_credit (unrealized P&L as % of
   entry credit). Unmarked positions drop out while a bucket is active. */
const PROFIT_FILTERS: { value: string; short: string; label: string; hint: string; test: (v: number) => boolean }[] = [
  { value: "pt", short: "≥ 50%", label: "At profit target", hint: "kept ≥ 50% of the credit — manage it", test: (v) => v >= 50 },
  { value: "win", short: "> 0%", label: "Profitable", hint: "any unrealized gain", test: (v) => v > 0 },
  { value: "loss", short: "< 0%", label: "Under water", hint: "any unrealized loss", test: (v) => v < 0 },
  { value: "deep", short: "≤ −100%", label: "Loss exceeds credit", hint: "deeper red than the credit taken in", test: (v) => v <= -100 },
];

/* ---------------- per-position consult ---------------- */

/* Entry points for managing a live trade — the backend pins this exact
   position as the subject, so the agent skips the intent interview. */
const POSITION_STARTERS = (p: Position) => [
  {
    title: "Evaluate current state",
    desc: "where this trade stands vs the plan",
    msg: `Evaluate my open ${p.underlying} ${p.strikes ?? ""} position — where does it stand right now versus the plan?`,
  },
  {
    title: "Walk me through managing it",
    desc: "hold toward 50%, close, or roll",
    msg: "Walk me through managing this position from here — hold toward the 50% target, close, or roll? Give me the decision points.",
  },
  {
    title: "Take profit?",
    desc: "is the remaining credit worth the risk",
    msg: "Should I take profit on this position here, or is the remaining credit still worth the risk left on the table?",
  },
  {
    title: "Defense plan",
    desc: "what to do if it moves against me",
    msg: "If this position moves against me, what's the defense? Walk me through the scenarios and the levels that matter.",
  },
];

function PositionConsult({ p }: { p: Position }) {
  return (
    <ConsultChat
      intro={`The desk reads this position's latest marks and greeks, the DD on ${p.underlying}, the live put
        chain, and your journal history. Marks refresh every 2 minutes during market hours — outside them
        it will say how stale they are. Every number it cites is code-computed; the judgment is its own.`}
      starters={POSITION_STARTERS(p)}
      send={async (message) => {
        const r = await api<{ reply: string }>(`/journal/positions/${p.id}/consult`, {
          method: "POST",
          body: { message },
        });
        return r.reply;
      }}
      load={async () => {
        const r = await api<{ messages: { role: string; content: string }[] }>(
          `/journal/positions/${p.id}/consult`,
        );
        return r.messages;
      }}
      onClear={async () => {
        await api(`/journal/positions/${p.id}/consult`, { method: "DELETE" });
      }}
    />
  );
}

/* Progress toward the 50% profit target on a credit trade. */
export function PtBar({ pctOfCredit }: { pctOfCredit: number }) {
  const fill = Math.max(0, Math.min(100, pctOfCredit));
  const hit = pctOfCredit >= 50;
  return (
    <div className="pt-wrap" title={`${num(pctOfCredit, 1)}% of entry credit kept · manage at 50%`}>
      <div className="pt-bar">
        <div className={"pt-fill" + (hit ? " hit" : "")} style={{ width: `${fill}%` }} />
        <div className="pt-mark" />
      </div>
      <span className="pt-lbl num">
        {num(pctOfCredit, 0)}% <span className="muted">/ 50% PT</span>
      </span>
    </div>
  );
}

function Legs({ p }: { p: Position }) {
  const narrow = useNarrow(640);
  if (narrow) {
    // stacked blocks: the 7-column table + 21-char OCC symbols made a nested
    // horizontal scroller inside an expanded row
    return (
      <div className="stack" style={{ gap: 6, marginBottom: 8 }}>
        {p.legs.map((l) => (
          <div key={l.option_symbol} style={{ borderBottom: "1px solid var(--grid)", paddingBottom: 6 }}>
            <div className="num small" style={{ color: "var(--ink-2)", overflowWrap: "anywhere" }}>
              {l.option_symbol} <b style={{ color: "var(--ink)" }}>{l.net > 0 ? `+${l.net}` : l.net}</b>
            </div>
            <div className="muted small num">
              mark {l.mark != null ? money(l.mark, { sign: false }) : "—"}
              {" · "}Δ {l.delta != null ? num(Number(l.delta)) : "—"}
              {" · "}θ {l.theta != null ? num(Number(l.theta)) : "—"}
              {" · "}IV {l.iv != null ? pct(Number(l.iv) * 100) : "—"}
              {l.as_of ? ` · ${timeShort(l.as_of)}` : ""}
            </div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table className="mini-table">
        <thead>
          <tr>
            <th>Leg</th>
            <th className="n">Net</th>
            <th className="n">Mark</th>
            <th className="n">Δ</th>
            <th className="n">θ</th>
            <th className="n">IV</th>
            <th>As of</th>
          </tr>
        </thead>
        <tbody>
          {p.legs.map((l) => (
            <tr key={l.option_symbol}>
              <td className="num">{l.option_symbol}</td>
              <td className="n">{l.net > 0 ? `+${l.net}` : l.net}</td>
              <td className="n">
                {l.mark != null ? money(l.mark, { sign: false }) : <span className="muted">no mark yet</span>}
              </td>
              <td className="n">{l.delta != null ? num(Number(l.delta)) : "—"}</td>
              <td className="n">{l.theta != null ? num(Number(l.theta)) : "—"}</td>
              <td className="n">{l.iv != null ? pct(Number(l.iv) * 100) : "—"}</td>
              <td className="muted">{l.as_of ? timeShort(l.as_of) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* Per-position stat facts: greeks, risk, breakeven, time in trade. Only what exists. */
function PositionStats({ p }: { p: Position }) {
  const bits: { label: string; val: ReactNode; title?: string }[] = [];
  if (p.position_delta != null)
    bits.push({ label: "Δ", val: num(p.position_delta, 0), title: "net position delta (share-equivalent)" });
  if (p.position_theta != null)
    bits.push({ label: "θ/day", val: money(p.position_theta), title: "daily theta decay in your favor when positive" });
  if (p.days_open != null) bits.push({ label: "held", val: `${p.days_open}d` });
  if (p.assignment_intent && p.assignment_capital != null)
    bits.push({
      label: "if assigned",
      val: money(p.assignment_capital, { sign: false }),
      title: "assignment is the plan — this is the cash that buys the shares (strike × 100 × contracts)",
    });
  else if (p.max_loss != null)
    bits.push({ label: "max loss", val: money(p.max_loss, { sign: false }), title: "defined risk: width − credit" });
  if (p.return_on_risk != null)
    bits.push({ label: "RoR", val: pct(p.return_on_risk, 0), title: "credit / max loss" });
  if (p.breakevens?.length)
    bits.push({
      label: "BE",
      title: "breakeven (short strike ∓ credit/share)",
      val: p.breakevens.map((b) => `$${num(b)}`).join(" / "),
    });
  if (p.pct_of_credit != null)
    bits.push({
      label: "profit",
      title: "unrealized P&L as % of entry credit · manage at 50%",
      val: (
        <span className={p.pct_of_credit >= 0 ? "pnl win" : "pnl loss"}>
          {num(p.pct_of_credit, 0)}%
        </span>
      ),
    });
  if (p.day_pnl != null)
    bits.push({
      label: "day",
      title: "unrealized change since the prior session's last mark",
      val: <span className={p.day_pnl >= 0 ? "pnl win" : "pnl loss"}>{money(p.day_pnl)}</span>,
    });
  if (!bits.length) return null;
  return (
    <span className="muted small num pos-stats">
      {bits.map((b) => (
        <span key={b.label} title={b.title}>
          {b.label} <b style={{ color: "var(--ink-2)" }}>{b.val}</b>
        </span>
      ))}
    </span>
  );
}

/* DTE pill graded by the management clock: green outside 21 DTE, amber inside
   it (the 21-DTE window), red in the final week. */
function DtePill({ dte }: { dte: number }) {
  const cls = dte <= 7 ? "dte-urgent" : dte <= 21 ? "dte-soon" : "dte-ok";
  return <span className={`badge ${cls}`}>{dte} DTE</span>;
}

/* Moneyness pill: distance of spot to the nearest SHORT strike, risk-graded. */
function MoneynessPill({ p }: { p: Position }) {
  if (!p.moneyness) return null;
  const cls = { OTM: "mny-otm", ATM: "mny-atm", ITM: "mny-itm" }[p.moneyness];
  const title =
    p.cushion_pct != null
      ? `spot ${p.spot != null ? "$" + num(p.spot) + " " : ""}is ${num(Math.abs(p.cushion_pct), 1)}% ${p.cushion_pct >= 0 ? "from" : "past"} the short strike`
      : "graded from short-leg delta (no spot yet)";
  return (
    <span className={`badge ${cls}`} title={title}>
      {p.moneyness}
    </span>
  );
}

/* Row overflow menu — direct actions without expanding the row. Sits outside
   the row <button> (nested buttons are invalid HTML). */
function RowMenu({ items }: { items: { label: string; onClick: () => void }[] }) {
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);
  useEffect(() => {
    if (!open) return;
    // pointerdown, not click — iOS doesn't synthesize click on non-interactive
    // elements, so an outside tap never closed the menu
    const close = () => setOpen(false);
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);
  return (
    <div className="kebab" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
      <button
        className="kebab-btn"
        aria-label="Position actions"
        onClick={(e) => {
          // open upward when the row sits near the viewport bottom, else the
          // menu renders under the fixed bottom nav / off-screen
          setUp(e.currentTarget.getBoundingClientRect().bottom > window.innerHeight - 220);
          setOpen(!open);
        }}
      >
        ⋮
      </button>
      {open && (
        <div className={"kebab-menu" + (up ? " up" : "")}>
          {items.map((it) => (
            <button
              key={it.label}
              onClick={() => {
                setOpen(false);
                it.onClick();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function PositionRow({
  p,
  open,
  onToggle,
  onChanged,
  onConsult,
  onDeepDive,
}: {
  p: Position;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onConsult?: () => void;
  onDeepDive?: () => void;
}) {
  const dte = dteFrom(p.expiration);
  const noMarks = p.unrealized_pnl == null;
  const menuItems = [
    { label: open ? "Close journal" : "Journal & tags", onClick: onToggle },
    ...(onConsult ? [{ label: "Consult the desk", onClick: onConsult }] : []),
    ...(onDeepDive && p.underlying ? [{ label: `Deep dive ${p.underlying}`, onClick: onDeepDive }] : []),
  ];
  return (
    <div className={"entry-wrap" + (open ? " open" : "")}>
      <RowMenu items={menuItems} />
      <button className="entry" onClick={onToggle}>
        <div className="tab" style={{ background: "var(--accent)" }} />
        <div className="body">
          <div className="meta">
            <span className="sym">{p.underlying || "?"}</span>
            {p.spot != null ? <span className="muted small num">@ ${num(p.spot)}</span> : null}
            {p.direction ? <span className="badge">{strategyLabel(p.direction)}</span> : null}
            <span className="badge">{p.strikes}</span>
            <MoneynessPill p={p} />
            {dte != null ? <DtePill dte={dte} /> : null}
            <span className="muted small num">exp {p.expiration}</span>
            {p.edge_name ? <span className="badge">{p.edge_name}</span> : null}
            {(p.tags || []).map((tag) => (
              <span key={tag.id} className="badge" style={tagBadgeStyle(tag.color)}>
                {tag.name}
              </span>
            ))}
            {p.is_system != null &&
              (p.is_system ? <span className="badge sys">system</span> : <span className="badge disc">discretionary</span>)}
            {(p.consult_turns ?? 0) > 0 ? (
              <span className="badge" title="a desk consult conversation is on file — open the position to resume it">
                desk chat
              </span>
            ) : null}
            {p.conviction ? <span className="conviction">{conviction(p.conviction)}</span> : null}
          </div>
          <div className="pos-line num">
            <span>
              credit <b>{money(p.entry_premium, { sign: false })}</b>
            </span>
            <span>
              liq{" "}
              <b>{p.liquidation_value != null ? money(p.liquidation_value, { sign: false }) : "—"}</b>
            </span>
            {p.pct_of_credit != null && !noMarks ? <PtBar pctOfCredit={p.pct_of_credit} /> : null}
          </div>
          <PositionStats p={p} />
        </div>
        <div className="side">
          {noMarks ? (
            <span className="muted small">no mark yet (polls market hours)</span>
          ) : (
            <Pnl v={p.unrealized_pnl} />
          )}
          <span className="muted small">unrealized</span>
        </div>
      </button>
      {open && (
        <div className="trade-editor">
          <Legs p={p} />
          {p.marks_missing.length > 0 && (
            <p className="muted small">
              awaiting marks for {p.marks_missing.join(", ")} — the poller runs during market hours.
            </p>
          )}
          {onConsult && (
            <div className="consult-cta">
              <button className="btn btn-primary btn-sm" onClick={onConsult}>
                {(p.consult_turns ?? 0) > 0 ? "Resume consult" : "Consult the desk"}
              </button>
              <span className="muted small">
                {(p.consult_turns ?? 0) > 0
                  ? `pick up your conversation with the desk (${p.consult_turns} messages on file)`
                  : "evaluate this position and walk through managing it"}
              </span>
            </div>
          )}
          <TradeEditor tradeId={p.id} onClose={onToggle} onChanged={onChanged} />
        </div>
      )}
    </div>
  );
}

/* ---------------- pre-trade intents ---------------- */

const INTENT_STRUCTURES = [
  "put_credit_spread", "short_put", "covered_call", "call_credit_spread",
  "iron_condor", "short_strangle", "short_straddle",
];

/* State the plan BEFORE the fill exists. The tracker binds the next matching
   trade to it and pre-fills the journal — entry journaling becomes a tap. */
function IntentComposer({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const toast = useToast();
  const [mechs, setMechs] = useState<Edge[]>([]);
  const [underlying, setUnderlying] = useState("");
  const [direction, setDirection] = useState("");
  const [edgeId, setEdgeId] = useState<number | null>(null);
  const [risk, setRisk] = useState("");
  const [conviction, setConviction] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ edges: Edge[] }>("/journal/edges")
      .then((r) => setMechs(r.edges || []))
      .catch(() => {});
  }, []);

  const create = async () => {
    if (!underlying.trim() || busy) return;
    setBusy(true);
    try {
      await api("/journal/intents", {
        method: "POST",
        body: {
          underlying: underlying.trim().toUpperCase(),
          direction: direction || null,
          edge_id: edgeId,
          planned_risk: risk.trim() === "" ? null : parseFloat(risk),
          conviction,
          note: note.trim() || null,
        },
      });
      toast("Intent logged — the next matching fill binds to it");
      onCreated();
      onClose();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Plan a trade" onClose={onClose}>
      <p className="muted small" style={{ marginTop: 0 }}>
        Journal the intent before the fill exists. When a matching trade arrives from Tastytrade
        (same underlying + structure, within 7 days), it inherits everything below — entry
        journaling done before entry.
      </p>
      <div className="field-grid">
        <label className="field">
          <span>Underlying</span>
          <input autoFocus value={underlying} placeholder="QQQ"
                 autoCapitalize="characters" autoCorrect="off" spellCheck={false}
                 autoComplete="off" enterKeyHint="next"
                 onChange={(e) => setUnderlying(e.target.value.toUpperCase())} />
        </label>
        <label className="field">
          <span>Structure</span>
          <select value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="">any</option>
            {INTENT_STRUCTURES.map((s) => (
              <option key={s} value={s}>{strategyLabel(s)}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Edge</span>
          <select value={edgeId ?? ""}
                  onChange={(e) => setEdgeId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">— none —</option>
            {mechs.map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Planned risk $</span>
          <input inputMode="decimal" enterKeyHint="done" value={risk}
                 placeholder="max planned loss" onChange={(e) => setRisk(e.target.value)} />
        </label>
        <div className="field">
          <span>Conviction</span>
          <ConvictionPicker value={conviction} onChange={setConviction} />
        </div>
      </div>
      <label className="field" style={{ marginTop: 8 }}>
        <span>Why (becomes “why entered”)</span>
        <AutoTextarea minRows={3} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <div className="modal-actions">
        <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" onClick={create} disabled={busy || !underlying.trim()}>
          Log intent
        </button>
      </div>
    </Modal>
  );
}

function IntentStrip({ intents, onChanged }: { intents: TradeIntent[]; onChanged: () => void }) {
  const toast = useToast();
  if (!intents.length) return null;
  const cancel = async (id: number) => {
    try {
      await api(`/journal/intents/${id}`, { method: "DELETE" });
      onChanged();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };
  return (
    <div className="chips" style={{ marginBottom: 10 }}>
      <span className="muted small">planned:</span>
      {intents.map((i) => (
        <span key={i.id} className="chip" title={i.note || undefined}>
          {i.underlying}
          {i.direction ? ` ${strategyLabel(i.direction)}` : ""}
          {i.planned_risk != null ? ` · $${num(i.planned_risk, 0)} risk` : ""}
          <button className="x"
                  style={{ border: 0, background: "none", color: "inherit", cursor: "pointer", font: "inherit",
                           minWidth: 32, minHeight: 32, margin: "-6px -8px -6px 0",
                           display: "inline-flex", alignItems: "center", justifyContent: "center" }}
                  aria-label="Cancel intent" onClick={() => cancel(i.id)}>
            ×
          </button>
        </span>
      ))}
    </div>
  );
}

export function Positions() {
  const { accountEpoch } = useAuth();
  const [positions, setPositions] = useState<Position[] | null>(null);
  const [summary, setSummary] = useState<PositionsSummary | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [consult, setConsult] = useState<Position | null>(null);
  const [deepDive, setDeepDive] = useState<string | null>(null);
  const [intents, setIntents] = useState<TradeIntent[]>([]);
  const [planOpen, setPlanOpen] = useState(false);
  const [error, setError] = useState("");
  const [tick, setTick] = useState("");
  const [strat, setStrat] = useState("");
  const [sleeve, setSleeve] = useState<"" | "system" | "disc">("");
  const [profit, setProfit] = useState("");
  const [sortId, setSortId] = useState("newest");

  const load = useCallback(async () => {
    try {
      const r = await api<{ positions: Position[]; summary: PositionsSummary }>("/journal/positions");
      setPositions(r.positions || []);
      setSummary(r.summary || null);
      setError("");
    } catch (e) {
      setError((e as Error).message);
      setPositions([]);
    }
    api<{ intents: TradeIntent[] }>("/journal/intents")
      .then((r) => setIntents(r.intents || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    setPositions(null);
    load();
    const timer = setInterval(load, REFRESH_MS);
    const onSync = () => load();
    window.addEventListener("pt:synced", onSync);
    return () => {
      clearInterval(timer);
      window.removeEventListener("pt:synced", onSync);
    };
  }, [load, accountEpoch]);

  const tickers = useMemo(
    () => [...new Set((positions ?? []).map((p) => p.underlying).filter(Boolean))].sort() as string[],
    [positions],
  );
  const strategies = useMemo(
    () => [...new Set((positions ?? []).map((p) => p.direction).filter(Boolean))].sort() as string[],
    [positions],
  );
  const shown = useMemo(() => {
    let out = positions ?? [];
    if (tick) out = out.filter((p) => p.underlying === tick);
    if (strat) out = out.filter((p) => p.direction === strat);
    // anything not tagged "system" counts as discretionary, including untagged
    if (sleeve) out = out.filter((p) => (sleeve === "system") === !!p.is_system);
    const pf = PROFIT_FILTERS.find((f) => f.value === profit);
    if (pf) out = out.filter((p) => p.pct_of_credit != null && pf.test(p.pct_of_credit));
    const sort = SORTS.find((s) => s.id === sortId) ?? SORTS[0];
    return [...out].sort(sort.cmp);
  }, [positions, tick, strat, sleeve, profit, sortId]);
  const filtered = Boolean(tick || strat || sleeve || profit);

  if (positions === null) return <Skeleton h={90} n={3} />;
  if (error) return <Empty>{error}</Empty>;

  return (
    <div>
      <div className="page-header">
        <h1>Positions</h1>
        <div className="row">
          {summary && summary.count > 0 && (
            <span className="muted small num">
              {summary.count} open
              {summary.marked_count < summary.count
                ? ` (${summary.count - summary.marked_count} unmarked)`
                : ""}
            </span>
          )}
          <button className="btn btn-ghost btn-sm" onClick={() => setPlanOpen(true)}>
            Plan a trade
          </button>
        </div>
      </div>
      <p className="muted small" style={{ marginTop: -8 }}>
        The open book, valued at the latest market-hours marks. Trades appear here automatically on sync —
        your job is the annotation, not the bookkeeping.
      </p>

      {summary && summary.count > 0 ? (
        <div className="tiles" style={{ marginBottom: 12 }}>
          <StatTile label="Unrealized P&L" value={<Pnl v={summary.total_unrealized} />} sub={`${summary.marked_count}/${summary.count} marked`} />
          <StatTile
            label="Day P&L"
            value={<Pnl v={summary.total_day_pnl} />}
            sub={
              summary.day_covered < summary.count
                ? `${summary.day_covered}/${summary.count} covered`
                : "since prior session close"
            }
          />
          <StatTile label="Credit collected" value={money(summary.total_credit, { sign: false })} />
          <StatTile
            label="Liquidation value"
            value={summary.total_liquidation != null ? money(summary.total_liquidation, { sign: false }) : "—"}
            sub="what closing everything costs now"
          />
          <StatTile
            label="Net Δ"
            value={summary.net_delta != null ? num(summary.net_delta, 0) : "—"}
            sub={summary.greeks_covered < summary.count ? `${summary.greeks_covered}/${summary.count} covered` : "share-equivalent"}
          />
          <StatTile
            label="θ / day"
            value={summary.net_theta != null ? money(summary.net_theta) : "—"}
            sub="decay in your favor when positive"
          />
          <StatTile
            label="Defined risk"
            value={summary.defined_risk_total != null ? money(summary.defined_risk_total, { sign: false }) : "—"}
            sub={
              summary.defined_risk_count < summary.count
                ? `${summary.count - summary.defined_risk_count} position(s) undefined`
                : "total max loss"
            }
          />
        </div>
      ) : null}

      {positions.length > 1 && (
        <div className="fbar">
          <FilterPill
            label="Ticker"
            value={tick || null}
            options={tickers.map((t) => ({ value: t, label: t }))}
            onChange={(v) => setTick(v ?? "")}
          />
          {strategies.length > 1 && (
            <FilterPill
              label="Strategy"
              allLabel="All strategies"
              value={strat || null}
              options={strategies.map((d) => ({ value: d, label: strategyLabel(d) }))}
              onChange={(v) => setStrat(v ?? "")}
            />
          )}
          <FilterSeg
            label="Sleeve"
            options={[["", "All"], ["system", "System"], ["disc", "Discretionary"]] as const}
            value={sleeve}
            onChange={setSleeve}
          />
          <FilterPill
            label="Profit"
            allLabel="Any profit %"
            value={profit || null}
            options={PROFIT_FILTERS.map((f) => ({ value: f.value, label: f.label, short: f.short, hint: f.hint }))}
            onChange={(v) => setProfit(v ?? "")}
          />
          <FilterPill
            label="Sort"
            clearable={false}
            value={sortId}
            options={SORTS.map((s) => ({ value: s.id, label: s.label }))}
            onChange={(v) => setSortId(v ?? "newest")}
          />
          {filtered && (
            <FilterReset
              shown={shown.length}
              total={positions.length}
              onClear={() => {
                setTick("");
                setStrat("");
                setSleeve("");
                setProfit("");
              }}
            />
          )}
        </div>
      )}

      <IntentStrip intents={intents} onChanged={load} />

      {positions.length === 0 ? (
        <Empty hint="Flat is a position too. New fills become trades automatically on the next sync.">
          No open positions.
        </Empty>
      ) : shown.length === 0 ? (
        <Empty hint="Nothing in the open book matches these filters.">No matches.</Empty>
      ) : (
        <div className="stack">
          {shown.map((p) => (
            <PositionRow
              key={p.id}
              p={p}
              open={openId === p.id}
              onToggle={() => setOpenId(openId === p.id ? null : p.id)}
              onChanged={() => load()}
              onConsult={() => setConsult(p)}
              onDeepDive={() => setDeepDive(p.underlying || null)}
            />
          ))}
        </div>
      )}
      <p className="chart-note">Refreshes every 60s while open.</p>

      <ForwardCalendar />

      {planOpen ? <IntentComposer onClose={() => setPlanOpen(false)} onCreated={load} /> : null}

      {deepDive ? (
        <Modal wide dismissable title={`Deep dive — ${deepDive}`} onClose={() => setDeepDive(null)}>
          <DDPanel symbol={deepDive} />
        </Modal>
      ) : null}

      {consult ? (
        <Modal
          wide
          title={
            <span>
              Consult the desk{" "}
              <span className="muted" style={{ fontWeight: 400 }}>
                — {consult.underlying} {consult.strikes} · exp {consult.expiration}
              </span>
            </span>
          }
          onClose={() => setConsult(null)}
        >
          <PositionConsult p={consult} />
        </Modal>
      ) : null}
    </div>
  );
}
