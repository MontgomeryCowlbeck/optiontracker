/* Strangle tooling — vol watch, metric legend, defense playbook, and the ETF
   screener. Lived on the Strangles tab until 2026-07-27; now sections of the
   Morning digest (consolidation: one page to open pre-market).

   DECISION SUPPORT, NOT A SIGNAL. The research program's VRP-z entry looked
   ~2× on train and FAILED its one holdout shot (train +$104/trade → holdout
   −$77; ledger e_strat2_strangle_holdout_capstone_20260719, 2026 burned).
   These sections show conditions and live math, carry that label loudly, and
   annotate the TT defense ladder with what our own experiments measured. */
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { StrangleRow, VolWatchRow } from "../api/types";
import { Empty, Skeleton } from "../components/ui";
import { useNarrow } from "../lib/device";
import { fmtDayShort, money, num } from "../lib/format";
import { useAuth, useToast } from "../state/store";

/* ---------------- vol watch — marked names, all the juice ---------------- */

function pctS(v: number | null | undefined, dp = 1): string {
  return v == null ? "—" : num(v * 100, dp) + "%";
}

function daysTo(d?: string | null): number | null {
  if (!d) return null;
  const t = new Date(d.slice(0, 10) + "T00:00:00").getTime() - new Date().setHours(0, 0, 0, 0);
  return Math.round(t / 86400000);
}

/* juicy = the conditions a premium seller actually hunts */
function juice(r: VolWatchRow): { label: string; cls: string }[] {
  const out: { label: string; cls: string }[] = [];
  if (r.iv_rv != null && r.iv_rv >= 1.3) out.push({ label: `IV/RV ${num(r.iv_rv)}`, cls: "warn" });
  if (r.ivr != null && r.ivr >= 50) out.push({ label: `IVR ${num(r.ivr, 0)}`, cls: "warn" });
  if (r.term_ratio != null && r.term_ratio >= 1.03)
    out.push({ label: "backwardation", cls: "warn" });
  const ed = daysTo(r.earnings);
  if (ed != null && ed >= 0 && ed <= 10) out.push({ label: `earnings ${ed}d`, cls: "good" });
  return out;
}

/* The per-name snapshot history — the crush record. Shared by the desktop
   table (spanned row) and the mobile card layout. */
