/* Deep-dive panel — the full breakdown behind a signal card. All numbers come
   from /journal/dd/{symbol} (code-computed, watchtower level detection); the
   optional research brief is the AI layer over those same numbers. */
import { useEffect, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { DD, DDZone, WatchtowerCard } from "../api/types";
import { fmtDayShort, money, num, pct } from "../lib/format";
import { Markdown } from "../lib/markdown";
import { Empty, Skeleton, StatTile } from "./ui";

const BLUE = "#3987e5";
const ORANGE = "#d95926";
const SUPPORT = "#3fa46a";
const RESIST = "#c94f4f";

function zoneLabel(z: DDZone): string {
  const bits = [`×${z.touches}`, `str ${num(z.strength, 1)}`];
  if (z.volume_node) bits.push("HVN");
  if (z.round_number != null) bits.push(`round ${z.round_number}`);
  return bits.join(" · ");
}

function ZoneRow({ z }: { z: DDZone }) {
  return (
    <div className="row small num" style={{ gap: 8, alignItems: "baseline" }}>
      <span
        className="badge"
        style={{ background: z.kind === "support" ? "rgba(63,164,106,.15)" : "rgba(201,79,79,.15)" }}
      >
        {z.kind}
      </span>
      <b style={{ color: "var(--ink)" }}>
        {num(z.lo)}–{num(z.hi)}
      </b>
      <span className="muted">
        {z.distance_pct != null ? `${z.distance_pct > 0 ? "+" : ""}${num(z.distance_pct, 1)}%` : ""} ·{" "}
        {zoneLabel(z)} · last {fmtDayShort(z.last_touch)}
      </span>
    </div>
  );
}

/* Chart timeframes in trading days. "2Y" shows everything, which covers the
   full zone-detection lookback; shorter views are for price action only. */
const TIMEFRAMES: [string, number][] = [
  ["3M", 63],
  ["6M", 126],
  ["1Y", 252],
  ["2Y", Infinity],
];

function DDChart({ dd, strike, days }: { dd: DD; strike?: number | null; days: number }) {
  const zones = [...dd.supports, ...dd.resistances];
  const series = Number.isFinite(days) ? dd.series.slice(-days) : dd.series;
  return (
    <div style={{ width: "100%", height: "clamp(200px, 60vw, 280px)" }}>
      <ResponsiveContainer>
        <ComposedChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#2c2c2a" strokeWidth={1} vertical={false} />
          <XAxis
            dataKey="d"
            tickFormatter={fmtDayShort}
            tick={{ fill: "#9a988e", fontSize: 11 }}
            axisLine={{ stroke: "#383835" }}
            tickLine={false}
            minTickGap={36}
          />
          <YAxis
            tick={{ fill: "#9a988e", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={38}
            domain={["auto", "auto"]}
            tickFormatter={(v: number) => v.toFixed(0)}
          />
          <Tooltip
            contentStyle={{
              background: "#232321",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              fontSize: 12,
              color: "#ffffff",
            }}
            formatter={(value: number | string, name: string) => [money(Number(value), { sign: false }), name]}
          />
          {zones.map((z, i) => (
            <ReferenceArea
              key={i}
              y1={z.lo}
              y2={z.hi}
              fill={z.kind === "support" ? SUPPORT : RESIST}
              fillOpacity={0.05 + Math.min(0.15, z.strength * 0.04)}
              stroke={z.kind === "support" ? SUPPORT : RESIST}
              strokeOpacity={0.35}
              strokeDasharray="4 3"
            />
          ))}
          {strike != null ? (
            <ReferenceLine
              y={strike}
              stroke={ORANGE}
              strokeWidth={1.5}
              strokeDasharray="6 3"
              label={{ value: `${strike}P`, fill: ORANGE, fontSize: 11, position: "insideRight" }}
            />
          ) : null}
          <Line type="monotone" dataKey="sma200" name="SMA200" stroke="#8a6bbf" strokeWidth={1} dot={false} connectNulls />
          <Line type="monotone" dataKey="sma50" name="SMA50" stroke="#b0893b" strokeWidth={1} dot={false} connectNulls />
          <Line type="monotone" dataKey="c" name={dd.symbol} stroke={BLUE} strokeWidth={2} dot={false} connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export function DDPanel({ symbol, card }: { symbol: string; card?: WatchtowerCard | null }) {
  const [dd, setDd] = useState<DD | null>(null);
  const [error, setError] = useState("");
  const [brief, setBrief] = useState<string | null>(null);
  const [briefBusy, setBriefBusy] = useState(false);
  const [briefError, setBriefError] = useState("");
  const [tfDays, setTfDays] = useState<number>(252);

  useEffect(() => {
    setDd(null);
    setError("");
    setBrief(null);
    setBriefError("");
    api<DD>(`/journal/dd/${symbol}`)
      .then(setDd)
      .catch((e) => setError((e as Error).message));
  }, [symbol]);

  const runBrief = async () => {
    setBriefBusy(true);
    setBriefError("");
    try {
      const r = await api<{ brief: string }>(`/journal/dd/${symbol}/brief`, { method: "POST" });
      setBrief(r.brief);
    } catch (e) {
      setBriefError((e as Error).message);
    } finally {
      setBriefBusy(false);
    }
  };

  const s = dd?.stats;
  const nearEarnings = s?.earnings && card?.put?.expiry ? s.earnings <= card.put.expiry : false;

  return (
    <div>
      {error ? <Empty>{error}</Empty> : null}
      {!dd && !error ? <Skeleton h={280} n={1} /> : null}

      {dd && s ? (
        <>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
            <span className="num" style={{ fontSize: 17, color: "var(--ink)", fontWeight: 650 }}>
              {money(dd.price, { sign: false })}
              <span className="muted small" style={{ fontWeight: 400 }}> as of {dd.as_of}</span>
            </span>
            <div className="seg" role="group" aria-label="Timeframe">
              {TIMEFRAMES.map(([label, d]) => (
                <button key={label} className={tfDays === d ? "on" : ""} onClick={() => setTfDays(d)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <DDChart dd={dd} strike={card?.put?.strike ?? null} days={tfDays} />
          <p className="chart-note" style={{ marginTop: 2 }}>
            Green bands = detected support zones (opacity ∝ strength), red = resistance
            {card?.put ? " · dashed orange = the alert's suggested strike" : ""}. Zones are computed
            from the last {dd.zone_lookback_days} trading days regardless of the view — select 2Y to
            see the full window they came from.
          </p>

          <div className="tiles" style={{ marginTop: 10 }}>
            <StatTile label="RSI 14" value={num(s.rsi14, 0)} sub={s.rsi14 != null && s.rsi14 <= 35 ? "washout zone" : undefined} />
            <StatTile
              label="52-week range"
              value={`${num(s.lo52, 0)}–${num(s.hi52, 0)}`}
              sub={`${num(s.pct_from_52w_high, 1)}% off high`}
            />
            <StatTile label="5d / 20d" value={`${num(s.ret_5d, 1)}% / ${num(s.ret_20d, 1)}%`} />
            <StatTile label="ATR 14" value={money(s.atr14, { sign: false })} sub={`${num(s.atr_pct, 1)}% of price`} />
            <StatTile
              label="Vol (RV20 / ATM IV)"
              value={`${s.rv20 != null ? pct(s.rv20 * 100, 0) : "—"} / ${s.atm_iv != null ? pct(s.atm_iv * 100, 0) : "—"}`}
              sub={
                s.iv_rv_ratio != null
                  ? `IV/RV ${num(s.iv_rv_ratio)}${s.iv_rv_ratio >= 1.3 ? " — rich" : ""}`
                  : undefined
              }
            />
            <StatTile
              label="Trend"
              value={s.above_sma200 == null ? "—" : s.above_sma200 ? "above 200sma" : "below 200sma"}
              sub={`50sma ${num(s.sma50, 0)} · 200sma ${num(s.sma200, 0)}`}
            />
            <StatTile
              label="Earnings"
              value={s.earnings ?? "none found"}
              valueClass={nearEarnings ? "loss" : undefined}
              sub={nearEarnings ? "BEFORE suggested expiry" : undefined}
            />
          </div>

          <div className="card" style={{ marginTop: 10 }}>
            <h3 style={{ marginTop: 0 }}>Levels</h3>
            {dd.supports.length + dd.resistances.length === 0 ? (
              <p className="muted small" style={{ margin: 0 }}>
                No unbroken zones detected near price — the freefall shape. Strikes have no defended level
                to lean on here.
              </p>
            ) : (
              <div className="stack" style={{ gap: 6 }}>
                {dd.supports.map((z, i) => (
                  <ZoneRow key={`s${i}`} z={z} />
                ))}
                {dd.resistances.map((z, i) => (
                  <ZoneRow key={`r${i}`} z={z} />
                ))}
              </div>
            )}
          </div>

          <div className="card" style={{ marginTop: 10 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3 style={{ margin: 0 }}>Research brief</h3>
              {!brief ? (
                <button className="btn btn-primary" onClick={runBrief} disabled={briefBusy}>
                  {briefBusy ? "Researching…" : "Run AI brief"}
                </button>
              ) : null}
            </div>
            {briefBusy ? (
              <p className="hint-line" style={{ margin: "6px 0 0" }}>
                web search + chart read — can take a few minutes
              </p>
            ) : null}
            {briefError ? <p className="muted small">{briefError}</p> : null}
            {brief ? (
              <Markdown text={brief} />
            ) : !briefBusy ? (
              <p className="muted small" style={{ marginBottom: 0 }}>
                Chart read, catalysts (web search), vol context, bull/bear case, and the watchtower lens —
                grounded in the numbers above. Decision support, never a buy/sell call.
              </p>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
