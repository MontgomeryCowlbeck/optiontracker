import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { LogbookDay, Trade } from "../api/types";
import { TradeEditor } from "../components/TradeEditor";
import { AutoTextarea, Empty, Pnl, Skeleton } from "../components/ui";
import { COARSE_POINTER } from "../lib/device";
import { conviction, fmtDay, num, pct, strategyLabel, tagBadgeStyle } from "../lib/format";
import { Markdown } from "../lib/markdown";
import { navigate } from "../lib/router";
import { useAuth, useFilters, useToast } from "../state/store";

/* Markdown note box with edit toggle + autosave-on-blur. */
function NoteBox({
  label,
  value,
  placeholder,
  onSave,
  action,
}: {
  label: string;
  value: string | null | undefined;
  placeholder: string;
  onSave: (text: string) => Promise<void>;
  action?: React.ReactNode;
}) {
  const [editing, setEditing] = useState(!value);
  const [text, setText] = useState(value || "");
  const toast = useToast();
  const saved = useRef(value || "");

  useEffect(() => {
    // Our own save echoes back as a prop change (parent mutates + re-renders);
    // resetting `editing` then would collapse the textarea mid-session the
    // moment the mobile keyboard's "Done" blurs it. Only sync on EXTERNAL
    // changes (date switch, server refresh).
    if ((value || "") === saved.current) return;
    setText(value || "");
    saved.current = value || "";
    setEditing(!value);
  }, [value]);

  const saveIfDirty = async () => {
    if (text === saved.current) return;
    try {
      await onSave(text);
      saved.current = text;
      toast(`${label} saved`);
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div className="notebox">
      <div className="notebox-head">
        <span className="lbl">{label}</span>
        {action}
        <button
          className="btn btn-ghost btn-sm"
          onClick={async () => {
            if (editing) await saveIfDirty();
            setEditing(!editing);
          }}
        >
          {editing ? "Done" : "Edit"}
        </button>
      </div>
      {editing ? (
        <AutoTextarea
          minRows={label === "Note to self" ? 3 : 8}
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={saveIfDirty}
        />
      ) : (
        <Markdown text={text} />
      )}
    </div>
  );
}

/* Conversation under the day's AI review — persisted server-side, one thread per day.
   Regenerating the review clears it (the old conversation loses its subject). */
function ReviewThread({ date, aiAvailable }: { date: string; aiAvailable: boolean }) {
  const toast = useToast();
  const [messages, setMessages] = useState<{ role: string; content: string }[] | null>(null);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);

  useEffect(() => {
    setMessages(null);
    api<{ messages: { role: string; content: string }[] }>(`/journal/ai/day-review/${date}/thread`)
      .then((r) => setMessages(r.messages || []))
      .catch(() => setMessages([]));
  }, [date]);

  if (messages === null) return null;
  if (!aiAvailable && !messages.length) return null;

  const send = async () => {
    const message = input.trim();
    if (!message || thinking) return;
    setMessages((cur) => [...(cur || []), { role: "user", content: message }]);
    setInput("");
    setThinking(true);
    try {
      const r = await api<{ reply: string }>(`/journal/ai/day-review/${date}/reply`, {
        method: "POST",
        body: { message },
        timeoutMs: 300_000,
      });
      setMessages((cur) => [...(cur || []), { role: "assistant", content: r.reply }]);
    } catch (e) {
      toast((e as Error).message, "err");
      setMessages((cur) => (cur || []).slice(0, -1)); // put the question back in the box
      setInput(message);
    } finally {
      setThinking(false);
    }
  };

  return (
    <div className="review-thread">
      {messages.map((m, i) =>
        m.role === "user" ? (
          <div key={i} className="msg user">
            {m.content}
          </div>
        ) : (
          <div key={i} className="msg assistant">
            <Markdown text={m.content} />
          </div>
        ),
      )}
      {thinking && (
        <div className="msg assistant thinking" aria-label="thinking">
          <i />
          <i />
          <i />
        </div>
      )}
      {aiAvailable ? (
        <div className="review-thread-input">
          <AutoTextarea
            minRows={1}
            placeholder="Respond to the review — push back, add context, ask for more…"
            value={input}
            enterKeyHint="enter"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !COARSE_POINTER) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button className="btn btn-ghost btn-sm" onClick={send} disabled={thinking || !input.trim()}>
            Reply
          </button>
        </div>
      ) : null}
    </div>
  );
}

