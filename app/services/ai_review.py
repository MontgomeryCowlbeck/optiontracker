"""AI layer — day reviews and chat-over-journal via headless `claude -p`
(the proven watchtower brief.py pattern). Code computes ALL numbers and hands
them to the LLM; the LLM adds coaching and pattern-reading, never arithmetic.

Runs wherever the `claude` CLI exists (kaiju host). In a container without the
CLI the endpoints return 503 and the rest of the app is unaffected — the
nightly host-side cron (scripts/ai_day_review.py) covers reviews there."""
import asyncio
import json
import shutil
from pathlib import Path

from app.config import get_settings
from app.database import fetch_all, fetch_one, get_db
from app.services.journal_analytics import compute_journal_analytics, compute_motd
from app.services.journal_trades import fetch_trades

# claude -p sometimes exits 0 with a usage-limit banner as its only output.
_LIMIT_MARKERS = ("usage limit", "rate limit", "resets at", "claude ai usage limit")

REVIEW_PROMPT = """You are the end-of-day reviewer inside a private trade journal.
The trader's philosophy: reward discipline, not wins; a by-the-book loss is a good
trade; a lucky rule-break is a bad one. "If a trade is exciting, don't take it."

All numbers below are computed by the journal and are authoritative — never
recompute or invent figures. Write a review of THIS day in markdown (<= 350 words):
1. **Process** — plan-adherence, per trade if it earns comment. Praise well-executed
   losers; call out rule-breaks even when they made money.
2. **Patterns** — anything the tags/edges/notes suggest (recurring mistakes,
   emotional states preceding losses, edges performing vs their expectation).
3. **One instruction for tomorrow** — a single concrete, checkable sentence.
Address the trader directly. No preamble, no sign-off.

DAY: {date}
{context}"""

REVIEW_REPLY_PROMPT = """You are the end-of-day reviewer inside a private trade
journal, continuing a conversation about the review you wrote for one trading day.
The trader is responding to that review — pushing back, adding context the journal
didn't capture, or asking for more depth. Engage with what he actually says:
concede when his context genuinely changes the read, hold your ground when the
data still supports it. All numbers below are computed by the journal and are
authoritative — never invent or recompute figures. Be direct and concise;
markdown allowed. No preamble, no sign-off.
The trader's philosophy: reward discipline, not wins; a by-the-book loss is a
good trade; a lucky rule-break is a bad one.

DAY: {date}
{context}

YOUR REVIEW OF THE DAY:
{review}

{history}Trader: {message}"""

CHAT_PROMPT = """You are the analyst inside a private options trade journal, in an
ongoing chat with the trader. All statistics below are computed by the journal and
are authoritative — never invent or recompute numbers; if the data can't answer,
say so. Ground every claim in the data. Be direct and concise; markdown allowed.
The trader's philosophy: discipline over outcomes, process over P&L.

JOURNAL DATA:
{context}

{history}Trader: {message}"""


def _claude_bin() -> str | None:
    """Resolve the claude CLI even under a minimal PATH."""
    configured = get_settings().claude_bin
    found = shutil.which(configured) if configured else None
    if found:
        return found
    fallback = Path.home() / ".local/bin/claude"
    return str(fallback) if fallback.exists() else None


def ai_available() -> bool:
    return _claude_bin() is not None


async def _run_claude(prompt: str, tools: list[str] | None = None) -> str:
    """Run `claude -p` headless and return its text. No tools by default; pass
    e.g. ["WebSearch", "WebFetch"] for briefs that need live context. Raises
    RuntimeError with a user-facing message on any failure."""
    bin_ = _claude_bin()
    if bin_ is None:
        raise RuntimeError("claude CLI not available on this host")
    args = [bin_, "-p", prompt, "--output-format", "text"]
    if tools:
        args += ["--allowedTools", *tools]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=get_settings().ai_timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("AI request timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {err.decode()[:200]}")
    text = out.decode().strip()
    if not text:
        raise RuntimeError("AI returned no output")
    if any(m in text[:200].lower() for m in _LIMIT_MARKERS):
        raise RuntimeError(f"AI session limit: {text[:120]}")
    return text


