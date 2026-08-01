/* API contract types — mirrors app/routers/journal.py + auth.py responses. */

export interface Account {
  id: number;
  user_id: number;
  name: string;
  description?: string | null;
  is_default: boolean;
  created_at?: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: { id: number; username: string; email?: string };
  accounts: Account[];
  active_account_id?: number | null;
}

export interface Fill {
  id: number;
  external_id?: string;
  order_id?: string;
  underlying: string;
  option_symbol?: string;
  option_type?: string;
  strike?: number | string;
  expiration?: string;
  action: string;
  is_opening: number | boolean;
  quantity: number;
  price: number;
  value?: number;
  executed_at: string;
  trade_date?: string;
}

export interface SyncResult {
  synced: number;
  skipped: number;
  trades_created: number;
  fills_attached: number;
  unmatched: number;
  dismissed_pre_epoch: number;
  trades_expired: number;
}

export interface TagT {
  id: number;
  name: string;
  /** #rrggbb, chosen at creation */
  color: string;
}

export interface Edge {
  id: number;
  name: string;
  criteria?: string | null;
  regime?: string | null;
  status: "developing" | "validated" | "retired" | string;
  notes?: string | null;
}

/** Per-structure playbook, auto-resolved onto trades via `direction`. */
export interface Mechanism {
  id: number;
  structure: string;
  name: string;
  rules?: string | null;
  notes?: string | null;
  trade_count?: number;
}

export interface Trade {
  id: number;
  underlying?: string;
  direction?: string;
  strikes?: string;
  expiration?: string;
  dte_at_entry?: number | null;
  is_0dte?: number | boolean;
  entry_premium?: number | null;
  exit_premium?: number | null;
  quantity?: number | null;
  fees_total?: number | null;
  entry_at?: string | null;
  exit_at?: string | null;
  realized_pnl?: number | null;
  realized_pnl_pct?: number | null;
  status: "open" | "closed" | string;
  edge_id?: number | null;
  edge_name?: string | null;
  /** auto-resolved structure playbook (from direction) — never set by hand */
  mechanism_id?: number | null;
  mechanism_name?: string | null;
  is_system?: number | boolean | null;
  conviction?: number | null;
  why_entered?: string | null;
  thesis_worked?: string | null;
  exit_reason?: string | null;
  emotional_state?: string | null;
  reflection?: string | null;
  regime_read?: string | null;
  planned_risk?: number | null;
  /** CSP sold wanting the shares — assignment is a fill, not a loss */
  assignment_intent?: number | null;
  /** explicitly declared edge-less (a one-off) — distinct from edge unset */
  no_edge?: number | null;
  tags?: TagT[];
  /** rule-based insight badges (server-computed, /logbook only) */
  insights?: { key: string; label: string; tone: "good" | "warn" | "bad" }[];
}

export interface GreekSnapshot {
  id: number;
  snapshot_type?: string;
  captured_at?: string;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  implied_volatility?: number | null;
  underlying_price?: number | null;
  [k: string]: unknown;
}

export interface TradeDetail extends Trade {
  fills: Fill[];
  greeks: GreekSnapshot[];
  tags: TagT[];
  /** width − credit for defined-risk shapes (credit spreads/condors); null otherwise */
  defined_risk?: number | null;
  /** 2× the credit collected for short-premium shapes; null for debit/custom */
  two_x_credit?: number | null;
}

export interface PositionLeg {
  option_symbol: string;
  net: number;
  mark: number | null;
  delta: number | null;
  iv: number | null;
  theta: number | null;
  as_of: string | null;
}

