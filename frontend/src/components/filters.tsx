/* Modern filter kit — token pills shared by the global FilterBar and
   view-local bars (Positions). A pill reads "Label" when idle and
   "Label · Value" with its own clear × when active; options open in a
   popover. The popover is position:fixed AND portaled to document.body:
   the bars scroll horizontally on mobile, and iOS Safari both clips and
   mis-hit-tests fixed elements left inside a momentum-scroll container
   (taps landed behind the visible menu). Outside-close is a containment
   check on the native event target — no stopPropagation choreography,
   which portals would break anyway. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface PillOption {
  value: string;
  /** menu row content */
  label: ReactNode;
  /** compact text shown inside the pill when selected (falls back to label) */
  short?: string;
  /** color dot, e.g. tag colors */
  swatch?: string | null;
  /** quiet second line under the label */
  hint?: string;
}

const POP_W = 240;

type PopPos = { left: number; top?: number; bottom?: number };

function popPosition(anchor: HTMLElement): PopPos {
  const r = anchor.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.left, window.innerWidth - POP_W - 8));
  // open upward when the pill sits in the bottom third — bottom nav territory
  return r.bottom > window.innerHeight - 320
    ? { left, bottom: window.innerHeight - r.top + 6 }
    : { left, top: r.bottom + 6 };
}

function Popover({
  pos,
  anchor,
  onClose,
  children,
}: {
  pos: PopPos;
  /** the pill that opened the menu — taps on it must not count as "outside" */
  anchor: HTMLElement | null;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const down = (e: PointerEvent) => {
      const t = e.target;
      if (t instanceof Node && (ref.current?.contains(t) || anchor?.contains(t))) return;
      onClose();
    };
    // capture-phase scroll closes a menu whose anchor moved — but scrolling
    // the option list itself must not
    const scroll = (e: Event) => {
      if (ref.current && e.target instanceof Node && ref.current.contains(e.target)) return;
      onClose();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("pointerdown", down);
    window.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", onClose);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", down);
      window.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", onClose);
      document.removeEventListener("keydown", key);
    };
  }, [onClose, anchor]);
  return createPortal(
    <div ref={ref} className="fpop" role="listbox" style={{ position: "fixed", width: POP_W, ...pos }}>
      {children}
    </div>,
    document.body,
  );
}

/* One filter dimension as a token pill + option popover. */
export function FilterPill({
  label,
  value,
  options,
  onChange,
  allLabel,
  clearable = true,
}: {
  label: string;
  value: string | null;
  options: PillOption[];
  onChange: (v: string | null) => void;
  /** first menu row that clears the filter, e.g. "All tickers" */
  allLabel?: string;
  /** false for always-set dimensions like Sort — hides the × and the all-row */
  clearable?: boolean;
}) {
  const [pos, setPos] = useState<PopPos | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const cur = value != null ? options.find((o) => o.value === value) ?? null : null;
  const active = clearable && cur != null;
  const pick = (v: string | null) => {
    setPos(null);
    onChange(v);
  };
  return (
    <div ref={wrapRef} className={"fpill" + (active ? " on" : "")}>
      <button
        ref={btnRef}
        type="button"
        className="fpill-btn"
        aria-label={label}
        aria-expanded={pos != null}
        onClick={() => setPos(pos ? null : popPosition(btnRef.current!))}
      >
        <span className={cur ? "fpill-lbl" : undefined}>{label}</span>
        {cur ? <span className="fpill-val">{cur.short ?? cur.label}</span> : null}
        <svg className="caret" width="8" height="5" viewBox="0 0 8 5" aria-hidden>
          <path d="M1 1l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {active ? (
        <button
          type="button"
          className="fpill-x"
          aria-label={`Clear ${label} filter`}
          onClick={() => pick(null)}
        >
          ×
        </button>
      ) : null}
      {pos ? (
        <Popover pos={pos} anchor={wrapRef.current} onClose={() => setPos(null)}>
          {clearable && (
            <button type="button" className={value == null ? "on" : ""} onClick={() => pick(null)}>
              <span>{allLabel ?? `All ${label.toLowerCase()}s`}</span>
              {value == null ? <Check /> : null}
            </button>
          )}
          {options.map((o) => (
            <button
              type="button"
              key={o.value}
              className={o.value === value ? "on" : ""}
              onClick={() => pick(o.value)}
            >
              {o.swatch ? <i className="fpop-dot" style={{ background: o.swatch }} /> : null}
              <span className="fpop-body">
                {o.label}
                {o.hint ? <span className="fpop-hint">{o.hint}</span> : null}
              </span>
              {o.value === value ? <Check /> : null}
            </button>
          ))}
        </Popover>
      ) : null}
    </div>
  );
}

function Check() {
  return (
    <svg className="fpop-check" width="12" height="12" viewBox="0 0 12 12" aria-hidden>
      <path d="M2 6.5l2.6 2.6L10 3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* Joined segment group, filter-bar styling (fully rounded). */
export function FilterSeg<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly (readonly [T, string])[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="fseg" role="group" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} type="button" className={value === v ? "on" : ""} onClick={() => onChange(v)}>
          {l}
        </button>
      ))}
    </div>
  );
}

/* Free-text pill (underlying search). */
export function SearchPill({
  value,
  onChange,
  placeholder,
  width = 92,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  width?: number;
}) {
  return (
    <label className={"fsearch" + (value ? " on" : "")}>
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
        <circle cx="5" cy="5" r="3.4" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M7.8 7.8L11 11" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      <input
        style={{ width }}
        aria-label={placeholder}
        placeholder={placeholder}
        autoCapitalize="characters"
        autoCorrect="off"
        spellCheck={false}
        autoComplete="off"
        enterKeyHint="search"
        value={value}
        onChange={(e) => onChange(e.target.value.toUpperCase())}
      />
      {value ? (
        <button type="button" className="fpill-x" aria-label={`Clear ${placeholder}`} onClick={() => onChange("")}>
          ×
        </button>
      ) : null}
    </label>
  );
}

/* Trailing "n of m · Clear" block, shown only while something filters. */
export function FilterReset({
  shown,
  total,
  onClear,
}: {
  shown?: number;
  total?: number;
  onClear: () => void;
}) {
  return (
    <span className="fbar-reset">
      {shown != null && total != null && shown !== total ? (
        <span className="muted small num">
          {shown} of {total}
        </span>
      ) : null}
      <button type="button" className="fbar-clear" onClick={onClear}>
        Clear
      </button>
    </span>
  );
}
