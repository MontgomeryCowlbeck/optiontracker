import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { AutoTextarea, Empty, Modal } from "../components/ui";
import { COARSE_POINTER } from "../lib/device";
import { fmtDayShort } from "../lib/format";
import { Markdown } from "../lib/markdown";
import { useAuth, useToast } from "../state/store";

interface Msg {
  role: "user" | "assistant";
  content: string;
}

interface ChatSession {
  id: number;
  title: string;
  updated_at: string;
  n_messages: number;
}

/* Last-open session per account, so reopening the app resumes the chat. */
const sessionKey = (acct: string) => `pt_chat_session_${acct}`;

export function Chat() {
  const toast = useToast();
  const { accountEpoch, activeAccount } = useAuth();
  const acct = String(activeAccount ?? "");
  const [available, setAvailable] = useState<boolean | null>(null);
  const [sessionId, setSessionId] = useState<number | null>(() => {
    const v = localStorage.getItem(sessionKey(acct));
    return v ? Number(v) : null;
  });
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [sessions, setSessions] = useState<ChatSession[] | null>(null);
  const [delArmed, setDelArmed] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ available: boolean }>("/journal/ai/status")
      .then((r) => setAvailable(!!r.available))
      .catch(() => setAvailable(false));
  }, [accountEpoch]);

  // resume the last-open session; a stale id (deleted chat) just starts fresh
  useEffect(() => {
    const v = localStorage.getItem(sessionKey(acct));
    const sid = v ? Number(v) : null;
    setSessionId(sid);
    setMessages([]);
    if (sid != null) {
      api<{ messages: Msg[] }>(`/journal/ai/chats/${sid}`)
        .then((r) => setMessages(r.messages || []))
        .catch(() => {
          localStorage.removeItem(sessionKey(acct));
          setSessionId(null);
        });
    }
  }, [accountEpoch, acct]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, thinking]);

  const openHistory = () => {
    setShowHistory(true);
    setDelArmed(null);
    api<{ sessions: ChatSession[] }>("/journal/ai/chats")
      .then((r) => setSessions(r.sessions || []))
      .catch(() => setSessions([]));
  };

  const openSession = async (sid: number) => {
    setShowHistory(false);
    try {
      const r = await api<{ messages: Msg[] }>(`/journal/ai/chats/${sid}`);
      setSessionId(sid);
      setMessages(r.messages || []);
      localStorage.setItem(sessionKey(acct), String(sid));
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const deleteSession = async (sid: number) => {
    try {
      await api(`/journal/ai/chats/${sid}`, { method: "DELETE" });
      setSessions((cur) => (cur || []).filter((s) => s.id !== sid));
      if (sid === sessionId) {
        setSessionId(null);
        setMessages([]);
        localStorage.removeItem(sessionKey(acct));
      }
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const newChat = () => {
    setSessionId(null);
    setMessages([]);
    localStorage.removeItem(sessionKey(acct));
  };

  const send = async () => {
    const message = input.trim();
    if (!message || thinking) return;
    setMessages((cur) => [...cur, { role: "user", content: message }]);
    setInput("");
    setThinking(true);
    try {
      const r = await api<{ reply: string; session_id: number }>("/journal/ai/chat", {
        method: "POST",
        body: { message, session_id: sessionId },
        timeoutMs: 300_000,
      });
      setMessages((cur) => [...cur, { role: "assistant", content: r.reply }]);
      if (r.session_id != null && r.session_id !== sessionId) {
        setSessionId(r.session_id);
        localStorage.setItem(sessionKey(acct), String(r.session_id));
      }
    } catch (e) {
      toast((e as Error).message, "err");
      setMessages((cur) => cur.slice(0, -1)); // put the question back in the box
      setInput(message);
    } finally {
      setThinking(false);
    }
  };

  if (available === false) {
    return (
      <Empty hint="AI runs on the kaiju host — inside the container these endpoints answer 503.">
        Chat is offline.
      </Empty>
    );
  }

  // first turn reads the whole journal; later turns resume the conversation
  const firstTurn = messages.filter((m) => m.role === "assistant").length === 0;

  return (
    <div className="chat">
      <div className="chat-bar">
        <button className="btn btn-ghost" onClick={openHistory}>
          History
        </button>
        {messages.length > 0 && (
          <button className="btn btn-ghost" onClick={newChat}>
            New chat
          </button>
        )}
      </div>
      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <Empty hint="It reads your journal — trades, tags, notes — and answers with the numbers. Chats are saved; find them under History.">
            Ask anything about your trading.
          </Empty>
        )}
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
            <span className="muted small" style={{ marginLeft: 6 }}>
              {firstTurn
                ? "reading the journal — the first reply can take a couple of minutes"
                : "thinking — the journal is already loaded in this conversation"}
            </span>
          </div>
        )}
      </div>
      <div className="chat-input">
        <AutoTextarea
          minRows={1}
          placeholder="e.g. where am I breaking my own rules?"
          value={input}
          enterKeyHint="enter"
          onChange={(e) => setInput(e.target.value)}
          onFocus={() =>
            window.setTimeout(
              () => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }),
              300,
            )
          }
          onKeyDown={(e) => {
            // Desktop only: soft keyboards have no Shift+Enter, so Enter must
            // stay a newline there — the Send button submits.
            if (e.key === "Enter" && !e.shiftKey && !COARSE_POINTER) {
              e.preventDefault();
              send();
            }
          }}
          disabled={available === null}
        />
        <button className="btn btn-primary" onClick={send} disabled={thinking || !input.trim()}>
          Send
        </button>
      </div>
      {showHistory && (
        <Modal dismissable title="Chat history" onClose={() => setShowHistory(false)}>
          {sessions == null ? (
            <p className="muted small">Loading…</p>
          ) : sessions.length === 0 ? (
            <Empty>No saved chats yet — send a message and it starts one.</Empty>
          ) : (
            <div className="chat-hist">
              {sessions.map((s) => (
                <div key={s.id} className={"chat-hist-row" + (s.id === sessionId ? " on" : "")}>
                  <button className="chat-hist-open" onClick={() => openSession(s.id)}>
                    <span className="t">{s.title}</span>
                    <span className="muted small">
                      {fmtDayShort(s.updated_at)} · {s.n_messages} messages
                    </span>
                  </button>
                  <button
                    className={"btn btn-ghost" + (delArmed === s.id ? " btn-danger" : "")}
                    onClick={() =>
                      delArmed === s.id ? (setDelArmed(null), deleteSession(s.id)) : setDelArmed(s.id)
                    }
                  >
                    {delArmed === s.id ? "Sure?" : "Delete"}
                  </button>
                </div>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
