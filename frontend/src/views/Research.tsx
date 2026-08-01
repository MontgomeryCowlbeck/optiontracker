/* Research — deep-dive home for names you're following.

   Two modes on one route: #/research is a GRID of stock cards (signal-card
   theme — glanceable price/flags/AI snippet); #/research/SYMBOL is a whole
   page dedicated to that name: nightly AI daily card, inline deep-dive
   chart/levels, thesis + stance, running notes, and the full AI trail. */
import { useCallback, useEffect, useState } from "react";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { api } from "../api/client";
import type { ResearchBrief, ResearchCard, ResearchNoteRow } from "../api/types";
import { ConsultChat } from "../components/ConsultChat";
import { DDPanel } from "../components/DDPanel";
import { Empty, Modal, Pnl, Skeleton } from "../components/ui";
import { fmtDayShort, money, num, timeShort } from "../lib/format";
import { Markdown } from "../lib/markdown";
import { navigate } from "../lib/router";
import { useAuth, useToast } from "../state/store";

const FLAG_LABELS: Record<string, { label: string; cls: string; title: string }> = {
  near_support: { label: "near support", cls: "good", title: "price within 3% of a detected support zone" },
  earnings_soon: { label: "earnings soon", cls: "warn", title: "earnings within 7 days" },
  rsi_washout: { label: "RSI washout", cls: "good", title: "RSI14 ≤ 35" },
  iv_rich: { label: "IV rich", cls: "good", title: "IV/RV ≥ 1.3 — premium is expensive" },
};

function Flags({ flags }: { flags: string[] | undefined }) {
  return (
    <>
      {(flags || []).map((f) => {
        const fl = FLAG_LABELS[f];
        return fl ? (
          <span key={f} className={"badge " + fl.cls} title={fl.title}>
            {fl.label}
          </span>
        ) : null;
      })}
    </>
  );
}