export interface Position extends Trade {
  legs: PositionLeg[];
  cash_flow: number;
  liquidation_value: number | null;
  unrealized_pnl: number | null;
  marks_missing: string[];
  /** progress toward the 50% profit target on credit trades; null otherwise */
  pct_of_credit: number | null;
  /** net greeks of the open legs (× contracts × 100); null until marks exist */
  position_delta: number | null;
  position_theta: number | null;
  days_open: number | null;
  /** defined-risk shapes only (credit spreads / condors); null for naked legs */
  max_loss: number | null;
  return_on_risk: number | null;
  /** latest underlying spot from the greek poller; null before first poll */
  spot: number | null;
  /** short strike ∓ credit/share; two entries for two-sided shapes */
  breakevens: number[] | null;
  /** % move in spot to the nearest short strike; negative once breached */
  cushion_pct: number | null;
  moneyness: "OTM" | "ATM" | "ITM" | null;
  /** cash to buy the shares if every short put assigns (strike × 100 × contracts) */
  assignment_capital: number | null;
  /** unrealized change since the prior session's last mark */
  day_pnl: number | null;
  /** stored desk-consult messages on this trade (0 = no conversation yet) */
  consult_turns?: number;
}

export interface ForwardCalendar {
  today: string;
  expirations: Record<string, { id: number; underlying: string | null; direction: string | null; strikes: string | null }[]>;
  earnings: Record<string, { symbol: string; held: boolean }[]>;
  dte_window: { start: string; end: string };
}

export interface PositionsSummary {
  count: number;
  total_day_pnl: number | null;
  day_covered: number;
  total_credit: number;
  total_liquidation: number | null;
  total_unrealized: number | null;
  marked_count: number;
  net_delta: number | null;
  net_theta: number | null;
  greeks_covered: number;
  defined_risk_total: number | null;
  defined_risk_count: number;
}

export interface OverallStats {
  count: number;
  total_pnl: number;
  avg_pnl: number | null;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  payoff_ratio: number | null;
  profit_factor: number | null;
  expectancy: number | null;
}

export interface RowStats extends OverallStats {
  key?: string;
  tag?: string;
  color?: string | null;
  edge_name?: string;
  mechanism_name?: string;
}

export interface RMultiples {
  count: number;
  coverage_pct: number;
  avg_r: number | null;
  best_r: number | null;
  worst_r: number | null;
  total_r: number | null;
}

export interface Analytics {
  overall: OverallStats;
  r_multiples: RMultiples;
  by_tag: RowStats[];
  by_edge: RowStats[];
  by_mechanism: RowStats[];
  system_vs_discretionary: { system: OverallStats; discretionary: OverallStats };
  by_instrument: RowStats[];
  by_regime: RowStats[];
  discipline: {
    current_streak: number;
    longest_streak: number;
    /** consecutive most-recent closed trades with a complete journal record */
    journal_streak: number;
    dissonance: { won_but_off_plan: number; lost_but_well_executed: number };
  };
}

export interface CalendarDay {
  date: string;
  pnl: number;
  trades: number;
  wins: number;
  /** day's summed R-multiples over trades that declared planned risk (null if none) */
  r?: number | null;
  has_note: boolean;
  has_review: boolean;
}

export interface Score {
  components: {
    win_rate: number;
    payoff: number;
    profit_factor: number;
    drawdown: number;
    consistency: number;
    recovery: number;
    [k: string]: number;
  };
  score: number;
  trades: number;
  max_drawdown: number | null;
}

export interface LogbookDay {
  date: string;
  trades: Trade[];
  stats: OverallStats;
  message: string;
  note?: string | null;
  ai_feedback?: string | null;
}

export interface Motd {
  trades_today: number;
  system: number;
  discretionary: number;
  avg_conviction?: number | null;
  realized_pnl?: number | null;
  wins?: number;
  current_streak: number;
  won_but_off_plan?: number;
  lost_but_well_executed?: number;
  headline: string;
  date: string;
}

export interface EquityPoint {
  snapshot_date: string;
  portfolio_value: number | null;
  spy_price: number | null;
  cash_balance: number | null;
}

export interface JournalSettings {
  tt_account_number?: string | null;
  sync_from_date?: string | null;
}

export interface TTAccount {
  number: string;
  [k: string]: unknown;
}

/* ---- signals feed (watchtower + strategy #1 + pulse) ---- */

export interface WatchtowerPut {
  expiry: string;
  dte: number;
  strike: number;
  bid?: number | null;
  ask?: number | null;
  mid: number;
  prem_pct: number;
  otm_pct: number;
  iv?: number | null;
  oi?: number | null;
}