function TradeCard({ t, open, onToggle, onChanged }: { t: Trade; open: boolean; onToggle: () => void; onChanged: () => void }) {
  return (
    <div className={"entry-wrap" + (open ? " open" : "")}>
      <button
        className={
          "entry " +
          (t.regime_read === "range" ? "r-range" : t.regime_read === "trend" ? "r-trend" : "")
        }
        onClick={onToggle}
      >
        <div className="tab" />
        <div className="body">
          <div className="meta">
            <span className="sym">{t.underlying || "?"}</span>
            {t.direction ? <span className="badge">{strategyLabel(t.direction)}</span> : null}
            {t.is_0dte ? <span className="badge">0DTE</span> : null}
            {t.is_system != null &&
              (t.is_system ? <span className="badge sys">system</span> : <span className="badge disc">discretionary</span>)}
            {t.edge_name ? <span className="badge">{t.edge_name}</span> : null}
            {(t.tags || []).map((tag) => (
              <span key={tag.id} className="badge" style={tagBadgeStyle(tag.color)}>
                {tag.name}
              </span>
            ))}
            {t.conviction ? <span className="conviction">{conviction(t.conviction)}</span> : null}
          </div>
          {t.why_entered ? <div className="why">{t.why_entered}</div> : null}
          {(t.insights || []).length ? (
            <div className="meta" style={{ marginTop: 4 }}>
              {(t.insights || []).map((b) => (
                <span key={b.key} className={"badge insight " + b.tone} title="rule-based insight (computed server-side)">
                  {b.label}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <div className="side">
          {t.status === "closed" ? (
            <Pnl v={t.realized_pnl} risk={t.planned_risk} />
          ) : (
            <span className="pnl flat">open</span>
          )}
          <span className="muted small num">{t.strikes || ""}</span>
        </div>
      </button>
      {open && (
        <div className="trade-editor">
          <TradeEditor tradeId={t.id} onClose={onToggle} onChanged={onChanged} />
        </div>
      )}
    </div>
  );
}

export function Logbook({ dateParam }: { dateParam?: string }) {
  const toast = useToast();
  const { accountEpoch } = useAuth();
  const { qs } = useFilters();
  const [days, setDays] = useState<LogbookDay[] | null>(null);
  const [page, setPage] = useState(0);
  const [openTrade, setOpenTrade] = useState<number | null>(null);
  const [aiAvailable, setAiAvailable] = useState(false);
  const [generating, setGenerating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [lb, ai] = await Promise.all([
        api<{ days: LogbookDay[] }>("/journal/logbook" + qs),
        api<{ available: boolean }>("/journal/ai/status").catch(() => ({ available: false })),
      ]);
      setDays(lb.days || []);
      setAiAvailable(!!ai.available);
    } catch (e) {
      toast((e as Error).message, "err");
      setDays([]);
    }
  }, [toast, qs]);

  useEffect(() => {
    setDays(null);
    load();
  }, [load, accountEpoch]);

  // jump to a specific date (from the calendar)
  useEffect(() => {
    if (!days || !dateParam) return;
    const i = days.findIndex((d) => d.date === dateParam);
    if (i >= 0) setPage(i);
    else {
      // nearest older day
      const j = days.findIndex((d) => d.date < dateParam);
      setPage(j >= 0 ? j : days.length - 1);
    }
  }, [days, dateParam]);

  if (days === null) return <Skeleton h={120} n={3} />;
  if (!days.length) {
    return (
      <Empty hint="Sync from the top bar — trades are detected automatically, and each trading day becomes a page here.">
        No trades journaled yet.
      </Empty>
    );
  }

  const i = Math.max(0, Math.min(page, days.length - 1));
  const day = days[i];
  const s = day.stats;

  const turn = (delta: number) => {
    const next = Math.max(0, Math.min(i + delta, days.length - 1));
    setPage(next);
    setOpenTrade(null);
    navigate("logbook", days[next].date);
  };

  const generateReview = async () => {
    setGenerating(true);
    try {
      await api(`/journal/ai/day-review/${day.date}`, { method: "POST", timeoutMs: 300_000 });
      toast("Review generated");
      await load();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>Logbook</h1>
        <div className="row">
          <button className="btn btn-ghost btn-sm" disabled={i === 0} onClick={() => turn(-1)}>
            ← Newer
          </button>
          <span className="muted small num">
            {i + 1} / {days.length}
          </span>
          <button className="btn btn-ghost btn-sm" disabled={i >= days.length - 1} onClick={() => turn(1)}>
            Older →
          </button>
        </div>
      </div>

      <div className="stack">
        <div className="lb-date">{fmtDay(day.date)}</div>

        <NoteBox
          label="Note to self"
          value={day.note}
          placeholder="A message to yourself for this day — markdown, saved on blur."
          onSave={async (text) => {
            await api(`/journal/day-notes/${day.date}`, { method: "PUT", body: { note: text } });
            day.note = text;
          }}
        />

        <div className="stack">
          {day.trades.map((t) => (
            <TradeCard
              key={t.id}
              t={t}
              open={openTrade === t.id}
              onToggle={() => setOpenTrade(openTrade === t.id ? null : t.id)}
              onChanged={() => load()}
            />
          ))}
        </div>

        <div className="lb-footer">
          <div className="num muted">
            {s.count} closed · <Pnl v={s.total_pnl} /> · win {pct(s.win_rate)} · payoff{" "}
            {s.payoff_ratio == null ? "—" : num(s.payoff_ratio)}
          </div>
          <div className="lb-msg">{day.message}</div>
        </div>

        <NoteBox
          label="AI review"
          value={day.ai_feedback}
          placeholder="Generate a review (button above) or paste one here — it renders as formatted text and becomes part of this page."
          onSave={async (text) => {
            await api(`/journal/day-notes/${day.date}`, { method: "PUT", body: { ai_feedback: text } });
            day.ai_feedback = text;
          }}
          action={
            aiAvailable ? (
              <button className="btn btn-ghost btn-sm" onClick={generateReview} disabled={generating}>
                {generating ? "Thinking…" : "Generate review"}
              </button>
            ) : (
              <span className="muted small">AI offline</span>
            )
          }
        />
        {day.ai_feedback ? <ReviewThread key={day.date} date={day.date} aiAvailable={aiAvailable} /> : null}
      </div>
    </div>
  );
}