async def _run_claude_json(prompt: str, resume: str | None = None) -> tuple[str, str | None]:
    """Like _run_claude, but JSON output so the CLI's conversation id comes back.
    With `resume`, continues that conversation — the model already holds the
    journal context from turn one, so only the new message travels. Returns
    (reply, claude_session_id)."""
    bin_ = _claude_bin()
    if bin_ is None:
        raise RuntimeError("claude CLI not available on this host")
    args = [bin_, "-p", prompt, "--output-format", "json"]
    if resume:
        args += ["--resume", resume]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=get_settings().ai_timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("AI request timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {err.decode()[:200]}")
    try:
        payload = json.loads(out.decode())
    except ValueError:
        raise RuntimeError("AI returned unparseable output")
    text = (payload.get("result") or "").strip()
    if payload.get("is_error") or not text:
        raise RuntimeError("AI returned no output")
    if any(m in text[:200].lower() for m in _LIMIT_MARKERS):
        raise RuntimeError(f"AI session limit: {text[:120]}")
    return text, payload.get("session_id")


def _trade_line(t: dict) -> dict:
    """The fields worth showing the LLM for one trade — journal voice included."""
    keep = ["underlying", "direction", "strikes", "expiration", "dte_at_entry",
            "quantity", "entry_premium", "exit_premium", "realized_pnl",
            "realized_pnl_pct", "planned_risk", "status", "edge_name", "mechanism_name",
            "is_system", "conviction", "exit_reason", "emotional_state",
            "why_entered", "thesis_worked", "reflection", "mistake_tag",
            "regime_read", "entry_at", "exit_at"]
    out = {k: t[k] for k in keep if t.get(k) is not None}
    if t.get("tags"):
        out["tags"] = [x["name"] for x in t["tags"]]
    if t.get("planned_risk") and t.get("realized_pnl") is not None and t["planned_risk"] > 0:
        out["r_multiple"] = round(t["realized_pnl"] / t["planned_risk"], 2)
    return out


async def _day_context(account_id: int, date: str) -> tuple[list[dict], str]:
    """The day's computed facts as (day_trades, context-JSON) — shared by the
    review and the review-reply thread so both ground in the same numbers."""
    trades = await fetch_trades(account_id)
    day_trades = [t for t in trades
                  if str(t.get("exit_at") or t.get("entry_at") or "").startswith(date)]
    closed = [t for t in day_trades if t.get("realized_pnl") is not None]
    note = await fetch_one(
        "SELECT note FROM journal_day_notes WHERE account_id = ? AND date = ?",
        (account_id, date),
    )
    all_closed = [t for t in trades if t.get("realized_pnl") is not None]
    streak = compute_journal_analytics(all_closed)["discipline"]["current_streak"]
    context = json.dumps({
        "trades": [_trade_line(t) for t in day_trades],
        "day_stats": compute_motd(closed, streak),
        "trader_note": (note or {}).get("note"),
    }, indent=1, default=str)
    return day_trades, context


async def generate_day_review(account_id: int, date: str) -> str:
    """Compose the day's computed facts and ask the LLM for the review."""
    day_trades, context = await _day_context(account_id, date)
    if not day_trades:
        raise RuntimeError(f"no journal trades on {date} — nothing to review")
    return await _run_claude(REVIEW_PROMPT.format(date=date, context=context))


async def day_review_thread(account_id: int, date: str) -> list[dict]:
    """The stored conversation under one day's AI review, oldest first."""
    rows = await fetch_all(
        """SELECT role, content, created_at FROM day_review_messages
           WHERE account_id = ? AND date = ? ORDER BY id""",
        (account_id, date),
    )
    return [dict(r) for r in rows]


async def day_review_reply(account_id: int, date: str, message: str) -> str:
    """One trader turn in the conversation under a day's stored AI review.
    Raises ValueError when the day has no review to respond to."""
    note = await fetch_one(
        "SELECT ai_feedback FROM journal_day_notes WHERE account_id = ? AND date = ?",
        (account_id, date),
    )
    review = (note or {}).get("ai_feedback")
    if not review:
        raise ValueError(f"no AI review on {date} — generate one first")
    _, context = await _day_context(account_id, date)
    lines = ""
    for h in (await day_review_thread(account_id, date))[-12:]:
        who = "Trader" if h.get("role") == "user" else "Reviewer"
        lines += f"{who}: {h.get('content', '')}\n"
    reply = await _run_claude(REVIEW_REPLY_PROMPT.format(
        date=date, context=context, review=review, history=lines, message=message))
    async with get_db() as db:
        for role, content in (("user", message), ("assistant", reply)):
            await db.execute(
                """INSERT INTO day_review_messages (account_id, date, role, content)
                   VALUES (?, ?, ?, ?)""",
                (account_id, date, role, content),
            )
        await db.commit()
    return reply