export interface WatchtowerCard {
  date: string;
  source: "core" | "discovery" | string;
  symbol: string;
  tier: "STRONG" | "MARGINAL" | string;
  alert_class: "TRADE" | "WATCH" | string;
  spot?: number | null;
  rsi?: number | null;
  floor?: number | null;
  fair?: number | null;
  breach?: number | null;
  zone_lo?: number | null;
  zone_hi?: number | null;
  zone_touches?: number | null;
  earnings?: string | null;
  put?: WatchtowerPut | null;
  taken_trade_id: number | null;
}

export interface S1Signal {
  ticker: string;
  signal: "SELL" | "FLAT" | string;
  tier?: string | null;
  quality?: string | null;
  rv20?: number | null;
  calm_cutoff?: number | null;
  depth?: number | null;
  gap_ratio?: number | null;
  fatgap_cutoff?: number | null;
  fat?: boolean | null;
  calm?: boolean | null;
  iv?: number | null;
  spot?: number | null;
  [k: string]: unknown;
}

export interface PulseIndex {
  close?: number;
  d1?: number;
  d5?: number;
  d20?: number;
  from_52w_hi?: number;
}

export interface Pulse {
  date: string;
  indexes?: Record<string, PulseIndex>;
  vix?: { close?: number; chg_1d?: number; pctile_1y?: number };
  sectors_1d?: Record<string, number>;
  universe?: {
    n?: number;
    pct_down_1d?: number;
    median_1d?: number;
    pct_above_50dma?: number;
    washouts_rsi35?: string[];
  };
  tlt?: { d1?: number };
  gld?: { d1?: number };
  strategy1_signal?: Record<string, string>;
  [k: string]: unknown;
}

export interface SignalsFeed {
  watchtower: WatchtowerCard[];
  strategy1: ({ date: string; signals: S1Signal[] } & Record<string, unknown>) | null;
  pulse: Pulse | null;
}

/* ---------------- Morning digest ---------------- */

export interface AttentionItem {
  trade_id: number;
  underlying: string;
  direction: string | null;
  strikes: string | null;
  expiration: string | null;
  dte: number | null;
  moneyness: string | null;
  pct_of_credit: number | null;
  unrealized_pnl: number | null;
  day_pnl: number | null;
  earnings: string | null;
  /** priority-ordered: threatened / expiring / pt_hit / earnings / clock */
  reasons: string[];
}

export interface LiveQuote {
  last: number | null;
  prev_close: number | null;
  d1_pct: number | null;
  as_of: string | null;
}

export interface SentimentComponent {
  key: string;
  label: string;
  value: number | string | null;
  read: string;
  detail: string;
  score: number;
}

/** GET /journal/digest/live — the Refresh button's live layer. */
export interface LiveMarket {
  as_of: string;
  session: "pre-market" | "regular" | "after-hours" | "closed";
  quotes: Record<string, LiveQuote>;
  sentiment: { score: number | null; label: string; components: SentimentComponent[] };
}

export interface Digest {
  date: string;
  market: Pulse | null;
  strategy1: SignalsFeed["strategy1"];
  watchtower: WatchtowerCard[];
  book: PositionsSummary;
  attention: AttentionItem[];
  calendar: {
    horizon_days: number;
    expirations: Record<string, { id: number; underlying: string; direction: string | null; strikes: string | null }[]>;
    earnings: Record<string, { symbol: string; held: boolean }[]>;
    dte_window: { start: string; end: string };
  };
  research_flags: { symbol: string; date: string; flags: string[] }[];
  intents: TradeIntent[];
  debt: { count: number; items: JournalDebtItem[] };
}

/* ---------------- Research (watchlist) ---------------- */

export interface ResearchSnapshot {
  date: string;
  price: number | null;
  rsi14: number | null;
  rv20: number | null;
  atm_iv: number | null;
  iv_rv: number | null;
  support_lo: number | null;
  support_hi: number | null;
  support_dist_pct: number | null;
  support_touches: number | null;
  earnings: string | null;
  flags: string[];
  chg_1d_pct?: number | null;
}

