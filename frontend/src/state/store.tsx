/* App-wide state: toasts, auth/accounts, and the global analytics filter. */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  getAccountId,
  getToken,
  setAccountId,
  setToken,
  setUnauthorizedHandler,
} from "../api/client";
import type { Account, LoginResponse } from "../api/types";

/* ---------------- toasts ---------------- */

interface ToastItem {
  id: number;
  msg: string;
  kind: "info" | "err";
}
interface ToastCtx {
  toast: (msg: string, kind?: "info" | "err") => void;
  toasts: ToastItem[];
}
const ToastContext = createContext<ToastCtx>({ toast: () => {}, toasts: [] });
export const useToast = () => useContext(ToastContext).toast;

let toastSeq = 0;

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toast = useCallback((msg: string, kind: "info" | "err" = "info") => {
    const id = ++toastSeq;
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);
  const value = useMemo(() => ({ toast, toasts }), [toast, toasts]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="status">
        {toasts.map((t) => (
          <div key={t.id} className={"toast" + (t.kind === "err" ? " err" : "")}>
            {t.msg}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/* ---------------- auth ---------------- */

interface AuthCtx {
  authed: boolean;
  booting: boolean;
  accounts: Account[];
  activeAccount: number | null;
  /** bumps every time the active account changes — views refetch on it */
  accountEpoch: number;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  switchAccount: (id: number) => void;
  refreshAccounts: () => Promise<void>;
}
const AuthContext = createContext<AuthCtx | null>(null);
export function useAuth(): AuthCtx {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}

function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState<boolean>(!!getToken());
  const [booting, setBooting] = useState<boolean>(!!getToken());
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [activeAccount, setActive] = useState<number | null>(getAccountId());
  const [accountEpoch, setEpoch] = useState(0);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAuthed(false);
      setBooting(false);
    });
  }, []);

  const adoptAccounts = useCallback((accts: Account[]) => {
    setAccounts(accts);
    const stored = getAccountId();
    if (stored == null || !accts.some((a) => a.id === stored)) {
      const def = accts.find((a) => a.is_default) || accts[0];
      const id = def ? def.id : null;
      setAccountId(id);
      setActive(id);
    }
  }, []);

  const refreshAccounts = useCallback(async () => {
    const accts = await api<Account[]>("/auth/accounts");
    adoptAccounts(Array.isArray(accts) ? accts : []);
  }, [adoptAccounts]);

  // boot: validate stored token
  const bootedRef = useRef(false);
  useEffect(() => {
    if (bootedRef.current) return;
    bootedRef.current = true;
    if (!getToken()) return;
    (async () => {
      try {
        await api("/auth/me");
        await refreshAccounts();
        setAuthed(true);
      } catch {
        /* 401 handler cleared the token */
      } finally {
        setBooting(false);
      }
    })();
  }, [refreshAccounts]);

  const login = useCallback(
    async (username: string, password: string) => {
      const data = await api<LoginResponse>("/auth/login", {
        method: "POST",
        body: { username, password },
        noAuthRedirect: true,
      });
      setToken(data.access_token);
      adoptAccounts(data.accounts || []);
      setAuthed(true);
      setEpoch((e) => e + 1);
    },
    [adoptAccounts],
  );

  const logout = useCallback(() => {
    setToken(null);
    setAuthed(false);
  }, []);

  const switchAccount = useCallback((id: number) => {
    setAccountId(id);
    setActive(id);
    setEpoch((e) => e + 1);
  }, []);

  const value = useMemo(
    () => ({
      authed,
      booting,
      accounts,
      activeAccount,
      accountEpoch,
      login,
      logout,
      switchAccount,
      refreshAccounts,
    }),
    [authed, booting, accounts, activeAccount, accountEpoch, login, logout, switchAccount, refreshAccounts],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/* ---------------- display mode (the app-wide "View" switch) ---------------- */

/** How P&L dollars render everywhere: raw $, R-multiples (per-trade planned
 *  risk), % of account (latest net-liq), or privacy (masked for screenshots).
 *  Render-layer only — every number still comes from the server. */
export type DisplayMode = "$" | "R" | "%acct" | "privacy";
export const DISPLAY_MODES: { id: DisplayMode; label: string; title: string }[] = [
  { id: "$", label: "$", title: "Dollars" },
  { id: "R", label: "R", title: "R-multiples (falls back to $ where no planned risk)" },
  { id: "%acct", label: "%", title: "% of account (latest net-liq)" },
  { id: "privacy", label: "◦◦◦", title: "Privacy — mask dollar amounts" },
];

interface DisplayCtx {
  mode: DisplayMode;
  setMode: (m: DisplayMode) => void;
  /** latest account net-liq (benchmark snapshot); null until known */
  equity: number | null;
  /** format a P&L dollar value under the active mode; `risk` enables R */
  fmtPnl: (v: number | null | undefined, risk?: number | null) => string;
}
const DisplayContext = createContext<DisplayCtx | null>(null);
export function useDisplay(): DisplayCtx {
  const ctx = useContext(DisplayContext);
  if (!ctx) throw new Error("useDisplay outside provider");
  return ctx;
}

function moneyText(v: number): string {
  const abs = Math.abs(v).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: Math.abs(v) < 100 ? 2 : 0,
  });
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}$${abs}`;
}

function DisplayProvider({ children }: { children: ReactNode }) {
  const { accountEpoch, authed } = useAuth();
  const [mode, setModeState] = useState<DisplayMode>(() => {
    const stored = localStorage.getItem("pt:display-mode");
    return (["$", "R", "%acct", "privacy"] as DisplayMode[]).includes(stored as DisplayMode)
      ? (stored as DisplayMode)
      : "$";
  });
  const [equity, setEquity] = useState<number | null>(null);

  const setMode = useCallback((m: DisplayMode) => {
    setModeState(m);
    localStorage.setItem("pt:display-mode", m);
  }, []);

  useEffect(() => {
    if (!authed) return;
    api<{ curve: { portfolio_value: number | null }[] }>("/journal/equity")
      .then((r) => {
        const last = [...(r.curve || [])].reverse().find((p) => (p.portfolio_value || 0) > 0);
        setEquity(last ? Number(last.portfolio_value) : null);
      })
      .catch(() => setEquity(null));
  }, [authed, accountEpoch]);

  const fmtPnl = useCallback(
    (v: number | null | undefined, risk?: number | null): string => {
      if (v == null || isNaN(Number(v))) return "—";
      const n = Number(v);
      if (mode === "privacy") return n > 0 ? "+•••" : n < 0 ? "−•••" : "•••";
      if (mode === "R" && risk != null && risk > 0) {
        const r = n / risk;
        return `${r > 0 ? "+" : ""}${r.toFixed(2)}R`;
      }
      if (mode === "%acct" && equity && equity > 0) {
        const p = (n / equity) * 100;
        return `${p > 0 ? "+" : ""}${p.toFixed(2)}%`;
      }
      return moneyText(n);
    },
    [mode, equity],
  );

  const value = useMemo(() => ({ mode, setMode, equity, fmtPnl }), [mode, setMode, equity, fmtPnl]);
  return <DisplayContext.Provider value={value}>{children}</DisplayContext.Provider>;
}

/* ---------------- global analytics filter ---------------- */

export type RangePreset = "7D" | "30D" | "90D" | "YTD" | "ALL";

export interface Filters {
  edgeId: number | null;
  tagId: number | null;
  underlying: string;
  direction: string;
  range: RangePreset;
}

interface FilterCtx {
  filters: Filters;
  setFilters: (f: Partial<Filters>) => void;
  /** query string ("?a=b" or "") the journal endpoints accept */
  qs: string;
  active: boolean;
}
const FilterContext = createContext<FilterCtx | null>(null);
export function useFilters(): FilterCtx {
  const ctx = useContext(FilterContext);
  if (!ctx) throw new Error("useFilters outside provider");
  return ctx;
}

function rangeToDates(range: RangePreset): { date_from?: string } {
  if (range === "ALL") return {};
  const now = new Date();
  const from = new Date(now);
  if (range === "YTD") {
    from.setMonth(0, 1);
  } else {
    const days = range === "7D" ? 7 : range === "30D" ? 30 : 90;
    from.setDate(from.getDate() - days);
  }
  return { date_from: from.toISOString().slice(0, 10) };
}

function FilterProvider({ children }: { children: ReactNode }) {
  const [filters, setF] = useState<Filters>({
    edgeId: null,
    tagId: null,
    underlying: "",
    direction: "",
    range: "ALL",
  });
  const setFilters = useCallback((f: Partial<Filters>) => setF((cur) => ({ ...cur, ...f })), []);
  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (filters.edgeId != null) p.set("edge_id", String(filters.edgeId));
    if (filters.tagId != null) p.set("tag_id", String(filters.tagId));
    if (filters.underlying.trim()) p.set("underlying", filters.underlying.trim().toUpperCase());
    if (filters.direction) p.set("direction", filters.direction);
    const d = rangeToDates(filters.range);
    if (d.date_from) p.set("date_from", d.date_from);
    const s = p.toString();
    return s ? "?" + s : "";
  }, [filters]);
  const active =
    filters.edgeId != null ||
    filters.tagId != null ||
    !!filters.underlying.trim() ||
    !!filters.direction ||
    filters.range !== "ALL";
  const value = useMemo(() => ({ filters, setFilters, qs, active }), [filters, setFilters, qs, active]);
  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>;
}

/* ---------------- combined ---------------- */

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <AuthProvider>
        <DisplayProvider>
          <FilterProvider>{children}</FilterProvider>
        </DisplayProvider>
      </AuthProvider>
    </ToastProvider>
  );
}