async def clear_day_review_thread(account_id: int, date: str) -> None:
    async with get_db() as db:
        await db.execute(
            "DELETE FROM day_review_messages WHERE account_id = ? AND date = ?",
            (account_id, date),
        )
        await db.commit()


async def _chat_context(account_id: int) -> str:
    trades = await fetch_trades(account_id)
    closed = [t for t in trades if t.get("realized_pnl") is not None]
    return json.dumps({
        "analytics": compute_journal_analytics(closed),
        # Recent-first; capped so the prompt stays bounded.
        "recent_trades": [_trade_line(t) for t in trades[:60]],
    }, indent=1, default=str)


def _history_lines(history: list[dict]) -> str:
    lines = ""
    for h in history[-12:]:
        who = "Trader" if h.get("role") == "user" else "Analyst"
        lines += f"{who}: {h.get('content', '')}\n"
    return lines


async def chat(account_id: int, message: str, history: list[dict] | None = None) -> str:
    """Legacy stateless turn (client-held history, nothing stored). Kept for
    API-key consumers; the platform UI uses chat_turn/sessions."""
    context = await _chat_context(account_id)
    return await _run_claude(CHAT_PROMPT.format(
        context=context, history=_history_lines(history or []), message=message))


# ---- persistent chat sessions -------------------------------------------------
# The journal context is read ONCE, on a session's first turn; every later turn
# resumes the same claude CLI conversation and sends only the new message. If
# the CLI session has been evicted, the turn transparently rebuilds from the
# stored transcript (fresh context + last 12 messages) and re-captures a new
# conversation id.

async def chat_sessions(account_id: int) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM ai_chat_messages m
                        WHERE m.session_id = s.id) AS n_messages
                 FROM ai_chat_sessions s
                WHERE s.account_id = ?
                ORDER BY s.updated_at DESC""",
            (account_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def chat_session_messages(account_id: int, session_id: int) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id FROM ai_chat_sessions WHERE id = ? AND account_id = ?",
            (session_id, account_id),
        )
        if await cur.fetchone() is None:
            raise ValueError("no such chat")
        cur = await db.execute(
            """SELECT role, content, created_at FROM ai_chat_messages
                WHERE session_id = ? ORDER BY id""",
            (session_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def delete_chat_session(account_id: int, session_id: int) -> None:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id FROM ai_chat_sessions WHERE id = ? AND account_id = ?",
            (session_id, account_id),
        )
        if await cur.fetchone() is None:
            raise ValueError("no such chat")
        await db.execute("DELETE FROM ai_chat_messages WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM ai_chat_sessions WHERE id = ?", (session_id,))
        await db.commit()


async def chat_turn(account_id: int, message: str,
                    session_id: int | None = None) -> dict:
    """One turn of a persistent chat. Creates the session on first use.
    Returns {reply, session_id, resumed}."""
    claude_sid: str | None = None
    history: list[dict] = []
    async with get_db() as db:
        if session_id is not None:
            cur = await db.execute(
                "SELECT claude_session_id FROM ai_chat_sessions WHERE id = ? AND account_id = ?",
                (session_id, account_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise ValueError("no such chat")
            claude_sid = row["claude_session_id"]
            cur = await db.execute(
                "SELECT role, content FROM ai_chat_messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            )
            history = [dict(r) for r in await cur.fetchall()]

    resumed = False
    reply: str | None = None
    new_sid: str | None = None
    if claude_sid:
        try:
            reply, new_sid = await _run_claude_json(message, resume=claude_sid)
            resumed = True
        except RuntimeError:
            # evicted/expired CLI session — rebuild below from the stored transcript
            reply = None
    if reply is None:
        context = await _chat_context(account_id)
        reply, new_sid = await _run_claude_json(CHAT_PROMPT.format(
            context=context, history=_history_lines(history), message=message))

    async with get_db() as db:
        if session_id is None:
            title = message.strip().splitlines()[0][:60]
            cur = await db.execute(
                "INSERT INTO ai_chat_sessions (account_id, title, claude_session_id) VALUES (?, ?, ?)",
                (account_id, title, new_sid),
            )
            session_id = cur.lastrowid
        else:
            await db.execute(
                """UPDATE ai_chat_sessions SET claude_session_id = ?,
                          updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (new_sid, session_id),
            )
        await db.executemany(
            "INSERT INTO ai_chat_messages (session_id, role, content) VALUES (?, ?, ?)",
            [(session_id, "user", message), (session_id, "assistant", reply)],
        )
        await db.commit()
    return {"reply": reply, "session_id": session_id, "resumed": resumed}


