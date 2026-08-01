/* Shared consult conversation — one chat surface for the desk agent, used by
   the Research tab (per-name) and the Positions tab (per-position). The caller
   supplies the intro copy, the starter prompts, and the transport. The thread
   is persisted server-side: `load` fetches it on open, each turn is stored by
   the backend, and `onClear` starts a fresh conversation. */
import { useEffect, useRef, useState } from "react";
import { COARSE_POINTER } from "../lib/device";
import { Markdown } from "../lib/markdown";
import { AutoTextarea } from "./ui";

export interface ConsultStarter {
  title: string;
  desc: string;
  msg: string;
}

export function ConsultChat({
  intro,
  starters,
  send: transport,
  load,
  onClear,
}: {
  intro: string;
  starters: ConsultStarter[];
  send: (message: string) => Promise<string>;
  load?: () => Promise<{ role: string; content: string }[]>;
  onClear?: () => Promise<void>;
}) {
  const [history, setHistory] = useState<{ role: string; content: string }[]>([]);
  const [loading, setLoading] = useState(!!load);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [clearArmed, setClearArmed] = useState(false); // native confirm() no-ops in standalone mobile — two-tap
  const endRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!load) return;
    let alive = true;
    load()
      .then((msgs) => alive && setHistory(msgs))
      .catch(() => {}) // no stored thread is fine — start empty
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, busy]);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    const next = [...history, { role: "user", content: text }];
    setHistory(next);
    setInput("");
    setBusy(true);
    setError("");
    try {
      const reply = await transport(text);
      setHistory([...next, { role: "assistant", content: reply }]);
    } catch (e) {
      setError((e as Error).message);
      setHistory(history); // roll back the unanswered turn
      setInput(text);
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!onClear || busy) return;
    if (!clearArmed) {
      setClearArmed(true);
      window.setTimeout(() => setClearArmed(false), 5000);
      return;
    }
    setClearArmed(false);
    try {
      await onClear();
      setHistory([]);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div>
      <div className="row" style={{ marginTop: 0, marginBottom: 8, gap: 10 }}>
        <p className="muted small" style={{ margin: 0, flex: 1, minWidth: 200 }}>
          {intro}
        </p>
        {onClear && history.length > 0 && !busy ? (
          <button className={"btn btn-sm " + (clearArmed ? "btn-danger" : "btn-ghost")} onClick={clear}>
            {clearArmed ? "Tap again to clear" : "Clear chat"}
          </button>
        ) : null}
      </div>

      {/* dvh, not vh — and contain the scroll so it doesn't chain into the sheet */}
      <div className="stack" style={{ maxHeight: "42dvh", overflowY: "auto", overscrollBehavior: "contain", gap: 8 }}>
        {loading ? <p className="muted small">loading conversation…</p> : null}
        {!loading && history.length === 0 && !busy ? (
          <div className="consult-starters">
            {starters.map((st) => (
              <button key={st.title} className="starter" onClick={() => send(st.msg)}>
                <b>{st.title}</b>
                <span>{st.desc}</span>
              </button>
            ))}
          </div>
        ) : null}
        {history.map((h, i) => (
          <div key={i} className="card" style={{ padding: 10 }}>
            <div className="muted small" style={{ marginBottom: 4 }}>
              {h.role === "user" ? "You" : "Desk"}
            </div>
            {h.role === "user" ? <div>{h.content}</div> : <Markdown text={h.content} />}
          </div>
        ))}
        {busy ? (
          <p className="thinking muted small" aria-label="thinking" style={{ margin: 0 }}>
            <i />
            <i />
            <i />
            <span style={{ marginLeft: 6 }}>thinking — web search + chain read; can take a few minutes</span>
          </p>
        ) : null}
        {error ? <p className="small" style={{ color: "var(--loss)" }}>{error}</p> : null}
        <div ref={endRef} />
      </div>

      <div className="composer" ref={composerRef}>
        <AutoTextarea
          minRows={1}
          value={input}
          placeholder={history.length ? "answer / follow up…" : "or ask in your own words…"}
          enterKeyHint="send"
          onChange={(e) => setInput(e.target.value)}
          onFocus={() =>
            window.setTimeout(
              () => composerRef.current?.scrollIntoView({ block: "nearest" }),
              300,
            )
          }
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !COARSE_POINTER) {
              e.preventDefault();
              send(input);
            }
          }}
          disabled={busy || loading}
        />
        <button className="btn btn-primary" onClick={() => send(input)} disabled={busy || loading || !input.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}