function Sparkline({ card, w = 110, h = 34 }: { card: ResearchCard; w?: number; h?: number }) {
  const pts = card.history.filter((p) => p.price != null);
  if (pts.length < 2) return null;
  const up = (pts[pts.length - 1].price ?? 0) >= (pts[0].price ?? 0);
  return (
    <div style={{ width: w, height: h }}>
      <ResponsiveContainer>
        <LineChart data={pts} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
          <YAxis hide domain={["auto", "auto"]} />
          <Line type="monotone" dataKey="price" stroke={up ? "var(--win)" : "var(--loss)"} strokeWidth={1.5} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ---------------- consult modal ---------------- */

const CONSULT_STARTERS = (s: string) => [
  {
    title: "Evaluate a new entry",
    desc: "interrogates your intent, then a verdict",
    msg: `I'm thinking about selling a put on ${s}. Interrogate my intent, then give me a verdict.`,
  },
  {
    title: "Review my open position",
    desc: "how it's doing, and how to manage it",
    msg: `Review my open position on ${s} — how is it doing, and how should I manage it from here?`,
  },
  {
    title: "Current read",
    desc: "conversational — no full writeup",
    msg: `Give me your current read on ${s}. Keep it conversational — no full verdict writeup.`,
  },
  {
    title: "Challenge my thesis",
    desc: "argues the other side against the data",
    msg: `Challenge my thesis on ${s} — argue the other side against the data and my notes.`,
  },
];

function ConsultModal({ symbol }: { symbol: string }) {
  return (
    <ConsultChat
      intro="The agent reads the DD, the live put chain, your thesis and journal history. It will ask about
        your intent first (assignment tolerance, goal, size) — then give a verdict. Every number it
        cites is code-computed; the judgment is its own."
      starters={CONSULT_STARTERS(symbol)}
      send={async (message) => {
        const r = await api<{ reply: string }>(`/journal/research/${symbol}/consult`, {
          method: "POST",
          body: { message },
        });
        return r.reply;
      }}
      load={async () => {
        const r = await api<{ messages: { role: string; content: string }[] }>(
          `/journal/research/${symbol}/consult`,
        );
        return r.messages;
      }}
      onClear={async () => {
        await api(`/journal/research/${symbol}/consult`, { method: "DELETE" });
      }}
    />
  );
}

/* ---------------- grid card ---------------- */

function StockCard({ card }: { card: ResearchCard }) {
  const s = card.latest;
  const daily = card.last_daily?.content?.replace(/[#*_>`]/g, "").slice(0, 140);
  return (
    <button className="rsch-card" onClick={() => navigate("research", card.symbol)}>
      <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
        <span className="sym" style={{ fontSize: 17 }}>{card.symbol}</span>
        {s?.price != null ? (
          <span className="num" style={{ color: "var(--ink)", fontWeight: 600 }}>
            {money(s.price, { sign: false })}
          </span>
        ) : null}
        {s?.chg_1d_pct != null ? <Pnl v={s.chg_1d_pct} pctVal={undefined} className="small" /> : null}
        <span style={{ marginLeft: "auto" }}>
          <Sparkline card={card} w={90} h={28} />
        </span>
      </div>
      <div className="chips" style={{ gap: 4 }}>
        <Flags flags={s?.flags} />
        {card.open_positions > 0 ? <span className="badge sys">{card.open_positions} open</span> : null}
        {s?.earnings ? <span className="badge">earn {fmtDayShort(String(s.earnings).slice(0, 10))}</span> : null}
      </div>
      <div className="muted small num rsch-stats">
        <span>RSI {s ? num(s.rsi14, 0) : "—"}</span>
        <span>IV/RV {s?.iv_rv != null ? num(s.iv_rv) : "—"}</span>
        <span>
          sup {s?.support_dist_pct != null ? `${num(s.support_dist_pct, 1)}%` : "—"}
        </span>
      </div>
      {daily ? (
        <p className="muted small rsch-snippet">{daily}…</p>
      ) : card.thesis ? (
        <p className="muted small rsch-snippet" style={{ fontStyle: "italic" }}>{card.thesis}</p>
      ) : (
        <p className="muted small rsch-snippet">no AI daily card yet — writes nightly at 5:30 ET</p>
      )}
    </button>
  );
}

/* ---------------- per-stock page ---------------- */

function StockPage({ symbol, onGone }: { symbol: string; onGone: () => void }) {
  const toast = useToast();
  const { accountEpoch } = useAuth();
  const [card, setCard] = useState<ResearchCard | null>(null);
  const [notes, setNotes] = useState<ResearchNoteRow[] | null>(null);
  const [briefs, setBriefs] = useState<ResearchBrief[] | null>(null);
  const [missing, setMissing] = useState(false);
  const [note, setNote] = useState("");
  const [thesis, setThesis] = useState("");
  const [consult, setConsult] = useState(false);
  const [openBrief, setOpenBrief] = useState<number | null>(null);
  const [busy, setBusy] = useState<"" | "brief" | "daily">("");
  /* Two-tap remove — declared up here with the other hooks: hooks below the
     early returns crash with React #310 once data arrives (more hooks than
     the skeleton render had). */
  const [removeArmed, setRemoveArmed] = useState(false);

  const load = useCallback(async () => {
    const r = await api<{ watchlist: ResearchCard[] }>("/journal/research");
    const c = r.watchlist.find((x) => x.symbol === symbol.toUpperCase()) || null;
    setCard(c);
    setMissing(!c);
    if (!c) return;
    setThesis(c.thesis || "");
    const [n, b] = await Promise.all([
      api<{ notes: ResearchNoteRow[] }>(`/journal/research/${symbol}/notes`),
      api<{ briefs: ResearchBrief[] }>(`/journal/research/${symbol}/briefs`),
    ]);
    setNotes(n.notes);
    setBriefs(b.briefs);
  }, [symbol]);

  useEffect(() => {
    setCard(null);
    load().catch((e) => toast((e as Error).message, "err"));
  }, [load, toast, accountEpoch]);

  if (missing)
    return (
      <Empty hint="It may have been removed — add it again from the Research grid.">
        {symbol} is not on your watchlist.
      </Empty>
    );
  if (!card) return <Skeleton h={120} n={3} />;

  const s = card.latest;
  const daily = briefs?.find((b) => b.kind === "daily") || null;

  const run = async (kind: "brief" | "daily") => {
    setBusy(kind);
    try {
      await api(`/journal/research/${symbol}/${kind === "brief" ? "brief" : "daily-update"}`,
        { method: "POST", timeoutMs: 300_000 });
      toast(kind === "brief" ? "Research brief saved" : "Daily card regenerated");
      load();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy("");
    }
  };

  const saveThesis = async () => {
    await api(`/journal/research/watchlist/${symbol}`, { method: "PUT", body: { thesis } });
    toast("Thesis saved");
  };

  const setStance = async (v: boolean) => {
    await api(`/journal/research/watchlist/${symbol}`, { method: "PUT", body: { assignment_ok: v } });
    load();
  };

  const addNote = async () => {
    if (!note.trim()) return;
    await api(`/journal/research/${symbol}/notes`, { method: "POST", body: { note } });
    setNote("");
    load();
  };

  /* Two-tap — destructive, and one stray tap away at the page bottom. */
  const remove = async () => {
    if (!removeArmed) {
      setRemoveArmed(true);
      window.setTimeout(() => setRemoveArmed(false), 5000);
      return;
    }
    await api(`/journal/research/watchlist/${symbol}`, { method: "DELETE" });
    onGone();
  };

  return (
    <div>
      <div className="page-header">
        <div className="row" style={{ gap: 10, alignItems: "baseline" }}>
          <button className="btn btn-ghost btn-sm" onClick={() => navigate("research")}>
            ← Research
          </button>
          <h1 style={{ margin: 0 }}>{card.symbol}</h1>
          {s?.price != null ? (
            <span className="num" style={{ fontSize: 18, fontWeight: 600 }}>
              {money(s.price, { sign: false })}
            </span>
          ) : null}
          {s?.chg_1d_pct != null ? <Pnl v={s.chg_1d_pct} className="small" /> : null}
        </div>
        <div className="row">
          <button className="btn btn-primary btn-sm" onClick={() => setConsult(true)}>
            Consult the desk
          </button>
        </div>
      </div>

      <div className="chips" style={{ marginBottom: 10 }}>
        <Flags flags={s?.flags} />
        {card.open_positions > 0 ? <span className="badge sys">{card.open_positions} open position(s)</span> : null}
        {card.journal_trades > 0 ? <span className="badge">{card.journal_trades} journaled trades</span> : null}
        {s?.earnings ? <span className="badge warn">earnings {fmtDayShort(String(s.earnings).slice(0, 10))}</span> : null}
        {s ? (
          <span className="muted small num">
            RSI {num(s.rsi14, 0)} · IV/RV {s.iv_rv != null ? num(s.iv_rv) : "—"} · support{" "}
            {s.support_lo != null
              ? `${num(s.support_lo)}–${num(s.support_hi)} (${num(s.support_dist_pct, 1)}%)`
              : "none"}
          </span>
        ) : null}
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
          <h3 className="section-title" style={{ margin: 0 }}>
            Daily card{" "}
            {daily ? <span className="muted small" style={{ fontWeight: 400 }}>— {timeShort(daily.created_at + "Z")}</span> : null}
          </h3>
          <button className="btn btn-ghost btn-sm" onClick={() => run("daily")} disabled={busy !== ""}>
            {busy === "daily" ? "writing…" : daily ? "Regenerate" : "Write now"}
          </button>
        </div>
        {daily ? (
          <Markdown text={daily.content} />
        ) : (
          <p className="muted small" style={{ margin: 0 }}>
            The overnight analyst writes one card per name nightly (5:30 PM ET): price action, volume,
            regime, levels, vol, and what to watch — every number code-computed.
          </p>
        )}
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <DDPanel symbol={card.symbol} />
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <label className="field">
          <span>Thesis — why you're following this name</span>
          <div className="row" style={{ gap: 8 }}>
            <input style={{ flex: 1 }} value={thesis} onChange={(e) => setThesis(e.target.value)} />
            <button className="btn btn-ghost" onClick={saveThesis}>
              Save
            </button>
          </div>
        </label>
        <div className="row" style={{ gap: 8, alignItems: "center", marginTop: 8 }}>
          <span className="muted small">If a short put gets tested:</span>
          <div className="seg" role="group">
            <button className={card.assignment_ok === 1 ? "on" : ""} onClick={() => setStance(true)}>
              happy to own it
            </button>
            <button className={card.assignment_ok === 0 ? "on" : ""} onClick={() => setStance(false)}>
              avoid assignment
            </button>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <h3 className="section-title" style={{ marginTop: 0 }}>Running notes</h3>
        <div className="row" style={{ gap: 8 }}>
          <input
            style={{ flex: 1, minWidth: 0 }}
            value={note}
            placeholder="add to the research trail…"
            enterKeyHint="send"
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addNote()}
          />
          <button className="btn btn-ghost" onClick={addNote} disabled={!note.trim()}>
            Add
          </button>
        </div>
        {notes === null ? (
          <Skeleton h={30} n={1} />
        ) : notes.length === 0 ? (
          <p className="muted small">Nothing yet — the trail starts with your first note.</p>
        ) : (
          notes.map((n) => (
            <p key={n.id} className="small" style={{ margin: "6px 0" }}>
              <span className="muted num">{timeShort(n.created_at + "Z")}</span> — {n.note}
            </p>
          ))
        )}
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 className="section-title" style={{ margin: 0 }}>AI trail</h3>
          <button className="btn btn-ghost btn-sm" onClick={() => run("brief")} disabled={busy !== ""}>
            {busy === "brief" ? "researching…" : "Run research brief"}
          </button>
        </div>
        {briefs === null ? (
          <Skeleton h={30} n={1} />
        ) : briefs.length === 0 ? (
          <p className="muted small">No saved briefs, daily cards, or consults yet.</p>
        ) : (
          briefs.map((b) => (
            <div key={b.id} className="card" style={{ padding: 10, marginBottom: 6 }}>
              <button
                className="row"
                style={{ background: "none", border: 0, color: "inherit", font: "inherit", cursor: "pointer", width: "100%", gap: 8, minHeight: 44 }}
                onClick={() => setOpenBrief(openBrief === b.id ? null : b.id)}
              >
                <span className={"badge " + (b.kind === "consult" ? "good" : b.kind === "daily" ? "" : "warn")}>
                  {b.kind}
                </span>
                <span className="muted small num">{timeShort(b.created_at + "Z")}</span>
                <span className="muted small" style={{ marginLeft: "auto" }}>
                  {openBrief === b.id ? "▾" : "▸"}
                </span>
              </button>
              {openBrief === b.id ? <Markdown text={b.content} /> : null}
            </div>
          ))
        )}
      </div>

      <p className="muted small">
        <button className={"btn btn-sm " + (removeArmed ? "btn-danger" : "btn-ghost")}
                style={removeArmed ? undefined : { color: "var(--loss)" }} onClick={remove}>
          {removeArmed ? "Tap again to stop following" : `Stop following ${card.symbol}`}
        </button>
      </p>

      {consult ? (
        <Modal
          wide
          title={<span>Consult the desk <span className="muted" style={{ fontWeight: 400 }}>— {card.symbol}</span></span>}
          onClose={() => setConsult(false)}
        >
          <ConsultModal symbol={card.symbol} />
        </Modal>
      ) : null}
    </div>
  );
}

/* ---------------- view ---------------- */

export function Research({ symbolParam }: { symbolParam?: string }) {
  const { accountEpoch } = useAuth();
  const [cards, setCards] = useState<ResearchCard[] | null>(null);
  const [error, setError] = useState("");
  const [symbol, setSymbol] = useState("");
  const [thesis, setThesis] = useState("");
  const [adding, setAdding] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api<{ watchlist: ResearchCard[] }>("/journal/research");
      setCards(r.watchlist);
      setError("");
    } catch (e) {
      setError((e as Error).message);
      setCards([]);
    }
  }, []);

  useEffect(() => {
    if (symbolParam) return; // the stock page loads its own data
    setCards(null);
    load();
  }, [load, accountEpoch, symbolParam]);

  if (symbolParam) {
    return <StockPage symbol={symbolParam.toUpperCase()} onGone={() => navigate("research")} />;
  }

  const add = async () => {
    if (!symbol.trim() || adding) return;
    setAdding(true);
    setError("");
    try {
      await api("/journal/research/watchlist", {
        method: "POST",
        body: { symbol: symbol.trim(), thesis: thesis.trim() || null },
      });
      const sym = symbol.trim().toUpperCase();
      setSymbol("");
      setThesis("");
      navigate("research", sym);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const refreshAll = async () => {
    setRefreshing(true);
    try {
      await api("/journal/research/refresh", { method: "POST", timeoutMs: 300_000 });
      load();
    } finally {
      setRefreshing(false);
    }
  };

  if (cards === null) return <Skeleton h={90} n={3} />;

  return (
    <div>
      <div className="page-header">
        <h1>Research</h1>
        <button className="btn btn-ghost btn-sm" onClick={refreshAll} disabled={refreshing}>
          {refreshing ? "refreshing…" : "Refresh all"}
        </button>
      </div>
      <p className="muted small" style={{ marginTop: -8 }}>
        Names you're following. Each card gets a nightly snapshot + an AI daily read (5:30 PM ET) —
        tap one for its dedicated page: chart, levels, notes, and the full AI trail.
      </p>

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row" style={{ gap: 8 }}>
          <input
            style={{ width: 110 }}
            value={symbol}
            placeholder="MSFT"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            autoComplete="off"
            enterKeyHint="next"
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <input
            style={{ flex: 1, minWidth: 0 }}
            value={thesis}
            placeholder="why you're following it (thesis)"
            enterKeyHint="done"
            onChange={(e) => setThesis(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <button className="btn btn-primary" onClick={add} disabled={adding || !symbol.trim()}>
            {adding ? "validating…" : "Follow"}
          </button>
        </div>
        {error ? <p className="small" style={{ color: "var(--loss)", margin: "6px 0 0" }}>{error}</p> : null}
      </div>

      {cards.length === 0 ? (
        <Empty hint="Add a name above — it gets a DD snapshot immediately and joins the nightly refresh.">
          Nothing under research yet.
        </Empty>
      ) : (
        <div className="rsch-grid">
          {cards.map((c) => (
            <StockCard key={c.id} card={c} />
          ))}
        </div>
      )}
    </div>
  );
}