CONSULT_PROMPT = """You are the desk consultant inside a private options trade
journal, in a live conversation with the trader. He is a premium seller: put
credit spreads and cash-secured puts, 25-50 DTE, 50% profit targets, defined
risk, "if a trade is exciting, don't take it". Discretionary trades are a
legitimate sleeve — your job is to make each one deliberate.

The conversation can be about ANY of these — read his message for which:
- a NEW entry he's weighing (interrogate intent, then verdict),
- an OPEN POSITION he already holds (open_positions_this_name in DATA is his
  live book with the last captured per-leg marks/greeks and their as-of
  timestamps — that position IS the subject; discuss management: hold toward
  the 50% target, close, roll, or take assignment given his stance. Marks may
  be stale outside market hours — say so, with the timestamp, rather than
  claiming you lack data. If DATA carries subject_position, he opened this
  consult FROM that exact trade: it is the subject, skip the intent gate, and
  ground the whole conversation in its numbers — pct_of_credit vs the 50%
  target, DTE, position delta/theta, max loss, days held),
- or just an ongoing WATCH — he may have followed this name for months; the
  research_notes and journal history are the trail. A "current read" or a
  thesis check deserves a conversational answer, not a trade pitch.

You ARE allowed to make a recommendation — that is why he came to you. But:
- Every number you use MUST come from the DATA below (DD stats, zones, the
  candidate-put table, his positions, his journal). Never invent or recompute
  a figure. If something you need is missing, name what's missing.
- For a NEW entry, if his intent isn't clear (goal: income vs entry-to-own;
  assignment tolerance; rough size; time horizon), your FIRST reply is 2-4
  pointed questions — no recommendation yet. His stored assignment stance in
  DATA counts as known. Position reviews and reads don't need this gate.
- A recommendation, when you give one, takes this shape (markdown):
  **Verdict** — favorable / unfavorable / wait-for-level (or for an open
  position: hold / close / roll / let it assign), one sentence of why.
  **Structure** — a specific candidate FROM the put table, or the concrete
  management action; if neither: what level or condition would change it.
  **The case** — 2-3 sentences tying TA (zones, RSI, trend), vol (IV/RV),
  earnings timing, and his intent together.
  **Risks** — what breaks this, honestly, including assignment mechanics.
  **Invalidation** — concrete, checkable exit-the-thesis conditions.
- This is a CONVERSATION. After a verdict, answer follow-ups directly and
  briefly — do NOT re-run the full verdict format or re-summarize the DD on
  every message. Produce a fresh Verdict block only when the facts or his
  intent change, or when he asks for one.
- Use WebSearch for catalysts/sentiment when it would change the answer;
  attribute what you find.
- Be direct. He can handle "this doesn't pay enough" or "wrong tool — you
  don't want assignment and this strike gets assigned."

DATA (code-computed):
{context}

{history}Trader: {message}"""


