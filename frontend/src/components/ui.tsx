import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode, TextareaHTMLAttributes } from "react";
import { pct as fmtPct, pnlClass } from "../lib/format";
import { useDisplay } from "../state/store";

/* The one P&L component — every $ value on the platform goes through this,
   so the global display mode ($ / R / % of account / privacy) applies
   everywhere at once. Pass `risk` (planned risk) where known to enable R. */
export function Pnl({
  v,
  pctVal,
  risk,
  className,
}: {
  v: number | null | undefined;
  pctVal?: number | null;
  risk?: number | null;
  className?: string;
}) {
  const { fmtPnl, mode } = useDisplay();
  return (
    <span className={`pnl ${pnlClass(v)} ${className || ""}`}>
      {fmtPnl(v, risk)}
      {pctVal != null && mode !== "privacy" ? ` (${fmtPct(pctVal)})` : ""}
    </span>
  );
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/* The one dialog. Structured header (title + X), focus trap + restore, body
   scroll lock, animated in/out, bottom sheet on mobile.

   `dismissable` (default false) controls ESC + backdrop-click: forms and
   conversations hold unsaved state, so by default those paths SHAKE the panel
   and pulse the X instead of silently discarding — the X (keyboard-focusable)
   and the child's own Cancel remain the explicit exits. Pass dismissable for
   read-only content (e.g. the deep-dive panel). */
export function Modal({
  children,
  wide,
  title,
  onClose,
  dismissable = false,
}: {
  children: ReactNode;
  wide?: boolean;
  title?: ReactNode;
  onClose?: () => void;
  dismissable?: boolean;
}) {
  const [closing, setClosing] = useState(false);
  const [shake, setShake] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<Element | null>(null);

  const close = useCallback(() => {
    if (!onClose || closing) return;
    setClosing(true);
    window.setTimeout(onClose, 150); // matches --modal-out duration
  }, [onClose, closing]);

  const deny = useCallback(() => {
    setShake(true);
    panelRef.current?.querySelector<HTMLElement>(".modal-x")?.focus();
    window.setTimeout(() => setShake(false), 400);
  }, []);

  useEffect(() => {
    restoreRef.current = document.activeElement;
    // Don't steal focus from a child's autoFocus (React applies it before this
    // effect runs) — on mobile that popped the keyboard and instantly killed it.
    if (!panelRef.current?.contains(document.activeElement)) panelRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
      (restoreRef.current as HTMLElement | null)?.focus?.();
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        if (dismissable) close();
        else if (onClose) deny();
      } else if (e.key === "Tab" && panelRef.current) {
        // Keep Tab inside the dialog.
        const nodes = panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE);
        if (!nodes.length) return;
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [close, deny, dismissable, onClose]);

  return (
    <div
      className={"modal-backdrop" + (closing ? " closing" : "")}
      onMouseDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (dismissable) close();
        else if (onClose) deny();
      }}
    >
      <div
        ref={panelRef}
        className={"modal" + (wide ? " wide" : "") + (closing ? " closing" : "") + (shake ? " shake" : "")}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
      >
        {title != null || onClose ? (
          <div className={"modal-head" + (title == null ? " bare" : "")}>
            {title != null ? <div className="modal-title">{title}</div> : null}
            {onClose ? (
              <button className="modal-x" aria-label="Close" title="Close" onClick={close}>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M18 6 6 18" />
                  <path d="m6 6 12 12" />
                </svg>
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

/* Fixed palette offered when creating a tag — any #rrggbb is legal server-side. */
export const TAG_COLORS = [
  "#3987e5", // blue
  "#d03b3b", // red
  "#d55181", // pink
  "#199e70", // green
  "#fab219", // amber
  "#8b5cf6", // purple
  "#14b8a6", // teal
  "#8a8f98", // gray
];

/* Inline "new tag" composer: name + color swatch + explicit Add. Deliberately
   NOT dismissed on blur — mobile keyboards blur the input on "Done" and a
   blur-dismiss silently ate the draft. Only Add, Escape, or ✕ close it. */
export function TagComposer({ onCreate }: { onCreate: (name: string, color: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [color, setColor] = useState(TAG_COLORS[0]);
  const [busy, setBusy] = useState(false);

  const add = async () => {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    try {
      await onCreate(n, color);
      setName("");
      setOpen(false);
    } catch {
      /* caller already toasted; stay open so the draft survives */
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className="chip" onClick={() => setOpen(true)}>
        + new tag
      </button>
    );
  }
  return (
    <span className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
      <input
        autoFocus
        style={{ width: 150 }}
        placeholder="tag name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        enterKeyHint="done"
        autoComplete="off"
        onKeyDown={(e) => {
          if (e.key === "Enter") add();
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {TAG_COLORS.map((c) => (
        <button
          key={c}
          type="button"
          className={"swatch" + (c === color ? " on" : "")}
          style={{ background: c }}
          aria-label={`color ${c}`}
          onClick={() => setColor(c)}
        />
      ))}
      <button type="button" className="btn btn-primary btn-sm" onClick={add} disabled={busy || !name.trim()}>
        Add
      </button>
      <button type="button" className="btn btn-ghost btn-sm" aria-label="Cancel" onClick={() => setOpen(false)}>
        ✕
      </button>
    </span>
  );
}

/* Textarea that grows with its content — the CSS resize grip is a no-op on iOS
   and a fixed 2-row box is no place to journal. Caps at ~40% of the viewport,
   then scrolls internally. */
export function AutoTextarea({
  value,
  minRows = 3,
  style,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { minRows?: number }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight + 2, Math.round(window.innerHeight * 0.4)) + "px";
  }, [value]);
  return (
    <textarea
      ref={ref}
      rows={minRows}
      value={value}
      style={{ ...style, resize: "none", overflow: "auto" }}
      {...props}
    />
  );
}

/* 1–5 conviction as five tappable chips (replaces the 30px range slider, which
   was scroll-draggable by accident inside a bottom sheet). Tap the current
   value again to clear. */
export function ConvictionPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  return (
    <div className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          className={"chip" + (value != null && value >= n ? " on" : "")}
          aria-label={`conviction ${n}` + (value === n ? " — tap to clear" : "")}
          onClick={() => onChange(value === n ? null : n)}
        >
          {value != null && value >= n ? "▲" : "△"}
        </button>
      ))}
    </div>
  );
}

export function Skeleton({ h = 120, n = 1 }: { h?: number; n?: number }) {
  return (
    <div className="stack" aria-hidden>
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="skel" style={{ height: h }} />
      ))}
    </div>
  );
}

export function Empty({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="empty">
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

export function StatTile({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  valueClass?: string;
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className={"value " + (valueClass || "")}>{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="empty">{error}</div>;
}