function VolHistory({ r }: { r: VolWatchRow }) {
  return (
    <div className="table-wrap" style={{ margin: "4px 0 8px" }}>
      <table className="mini-table">
        <thead>
          <tr>
            <th>Date</th><th className="n">Spot</th><th className="n">IV</th>
            <th className="n">RV20</th><th className="n">IV/RV</th>
            <th className="n">Term</th><th className="n">Strdl%</th>
          </tr>
        </thead>
        <tbody>
          {r.history.slice(0, 15).map((s) => (
            <tr key={s.date}>
              <td className="num">{s.date}</td>
              <td className="n num">{money(s.spot, { sign: false })}</td>
              <td className="n num">{pctS(s.iv)}</td>
              <td className="n num">{pctS(s.rv20)}</td>
              <td className="n num">{s.iv_rv ?? "—"}</td>
              <td className="n num">{s.term_ratio ?? "—"}</td>
              <td className="n num">{s.straddle_pct != null ? num(s.straddle_pct) + "%" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint-line" style={{ margin: "4px 0 0" }}>
        The crush record: compare the last pre-earnings row's IV/straddle to the
        day after — that difference is what an IV-crush trade would have earned
        before costs.
      </p>
    </div>
  );
}

export function VolWatchSection() {
  const toast = useToast();
  const { accountEpoch } = useAuth();
  const [rows, setRows] = useState<VolWatchRow[] | null>(null);
  const [symbol, setSymbol] = useState("");
  const [adding, setAdding] = useState(false);
  const [openSym, setOpenSym] = useState<string | null>(null);
  const narrow = useNarrow(767);

  const load = useCallback(async () => {
    try {
      const r = await api<{ rows: VolWatchRow[] }>("/journal/strangle/watch", { timeoutMs: 180_000 });
      setRows(r.rows || []);
    } catch (e) {
      toast((e as Error).message, "err");
      setRows([]);
    }
  }, [toast]);

  useEffect(() => {
    setRows(null);
    load();
  }, [load, accountEpoch]);

  const add = async () => {
    if (!symbol.trim() || adding) return;
    setAdding(true);
    try {
      await api("/journal/strangle/watch", { method: "POST", body: { symbol }, timeoutMs: 120_000 });
      setSymbol("");
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setAdding(false);
    }
  };

  /* Two-tap — the × sits at the row edge and fires a DELETE. */
  const [armedRemove, setArmedRemove] = useState<string | null>(null);
  const remove = async (sym: string) => {
    if (armedRemove !== sym) {
      setArmedRemove(sym);
      window.setTimeout(() => setArmedRemove((cur) => (cur === sym ? null : cur)), 4000);
      return;
    }
    setArmedRemove(null);
    await api(`/journal/strangle/watch/${sym}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h3 className="section-title" style={{ margin: 0 }}>Vol watch</h3>
        <div className="row" style={{ gap: 6 }}>
          <input
            style={{ width: 110 }}
            value={symbol}
            placeholder="NVDA"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            autoComplete="off"
            enterKeyHint="done"
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <button className="btn btn-primary btn-sm" onClick={add} disabled={adding || !symbol.trim()}>
            {adding ? "checking…" : "Mark"}
          </button>
        </div>
      </div>
      <p className="hint-line">
        Marked names snapshot nightly (5:40 PM ET): IV, RV, IV/RV, term structure, ATM straddle %.
        The history is what makes IVR (for non-index names) and post-earnings IV-crush review
        possible — day counts build from when you mark.
      </p>
      {rows === null ? (
        <Skeleton h={50} n={2} />
      ) : rows.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          Nothing marked yet — add a stock or ETF above to start its vol record.
        </p>
      ) : narrow ? (
        /* stacked cards: the 11-column table needed ~800px and put the delete
           button in the last column of an off-screen scroller */
        <div className="stack" style={{ gap: 8 }}>
          {rows.map((r) => {
            const ed = daysTo(r.earnings);
            const open = openSym === r.symbol;
            return (
              <div key={r.symbol} style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "8px 10px" }}>
                <button
                  className="row"
                  style={{ background: "none", border: 0, color: "inherit", font: "inherit", width: "100%", gap: 8, cursor: "pointer", minHeight: 44, padding: 0 }}
                  onClick={() => setOpenSym(open ? null : r.symbol)}
                >
                  <b style={{ color: "var(--ink)" }}>{r.symbol}</b>
                  {r.error ? (
                    <span className="muted small">fetch failed</span>
                  ) : (
                    <>
                      <span className="num small">{money(r.spot, { sign: false })}</span>
                      {juice(r).map((j) => (
                        <span key={j.label} className={"badge " + j.cls}>{j.label}</span>
                      ))}
                    </>
                  )}
                  <span className="muted small" style={{ marginLeft: "auto" }}>{open ? "▾" : "▸"}</span>
                </button>
                {!r.error ? (
                  <div className="muted small num" style={{ marginTop: 2 }}>
                    IV {pctS(r.iv)} · RV20 {pctS(r.rv20)} · IV/RV{" "}
                    <span className={(r.iv_rv ?? 0) >= 1.3 ? "hot" : ""}>{r.iv_rv ?? "—"}</span> · IVR{" "}
                    <span className={(r.ivr ?? 0) >= 50 ? "hot" : ""}>
                      {r.ivr != null ? num(r.ivr, 0) : r.ivr_n != null ? `n=${r.ivr_n}` : "—"}
                    </span>
                    {" "}· term <span className={(r.term_ratio ?? 0) >= 1.03 ? "hot" : ""}>{r.term_ratio ?? "—"}</span>
                    {" "}· strdl {r.straddle_pct != null ? num(r.straddle_pct) + "%" : "—"}
                    {r.earnings ? ` · earn ${fmtDayShort(r.earnings.slice(0, 10))}${ed != null ? ` (${ed}d)` : ""}` : ""}
                  </div>
                ) : (
                  <p className="muted small" style={{ margin: "2px 0 0" }}>{r.error}</p>
                )}
                {open ? (
                  <>
                    {r.history.length > 0 ? <VolHistory r={r} /> : null}
                    <button
                      className={"btn btn-sm " + (armedRemove === r.symbol ? "btn-danger" : "btn-ghost")}
                      style={armedRemove === r.symbol ? undefined : { color: "var(--loss)" }}
                      onClick={() => remove(r.symbol)}
                    >
                      {armedRemove === r.symbol ? "Tap again to remove" : "Remove from watch"}
                    </button>
                  </>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="table-wrap">
          <table className="mini-table">
            <thead>
              <tr>
                <th>Sym</th>
                <th className="n">Spot</th>
                <th className="n" title="30-45d ATM chain IV (front-month when no mid expiry)">IV</th>
                <th className="n">RV20</th>
                <th className="n" title="IV richness vs 20d realized — ≥1.3 is the fat-gap bar">IV/RV</th>
                <th className="n" title="IV rank over 252d (vol index for ETFs; our own snapshots otherwise)">IVR</th>
                <th className="n" title="front ATM IV / back ATM IV — >1 = backwardation, event priced in">Term</th>
                <th className="n" title="front ATM straddle as % of spot — the market's expected move">Strdl%</th>
                <th>Earnings</th>
                <th>Juice</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const ed = daysTo(r.earnings);
                return [
                  <tr key={r.symbol} style={{ cursor: "pointer" }} onClick={() => setOpenSym(openSym === r.symbol ? null : r.symbol)}>
                    <td className="sym">{r.symbol}</td>
                    {r.error ? (
                      <td colSpan={9} className="muted small">fetch failed: {r.error}</td>
                    ) : (
                      <>
                        <td className="n num">{money(r.spot, { sign: false })}</td>
                        <td className="n num">{pctS(r.iv)}</td>
                        <td className="n num">{pctS(r.rv20)}</td>
                        <td className={"n num" + ((r.iv_rv ?? 0) >= 1.3 ? " hot" : "")}>{r.iv_rv ?? "—"}</td>
                        <td className={"n num" + ((r.ivr ?? 0) >= 50 ? " hot" : "")}>
                          {r.ivr != null ? num(r.ivr, 0) : r.ivr_n != null ? `n=${r.ivr_n}` : "—"}
                        </td>
                        <td className={"n num" + ((r.term_ratio ?? 0) >= 1.03 ? " hot" : "")}>{r.term_ratio ?? "—"}</td>
                        <td className="n num">{r.straddle_pct != null ? num(r.straddle_pct) + "%" : "—"}</td>
                        <td className="num small">
                          {r.earnings ? `${fmtDayShort(r.earnings.slice(0, 10))}${ed != null ? ` (${ed}d)` : ""}` : "—"}
                        </td>
                        <td>
                          <span className="chips" style={{ gap: 4 }}>
                            {juice(r).map((j) => (
                              <span key={j.label} className={"badge " + j.cls}>{j.label}</span>
                            ))}
                          </span>
                        </td>
                      </>
                    )}
                    <td>
                      <button
                        className={"btn btn-sm " + (armedRemove === r.symbol ? "btn-danger" : "btn-ghost")}
                        style={armedRemove === r.symbol ? undefined : { color: "var(--loss)" }}
                        onClick={(e) => { e.stopPropagation(); remove(r.symbol); }}
                      >
                        {armedRemove === r.symbol ? "sure?" : "×"}
                      </button>
                    </td>
                  </tr>,
                  openSym === r.symbol && r.history.length > 0 ? (
                    <tr key={r.symbol + "-hist"}>
                      <td colSpan={11}>
                        <VolHistory r={r} />
                      </td>
                    </tr>
                  ) : null,
                ];
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ZBadge({ z, rich }: { z: number | null | undefined; rich?: boolean }) {
  if (z == null) return <span className="badge off">VRP-z —</span>;
  const cls = rich ? "warn" : z <= -0.5 ? "off" : "";
  return (
    <span className={"badge " + cls} title="z-score of (vol-index IV − trailing RV20) over 252d — the train-period entry framing; it did NOT survive its holdout">
      VRP-z {z >= 0 ? "+" : ""}{num(z)}
    </span>
  );
}

function RowCard({ r }: { r: StrangleRow }) {
  const [open, setOpen] = useState(false);
  if (r.error) {
    return (
      <div className="card" style={{ marginBottom: 10 }}>
        <b>{r.symbol}</b> <span className="muted small">screen failed: {r.error}</span>
      </div>
    );
  }
  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <button
        className="row"
        style={{ background: "none", border: 0, color: "inherit", font: "inherit", width: "100%", gap: 10, cursor: "pointer", alignItems: "baseline", minHeight: 44 }}
        onClick={() => setOpen(!open)}
      >
        <span className="sym" style={{ fontSize: 17 }}>{r.symbol}</span>
        <span className="num">{money(r.spot, { sign: false })}</span>
        <ZBadge z={r.vrp_z} rich={r.rich} />
        <span className="muted small num">
          IV {r.iv != null ? num(r.iv * 100, 1) + "%" : "—"} · RV20{" "}
          {r.rv20 != null ? num(r.rv20 * 100, 1) + "%" : "—"} · IV/RV{" "}
          <span className={(r.gap_ratio ?? 0) >= 1.3 ? "hot" : ""}>{r.gap_ratio ?? "—"}</span>
          {" "}· IVR{" "}
          <span className={(r.ivr ?? 0) >= 50 ? "hot" : ""} title="IV rank: where today's vol-index level sits in its 252-day range">
            {r.ivr != null ? num(r.ivr, 0) : "—"}
          </span>
        </span>
        {r.expected_move != null ? (
          <span className="muted small num" title={`1σ expected move to ${r.dte} DTE at the vol-index IV`}>
            ±{money(r.expected_move, { sign: false })}
          </span>
        ) : null}
        <span className="muted small" style={{ marginLeft: "auto" }}>
          {r.expiry ? `${r.expiry} · ${r.dte} DTE` : "no chain"} {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (r.constructions || []).length > 0 ? (
        <div className="table-wrap" style={{ marginTop: 8 }}>
          <table className="mini-table">
            <thead>
              <tr>
                <th>Construction</th>
                <th className="n">Put</th>
                <th className="n">Call</th>
                <th className="n">Credit</th>
                <th className="n">Breakevens</th>
              </tr>
            </thead>
            <tbody>
              {(r.constructions || []).map((c) => (
                <tr key={c.label}>
                  <td>{c.label}</td>
                  <td className="n num">
                    {num(c.put_strike)} <span className="muted">({num(c.put_delta)}Δ · {c.put_mid != null ? money(c.put_mid, { sign: false }) : "—"})</span>
                  </td>
                  <td className="n num">
                    {num(c.call_strike)} <span className="muted">({num(c.call_delta)}Δ · {c.call_mid != null ? money(c.call_mid, { sign: false }) : "—"})</span>
                  </td>
                  <td className="n num">{money(c.credit, { sign: false })}</td>
                  <td className="n num">{num(c.be_low)} / {num(c.be_high)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small" style={{ margin: "6px 0 0" }}>
            Deltas are screener-grade BS estimates from yfinance row IVs — confirm on the live TT
            chain before any order. Naked strangles carry undefined call-side risk; the research
            found the call is where the tail lives.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/* Plain-language legend for every metric in these sections. */
const LEGEND: { term: string; def: string }[] = [
  { term: "IV", def: "implied volatility — the annualized move the options market is charging for. 20% ≈ the market pricing ±20% over a year (±1.25%/day-ish). For the ETFs it's their CBOE vol index (VIX/VXN/VXD); on the vol watch it's the chain's 30-45-day ATM IV." },
  { term: "RV20", def: "realized volatility — how much the stock ACTUALLY moved over the last 20 trading days, annualized the same way. The premium seller's cost of goods." },
  { term: "IV/RV", def: "what you're paid vs what it's been moving. 1.0 = fairly priced; ≥1.3 (highlighted) is the fat-gap bar from Strategy #1 — premium is rich relative to recent movement." },
  { term: "IVR", def: "IV rank: where today's IV sits in its own 252-day range (0 = yearly low, 100 = yearly high). 69 = closer to the year's high than its low. ETFs get it from the vol index instantly; single names build it from our nightly snapshots (shown as n=Xd until 60 days exist). Note: the research REJECTED IVR as an entry signal for spreads — it's context, not a trigger." },
  { term: "VRP-z", def: "z-score of (IV − RV20) vs the last 252 days: how unusual today's premium-over-realized is, in standard deviations. +0.5 was the old train-period 'rich' bar — shown only to build a forward record; it failed validation as an entry signal." },
  { term: "Term", def: "front-month ATM IV ÷ back-month ATM IV. Under 1 is normal (near vol cheap, far vol dear). Over ~1.03 (highlighted) = backwardation: the market is paying UP for the near expiry, usually an event (earnings) priced in — the IV-crush setup." },
  { term: "Strdl%", def: "the front-expiry ATM straddle price as % of spot = the market's expected move by that expiry. NVDA at 4.5% means the market pays 4.5% of the stock for the move. Crush trades win when the actual move is smaller than this number." },
  { term: "±$ (expected move)", def: "the same idea in dollars at the screener's expiry: spot × IV × √(DTE/365) — one standard deviation." },
];

export function MetricLegend() {
  const [open, setOpen] = useState(false);
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <button
        className="row"
        style={{ background: "none", border: 0, color: "inherit", font: "inherit", width: "100%", cursor: "pointer", gap: 8, minHeight: 44 }}
        onClick={() => setOpen(!open)}
      >
        <h3 className="section-title" style={{ margin: 0 }}>What these numbers mean</h3>
        <span className="muted small" style={{ marginLeft: "auto" }}>{open ? "▾" : "▸"}</span>
      </button>
      {open ? (
        <dl className="legend-list">
          {LEGEND.map((l) => (
            <div key={l.term} className="legend-row">
              <dt className="num">{l.term}</dt>
              <dd>{l.def}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

/* The TT defense ladder, annotated with what OUR experiments measured. */
export function DefensePlaybook() {
  const [open, setOpen] = useState(false);
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <button
        className="row"
        style={{ background: "none", border: 0, color: "inherit", font: "inherit", width: "100%", cursor: "pointer", gap: 8, minHeight: 44 }}
        onClick={() => setOpen(!open)}
      >
        <h3 className="section-title" style={{ margin: 0 }}>Defense playbook — TT ladder vs our evidence</h3>
        <span className="muted small" style={{ marginLeft: "auto" }}>{open ? "▾" : "▸"}</span>
      </button>
      {open ? (
        <div className="small" style={{ display: "grid", gap: 8, marginTop: 8 }}>
          <p style={{ margin: 0 }}>
            <b>TT step 1 — tested side? roll the untested side in</b> (buy back the collapsed wing,
            re-sell at 16-30Δ; improves breakeven).{" "}
            <span style={{ color: "var(--warn)" }}>
              We backtested exactly this (plus 3 other defense schemes) with post-bugfix engines:
              ALL four raise win rate (81→90%) and ALL worsen the tail and the mean — the
              untested-side roll sells a closer call into a falling, mean-reverting index and the
              bounce runs it over (whipsaw). Post-fix: dynamic management −$49/trade overall,
              +$17 in normal regimes, <b>−$258/trade in the four crash episodes</b>. Better
              breakeven, worse distribution.
            </span>
          </p>
          <p style={{ margin: 0 }}>
            <b>TT step 2 — keep rolling until it's a straddle.</b>{" "}
            <span style={{ color: "var(--warn)" }}>
              Same machinery, more of it: max premium, max gamma, exactly as the move proves
              persistent. The full ladder is untested by us, but every partial rung we measured
              pointed the same way.
            </span>
          </p>
          <p style={{ margin: 0 }}>
            <b>TT step 3 — inverted strangle / hold / punt to later expiry.</b>{" "}
            <span style={{ color: "var(--warn)" }}>
              Inverting locks a max loss wider than the collected credit unless the chain covered
              it. Punting trades premium for time when IV is highest. The structural finding: the
              tail is a GAP — a multi-day move faster than any daily rule; adjustment can only add
              exposure to it. What actually helped: exiting (50% PT / 21 DTE), a standing long put
              wing already on when the gap hits, and size.
            </span>
          </p>
          <p style={{ margin: 0 }}>
            <b>What our research DID support:</b> asymmetry (the put side pays, the call is the
            tail — symmetric 16Δ/16Δ was the worst cell of the grid); the tail is ~4 correlated
            macro EPISODES, not 460 per-trade problems (worst 4 ≈ 78% of all losses — and the
            sample contains no 2008/2020-class crash); a ~0.15Δ long put wing neutralizes the
            crash bucket while rolling amplifies it; and naked isn't survivable (−$45k structural
            on QQQ) while a wing is ~13× more capital-efficient per risk dollar. The ENTRY is the
            unresolved part: nothing — VRP-z, rv20-calm, IVR — survives deflation.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/* Collapsible screener: the live sweep is heavy (yfinance chains across the
   ETF universe), so it fetches on first expand, never on page mount. */
export function ScreenerSection() {
  const { accountEpoch } = useAuth();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<StrangleRow[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setRows(null);
    try {
      const r = await api<{ rows: StrangleRow[] }>("/journal/strangle/screener", { timeoutMs: 120_000 });
      setRows(r.rows || []);
      setError("");
    } catch (e) {
      setError((e as Error).message);
      setRows([]);
    }
  }, []);

  useEffect(() => {
    setRows(null); // dropped on account switch; refetched on next expand
    setOpen(false);
  }, [accountEpoch]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && rows === null) load();
  };

  return (
    <div style={{ marginBottom: 12 }}>
      <div className="card" style={{ marginBottom: open ? 12 : 0 }}>
        <button
          className="row"
          style={{ background: "none", border: 0, color: "inherit", font: "inherit", width: "100%", cursor: "pointer", gap: 8, minHeight: 44 }}
          onClick={toggle}
        >
          <h3 className="section-title" style={{ margin: 0 }}>Strangle screener — ETF conditions + live math</h3>
          <span className="muted small" style={{ marginLeft: "auto" }}>
            {open ? "▾" : "on-demand · ▸"}
          </span>
        </button>
      </div>
      {open ? (
        <>
          <div className="card debt-queue" style={{ marginBottom: 12 }}>
            <p className="small" style={{ margin: 0 }}>
              <b>No validated entry edge — read this before trading.</b> The VRP-z entry's headline
              "~2× mean" was mostly an <b>engine-bug artifact</b>: after the 2026-07-20 fixes its
              deflated Sharpe collapsed (DSR 0.36 at 5 trials, ~0.05-0.07 at the honest trial
              count), <b>no entry signal survives deflation</b>, and the pre-registered system
              failed its holdout (train +$104/trade → 2026 −$77; the 2026 period is burned).
              What's below is <i>conditions + live math</i>, not a signal. "Rich" (VRP-z ≥ +0.5)
              marks the old framing purely so it can accumulate a <b>forward</b> record.
            </p>
          </div>
          <DefensePlaybook />
          <div className="row" style={{ justifyContent: "flex-end", marginBottom: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={load}>Refresh</button>
          </div>
          {rows === null ? (
            <Skeleton h={70} n={4} />
          ) : error ? (
            <Empty>{error}</Empty>
          ) : (
            rows.map((r) => <RowCard key={r.symbol} r={r} />)
          )}
          <p className="chart-note">
            IV = each ETF's CBOE vol index (VIX/VXN/VXD/RVX) — a 30d proxy, not the chain's ATM IV;
            RV20 = close-close annualized. Chains via yfinance, 10-min cache. Decision support for the
            discretionary sleeve; Strategy #1's live path touches none of this.
          </p>
        </>
      ) : null}
    </div>
  );
}