async def consult(account_id: int, symbol: str, message: str,
                  history: list[dict] | None = None,
                  focus_trade_id: int | None = None) -> str:
    """One turn of the trading-agent consult on a research name. First turn
    interrogates intent; later turns deliver the recommendation. With
    focus_trade_id the consult was opened from a specific open position — it
    becomes subject_position in the context and the intent gate is skipped."""
    from app.services.dd import fetch_dd, fetch_candidate_puts
    from app.services.positions import open_positions

    dd = await fetch_dd(symbol)
    puts = await fetch_candidate_puts(symbol)
    book = await open_positions(account_id, underlying=symbol.upper())
    wl = await fetch_one(
        """SELECT thesis, assignment_ok FROM research_watchlist
           WHERE account_id = ? AND symbol = ?""",
        (account_id, symbol.upper()),
    )
    trades = await fetch_trades(account_id, underlying=symbol.upper())
    notes = await fetch_all(
        """SELECT note, created_at FROM research_notes
           WHERE account_id = ? AND symbol = ? ORDER BY id DESC LIMIT 10""",
        (account_id, symbol.upper()),
    )
    stance = None
    if wl and wl.get("assignment_ok") is not None:
        stance = "willing to take assignment" if wl["assignment_ok"] else "wants to AVOID assignment"
    open_here = [
        {k: p.get(k) for k in
         ("id", "direction", "strikes", "expiration", "quantity", "entry_premium",
          "entry_at", "days_open", "unrealized_pnl", "pct_of_credit", "max_loss",
          "position_delta", "position_theta", "legs", "edge_name",
          "why_entered", "planned_risk")}
        for p in book["positions"]
    ]
    subject = None
    if focus_trade_id is not None:
        subject = next((p for p in open_here if p["id"] == focus_trade_id), None)
        if subject is None:
            raise ValueError(f"trade {focus_trade_id} is not an open position on {symbol}")
    context = json.dumps({
        **({"subject_position": subject} if subject else {}),
        "dd": {k: v for k, v in dd.items() if k != "series"},
        "candidate_puts": puts or "chain unavailable — do not quote premium numbers",
        "open_positions_this_name": open_here or "none — he holds nothing on this name",
        "trader_thesis": (wl or {}).get("thesis"),
        "assignment_stance": stance,
        "research_notes": [dict(x) for x in notes],
        "journal_history_this_name": [_trade_line(t) for t in trades[:15]],
    }, indent=1, default=str)
    lines = ""
    for h in (history or [])[-12:]:
        who = "Trader" if h.get("role") == "user" else "Consultant"
        lines += f"{who}: {h.get('content', '')}\n"
    return await _run_claude(
        CONSULT_PROMPT.format(context=context, history=lines, message=message),
        tools=["WebSearch", "WebFetch"])


def _thread_where(symbol: str | None, trade_id: int | None) -> tuple[str, tuple]:
    """One thread per open position (trade_id) or per research name (trade_id NULL)."""
    if trade_id is not None:
        return "trade_id = ?", (trade_id,)
    return "symbol = ? AND trade_id IS NULL", (symbol.upper(),)


async def consult_thread(account_id: int, symbol: str | None = None,
                         trade_id: int | None = None) -> list[dict]:
    """The stored consult conversation, oldest first."""
    where, params = _thread_where(symbol, trade_id)
    rows = await fetch_all(
        f"""SELECT role, content, created_at FROM consult_messages
            WHERE account_id = ? AND {where} ORDER BY id""",
        (account_id, *params),
    )
    return [dict(r) for r in rows]


async def log_consult_turn(account_id: int, symbol: str, trade_id: int | None,
                           message: str, reply: str) -> None:
    """Append one exchange (trader message + consultant reply) to the thread."""
    async with get_db() as db:
        for role, content in (("user", message), ("assistant", reply)):
            await db.execute(
                """INSERT INTO consult_messages (account_id, symbol, trade_id, role, content)
                   VALUES (?, ?, ?, ?, ?)""",
                (account_id, symbol.upper(), trade_id, role, content),
            )
        await db.commit()


async def clear_consult(account_id: int, symbol: str | None = None,
                        trade_id: int | None = None) -> None:
    where, params = _thread_where(symbol, trade_id)
    async with get_db() as db:
        await db.execute(
            f"DELETE FROM consult_messages WHERE account_id = ? AND {where}",
            (account_id, *params),
        )
        await db.commit()


async def generate_dd_brief(account_id: int, dd_payload: dict) -> str:
    """DD research brief for one symbol: the code-computed DD numbers plus this
    trader's own journal record on the name, WebSearch enabled for catalysts."""
    from app.services.dd import DD_BRIEF_PROMPT

    symbol = dd_payload.get("symbol", "")
    trades = await fetch_trades(account_id, underlying=symbol)
    journal = ([_trade_line(t) for t in trades[:20]]
               or ["no journal trades on this name yet"])
    # The chart series is for the UI — the LLM reads the derived stats/zones.
    context = {k: v for k, v in dd_payload.items() if k != "series"}
    return await _run_claude(
        DD_BRIEF_PROMPT.format(
            context=json.dumps(context, indent=1, default=str),
            journal=json.dumps(journal, indent=1, default=str)),
        tools=["WebSearch", "WebFetch"])