export interface ResearchHistoryPoint {
  date: string;
  price: number | null;
  rsi14: number | null;
  iv_rv: number | null;
  support_dist_pct: number | null;
}

export interface ResearchCard {
  id: number;
  symbol: string;
  thesis: string | null;
  assignment_ok: number | null;
  created_at: string;
  latest: ResearchSnapshot | null;
  history: ResearchHistoryPoint[];
  journal_trades: number;
  open_positions: number;
  last_ai: { kind: string; created_at: string } | null;
  /** latest nightly AI daily card, if one has been written */
  last_daily: { content: string; created_at: string } | null;
}

export interface ResearchBrief {
  id: number;
  kind: "brief" | "consult" | "daily";
  content: string;
  created_at: string;
}

export interface StrangleConstruction {
  label: string;
  put_strike: number;
  put_delta: number;
  put_mid: number | null;
  call_strike: number;
  call_delta: number;
  call_mid: number | null;
  credit: number;
  be_low: number;
  be_high: number;
}

export interface StrangleRow {
  symbol: string;
  error?: string;
  spot?: number;
  vol_index?: string;
  iv?: number | null;
  ivr?: number | null;
  rv20?: number | null;
  vrp?: number | null;
  vrp_z?: number | null;
  gap_ratio?: number | null;
  rich?: boolean;
  expiry?: string | null;
  dte?: number | null;
  expected_move?: number | null;
  constructions?: StrangleConstruction[];
}

export interface VolSnapshot {
  date: string;
  spot: number | null;
  iv: number | null;
  rv20: number | null;
  iv_rv: number | null;
  term_ratio: number | null;
  straddle_pct: number | null;
  earnings: string | null;
}

export interface VolWatchRow {
  symbol: string;
  error?: string;
  spot?: number;
  rv20?: number | null;
  iv?: number | null;
  iv_front?: number | null;
  iv_back?: number | null;
  front_expiry?: string | null;
  front_dte?: number | null;
  iv_rv?: number | null;
  term_ratio?: number | null;
  straddle_pct?: number | null;
  earnings?: string | null;
  ivr?: number | null;
  ivr_source?: string;
  ivr_n?: number;
  history: VolSnapshot[];
}

export interface JournalDebtItem {
  id: number;
  underlying: string | null;
  direction: string | null;
  strikes: string | null;
  status: string;
  entry_at: string | null;
  missing: string[];
}

export interface TradeIntent {
  id: number;
  underlying: string;
  direction: string | null;
  edge_id: number | null;
  edge_name?: string | null;
  is_system: number | null;
  planned_risk: number | null;
  conviction: number | null;
  note: string | null;
  status: string;
  created_at: string;
}

export interface ResearchNoteRow {
  id: number;
  note: string;
  created_at: string;
}

/* ---------------- DD (deep-dive) panel ---------------- */

export interface DDZone {
  kind: "support" | "resistance";
  lo: number;
  hi: number;
  touches: number;
  strength: number;
  last_touch: string;
  volume_node: boolean;
  round_number: number | null;
  distance_pct: number | null;
}

export interface DDPoint {
  d: string;
  c: number | null;
  sma50: number | null;
  sma200: number | null;
}

export interface DD {
  symbol: string;
  price: number;
  as_of: string;
  stats: {
    rsi14: number | null;
    ret_5d: number | null;
    ret_20d: number | null;
    lo52: number | null;
    hi52: number | null;
    pct_from_52w_low: number | null;
    pct_from_52w_high: number | null;
    atr14: number | null;
    atr_pct: number | null;
    rv20: number | null;
    atm_iv: number | null;
    iv_dte: number | null;
    iv_rv_ratio: number | null;
    sma50: number | null;
    sma200: number | null;
    above_sma200: boolean | null;
    earnings: string | null;
  };
  supports: DDZone[];
  resistances: DDZone[];
  /** trading-day lookback find_zones used — label it so the chart timeframe
      is never mistaken for the zone window */
  zone_lookback_days: number;
  series: DDPoint[];
}

export interface ApiKey {
  id: number;
  name: string;
  created_at?: string;
  last_used_at?: string | null;
  expires_at?: string | null;
  is_active: number | boolean;
}
