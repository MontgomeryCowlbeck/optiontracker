from pydantic import BaseModel, Field, field_validator
from datetime import date, datetime
from typing import Optional, Literal
from enum import Enum


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class TradeAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    ASSIGNED = "assigned"
    EXPIRED = "expired"


class Moneyness(str, Enum):
    ITM = "ITM"
    ATM = "ATM"
    OTM = "OTM"
    UNKNOWN = "Unknown"


class TradeGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class StrategyType(str, Enum):
    VERTICAL_SPREAD = "vertical_spread"
    IRON_CONDOR = "iron_condor"
    STRANGLE = "strangle"
    STRADDLE = "straddle"
    CALENDAR_SPREAD = "calendar_spread"
    DIAGONAL = "diagonal"
    NAKED_PUT = "naked_put"
    NAKED_CALL = "naked_call"
    COVERED_CALL = "covered_call"
    CUSTOM = "custom"


class AttachmentType(str, Enum):
    ENTRY_CHART = "entry_chart"
    EXIT_CHART = "exit_chart"
    OTHER = "other"


def generate_occ_symbol(ticker: str, expiration: date, option_type: str, strike: float) -> str:
    """Generate OCC option symbol.

    Format: {ROOT}{YYMMDD}{C/P}{STRIKE*1000 padded to 8 digits}
    Example: AAPL $150 Call expiring Jan 19, 2024 = AAPL240119C00150000
    """
    exp_str = expiration.strftime("%y%m%d")
    type_char = "C" if option_type.lower() == "call" else "P"
    strike_int = int(strike * 1000)
    return f"{ticker.upper()}{exp_str}{type_char}{strike_int:08d}"


# Trade Models
class TradeBase(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    strike: float = Field(..., gt=0)
    expiration: date
    option_type: OptionType
    action: TradeAction
    quantity: int = Field(..., gt=0)
    price: float  # Premium per contract at entry (STO credit or BTO debit)
    trade_date: date
    commission: float = Field(default=0, ge=0)
    notes: Optional[str] = None
    profit_target: Optional[int] = Field(default=None, ge=1, le=100)  # Percentage (e.g., 50 = 50%)

    # Journaling fields
    pre_trade_notes: Optional[str] = None  # Trade thesis at entry
    post_trade_notes: Optional[str] = None  # Lessons learned at close
    trade_grade: Optional[TradeGrade] = None  # A/B/C/D/F rating

    # Entry Greeks (user input)
    entry_delta: Optional[float] = Field(default=None, ge=-1, le=1)
    entry_theta: Optional[float] = None
    entry_vega: Optional[float] = None
    entry_gamma: Optional[float] = None

    # IV Analysis
    entry_iv: Optional[float] = Field(default=None, ge=0)  # IV % at entry
    entry_iv_rank: Optional[float] = Field(default=None, ge=0, le=100)  # IV rank 0-100
    entry_iv_percentile: Optional[float] = Field(default=None, ge=0, le=100)  # IV percentile 0-100

    # Position Sizing
    position_size_pct: Optional[float] = Field(default=None, ge=0, le=100)  # % of portfolio

    # Planned Exit
    planned_exit_price: Optional[float] = Field(default=None, ge=0)  # Target exit price
    planned_exit_dte: Optional[int] = Field(default=None, ge=0)  # Target DTE to exit

    # Multi-Leg Strategy
    strategy_group_id: Optional[int] = None  # FK to strategy_groups
    leg_number: Optional[int] = Field(default=None, ge=1)  # Leg order in strategy

    @field_validator('ticker')
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator('profit_target', 'planned_exit_dte', 'strategy_group_id', 'leg_number', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        """Convert empty strings to None for optional int fields (SQLite quirk)."""
        if v == '' or v == 'None':
            return None
        return v


class TradeCreate(TradeBase):
    pass


class TradeUpdate(BaseModel):
    ticker: Optional[str] = None
    strike: Optional[float] = None
    expiration: Optional[date] = None
    option_type: Optional[OptionType] = None
    action: Optional[TradeAction] = None
    quantity: Optional[int] = None
    price: Optional[float] = None
    trade_date: Optional[date] = None
    commission: Optional[float] = None
    notes: Optional[str] = None
    closed_date: Optional[date] = None
    closed_price: Optional[float] = None
    profit_target: Optional[int] = None
    # Journaling fields
    pre_trade_notes: Optional[str] = None
    post_trade_notes: Optional[str] = None
    trade_grade: Optional[TradeGrade] = None
    # Entry Greeks
    entry_delta: Optional[float] = None
    entry_theta: Optional[float] = None
    entry_vega: Optional[float] = None
    entry_gamma: Optional[float] = None
    # IV Analysis
    entry_iv: Optional[float] = None
    entry_iv_rank: Optional[float] = None
    entry_iv_percentile: Optional[float] = None
    # Position Sizing
    position_size_pct: Optional[float] = None
    # Planned Exit
    planned_exit_price: Optional[float] = None
    planned_exit_dte: Optional[int] = None
    # Multi-Leg Strategy
    strategy_group_id: Optional[int] = None
    leg_number: Optional[int] = None


class TradeClose(BaseModel):
    closed_date: date
    closed_price: float = Field(..., ge=0)
    close_quantity: Optional[int] = Field(None, ge=1)  # Contracts to close; None = close all
    post_trade_notes: Optional[str] = None  # Lessons learned
    trade_grade: Optional[TradeGrade] = None  # A/B/C/D/F rating


class Trade(TradeBase):
    id: int
    occ_symbol: str
    closed_date: Optional[date] = None
    closed_price: Optional[float] = None
    created_at: datetime
    status: TradeStatus = TradeStatus.OPEN
    underlying_price: Optional[float] = None
    profit_target: Optional[int] = None  # Override from TradeBase to allow None from DB
    sync_source: str = "manual"
    external_id: Optional[str] = None

    @field_validator('profit_target', 'planned_exit_dte', 'strategy_group_id', 'leg_number', mode='before')
    @classmethod
    def empty_string_to_none_trade(cls, v):
        """Convert empty strings to None for optional int fields (SQLite quirk)."""
        if v == '' or v == 'None':
            return None
        return v

    class Config:
        from_attributes = True


class TradeWithCurrentPrice(Trade):
    current_bid: Optional[float] = None
    current_ask: Optional[float] = None
    current_last: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    capital_deployed: Optional[float] = None
    # Current Greeks
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None
    implied_volatility: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    last_updated: Optional[datetime] = None
    # Computed fields
    dte: Optional[int] = None  # Days to expiration
    moneyness: Optional[str] = None  # ITM, ATM, OTM
    strategy_name: Optional[str] = None
    # Profit target tracking
    profit_pct: Optional[float] = None  # Current profit as percentage
    target_hit: Optional[bool] = None  # True if profit_target is set and achieved
    # Intrinsic/Extrinsic value breakdown
    intrinsic_value: Optional[float] = None  # Per share intrinsic value
    extrinsic_value: Optional[float] = None  # Per share extrinsic (time + vol) value
    intrinsic_total: Optional[float] = None  # Total position intrinsic value
    extrinsic_total: Optional[float] = None  # Total position extrinsic value
    extrinsic_pct: Optional[float] = None  # Percentage of current price that is extrinsic
    # Day P&L
    day_pnl: Optional[float] = None  # P&L change since previous trading day's close


# Price Snapshot Models
class PriceSnapshotBase(BaseModel):
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    underlying_price: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None
    # Greeks
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None
    implied_volatility: Optional[float] = None


class PriceSnapshotCreate(PriceSnapshotBase):
    trade_id: int


class PriceSnapshot(PriceSnapshotBase):
    id: int
    trade_id: int
    timestamp: datetime

    class Config:
        from_attributes = True


# Dashboard Models
class DashboardStats(BaseModel):
    # Cash
    cash_balance: float = 0
    # Premium stats
    total_premium_collected: float = 0
    total_premium_paid: float = 0
    net_premium: float = 0
    # Portfolio stats
    total_capital_deployed: float = 0
    total_portfolio_value: float = 0  # cash + stock value + options value
    # Trade counts
    open_trades_count: int = 0
    closed_trades_count: int = 0
    # P&L
    realized_pnl: float = 0
    unrealized_pnl: float = 0
    # Win/loss
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0


# API Response Models
class TradeListResponse(BaseModel):
    trades: list[TradeWithCurrentPrice]
    total: int


class PriceHistoryResponse(BaseModel):
    trade_id: int
    snapshots: list[PriceSnapshot]


# Analytics Models
class PremiumByTicker(BaseModel):
    ticker: str
    premium: float
    realized_pnl: float = 0.0
    trade_count: int


class PremiumByMonth(BaseModel):
    month: str
    premium_collected: float
    premium_paid: float
    net_premium: float


class PLByMonth(BaseModel):
    month: str
    realized_pnl: float
    unrealized_pnl: float


class CumulativePL(BaseModel):
    date: str
    cumulative_pnl: float


class StrategyBreakdown(BaseModel):
    strategy: str
    count: int
    premium: float
    pnl: float


class AnalyticsResponse(BaseModel):
    premium_by_ticker: list[PremiumByTicker]
    premium_by_month: list[PremiumByMonth]
    pnl_by_month: list[PLByMonth]
    cumulative_pnl: list[CumulativePL]
    strategy_breakdown: list[StrategyBreakdown]
    calls_stats: dict
    puts_stats: dict


class TickerPnL(BaseModel):
    """Combined options and stock P&L for a single ticker."""
    ticker: str
    options_premium_collected: float  # From sold options
    options_premium_paid: float  # From bought options
    options_net_premium: float  # collected - paid
    options_realized_pnl: float  # From closed trades
    options_unrealized_pnl: float  # From open trades
    options_total_pnl: float  # realized + unrealized
    stock_realized_pnl: float  # From sold stock positions
    stock_unrealized_pnl: float  # From open stock positions
    stock_total_pnl: float  # realized + unrealized stock P&L
    stock_cost_basis: float  # Total cost of stock held
    stock_market_value: float  # Current market value of stock held
    total_pnl: float  # Combined options + stock P&L
    options_trade_count: int  # Number of options trades
    open_options_count: int = 0  # Currently open/assigned option trades
    stock_shares_held: int  # Current shares owned


# Prospect Models (Trade Watchlist)
class ProspectBase(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    strike: float = Field(..., gt=0)
    expiration: date
    option_type: OptionType
    action: TradeAction
    quantity: int = Field(default=1, gt=0)
    target_entry_price: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None

    @field_validator('ticker')
    @classmethod
    def uppercase_ticker(cls, v):
        return v.upper() if v else v


class ProspectCreate(ProspectBase):
    pass


class Prospect(ProspectBase):
    id: int
    occ_symbol: str
    created_at: datetime

    class Config:
        from_attributes = True


class ProspectWithQuote(Prospect):
    """Prospect with live quote data."""
    current_bid: Optional[float] = None
    current_ask: Optional[float] = None
    current_last: Optional[float] = None
    underlying_price: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    dte: Optional[int] = None
    moneyness: Optional[str] = None
    target_hit: Optional[bool] = None  # True if current price <= target_entry_price


class ProspectExecute(BaseModel):
    """Data needed to convert a prospect to a trade."""
    quantity: int = Field(..., gt=0)
    price: float = Field(..., ge=0)
    trade_date: Optional[date] = None
    commission: float = Field(default=0, ge=0)
    notes: Optional[str] = None


# Multi-leg Prospect Strategy Models
class ProspectStrategyGroup(BaseModel):
    """Prospect strategy group model."""
    id: int
    name: Optional[str] = None
    strategy_type: str
    underlying_ticker: str
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ProspectStrategyGroupWithLegs(ProspectStrategyGroup):
    """Prospect strategy group with associated legs."""
    legs: list = []  # List of Prospect objects
    total_target_premium: float = 0


class MultiLegProspectLeg(BaseModel):
    """Single leg in a multi-leg prospect strategy."""
    leg_number: int = Field(..., ge=1, le=4)
    action: TradeAction
    option_type: OptionType
    strike: float = Field(..., gt=0)
    expiration: date
    quantity: int = Field(default=1, ge=1)
    target_entry_price: Optional[float] = Field(default=None, ge=0)


class MultiLegProspectCreate(BaseModel):
    """Create a multi-leg prospect strategy."""
    ticker: str
    strategy_type: str
    strategy_name: Optional[str] = None
    notes: Optional[str] = None
    legs: list[MultiLegProspectLeg] = Field(..., min_length=2, max_length=4)

    @field_validator('ticker')
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator('strategy_type')
    @classmethod
    def validate_strategy_type(cls, v: str) -> str:
        valid_types = ['vertical_spread', 'iron_condor', 'strangle', 'straddle',
                       'calendar_spread', 'diagonal', 'naked_put', 'naked_call',
                       'covered_call', 'custom']
        if v not in valid_types:
            raise ValueError(f"Invalid strategy type. Must be one of: {', '.join(valid_types)}")
        return v


class MultiLegProspectResponse(BaseModel):
    """Response for multi-leg prospect strategy creation."""
    strategy_group_id: int
    prospect_ids: list[int]
    strategy_type: str
    ticker: str
    net_target_premium: float
    leg_count: int


# Stock Position Models
class StockPositionBase(BaseModel):
    ticker: str
    shares: int
    cost_basis: float
    acquired_date: date
    notes: Optional[str] = None


class StockPositionCreate(StockPositionBase):
    acquired_from_trade_id: Optional[int] = None


class StockPosition(StockPositionBase):
    id: int
    acquired_from_trade_id: Optional[int] = None
    sold_date: Optional[date] = None
    sold_price: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StockPositionWithPnL(StockPosition):
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None


class StockLot(BaseModel):
    """Individual lot/acquisition of a stock position."""
    id: int
    shares: int
    cost_basis: float
    acquired_date: date
    acquired_from_trade_id: Optional[int] = None
    notes: Optional[str] = None
    sold_date: Optional[date] = None
    sold_price: Optional[float] = None

    class Config:
        from_attributes = True


class ConsolidatedStockPosition(BaseModel):
    """Consolidated view of all lots for a single ticker."""
    ticker: str
    total_shares: int
    avg_cost_basis: float
    total_cost: float
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    lots: list[StockLot] = []
    first_acquired: Optional[date] = None
    last_acquired: Optional[date] = None
    source: str = "local"  # "local" or "tastytrade"


# Assignment Models
class AssignmentRequest(BaseModel):
    """Request to mark a trade as assigned."""
    assignment_date: date
    shares_assigned: int = Field(..., gt=0)
    assignment_price: float = Field(..., ge=0)  # Price per share
    notes: Optional[str] = None


# Underlying Price Update
class UnderlyingPriceUpdate(BaseModel):
    """Manual underlying price update for a trade."""
    underlying_price: float = Field(..., gt=0)


# Cash Transaction Models
class CashTransactionType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    PREMIUM_RECEIVED = "premium_received"
    PREMIUM_PAID = "premium_paid"
    TRADE_CLOSE = "trade_close"
    ASSIGNMENT = "assignment"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    FEE = "fee"
    ADJUSTMENT = "adjustment"


class CashTransactionCreate(BaseModel):
    """Create a cash transaction (deposit/withdrawal)."""
    transaction_type: CashTransactionType
    amount: float = Field(..., description="Positive for inflows, negative for outflows")
    description: Optional[str] = None
    transaction_date: date


class CashTransaction(BaseModel):
    """Cash transaction record."""
    id: int
    transaction_type: CashTransactionType
    amount: float
    balance_after: float
    description: Optional[str] = None
    trade_id: Optional[int] = None
    transaction_date: date
    created_at: datetime

    class Config:
        from_attributes = True


class CashBalance(BaseModel):
    """Current cash balance summary."""
    balance: float
    total_deposits: float
    total_withdrawals: float
    total_premium_received: float
    total_premium_paid: float
    total_trade_pnl: float
    last_transaction_date: Optional[date] = None


class CashTransactionListResponse(BaseModel):
    """Response for listing cash transactions."""
    transactions: list[CashTransaction]
    total: int
    current_balance: float


# Extended Analytics Models
class WinRateByTicker(BaseModel):
    """Win rate statistics for a single ticker."""
    ticker: str
    wins: int
    losses: int
    total_trades: int
    win_rate: float
    total_pnl: float


class WinRateByDTE(BaseModel):
    """Win rate statistics by days-to-expiration range."""
    dte_range: str  # "0-7", "8-21", "22-45", "45+"
    wins: int
    losses: int
    total_trades: int
    win_rate: float
    avg_pnl: float


class HoldTimeAnalysis(BaseModel):
    """Analysis of trade hold times."""
    avg_hold_days_winners: Optional[float] = None
    avg_hold_days_losers: Optional[float] = None
    avg_hold_days_all: Optional[float] = None
    shortest_winner_days: Optional[int] = None
    longest_winner_days: Optional[int] = None
    shortest_loser_days: Optional[int] = None
    longest_loser_days: Optional[int] = None


class DayOfWeekPerformance(BaseModel):
    """Performance statistics by day of week."""
    day: str  # "Monday", "Tuesday", etc.
    day_index: int  # 0=Monday, 6=Sunday
    trades_opened: int
    trades_closed: int
    pnl_opened: float  # P&L from trades opened on this day
    pnl_closed: float  # P&L from trades closed on this day
    win_rate_closed: float  # Win rate of trades closed on this day


class AvgProfitByStrategy(BaseModel):
    """Average profit statistics per strategy."""
    strategy: str  # "Sell Put", "Buy Call", etc.
    trade_count: int
    total_pnl: float
    avg_pnl: float
    win_rate: float
    avg_premium: float


class TopTrade(BaseModel):
    """Top winning or losing trade."""
    trade_id: int
    ticker: str
    strike: float
    option_type: str
    action: str
    pnl: float
    pnl_pct: float
    trade_date: str
    closed_date: str


class WinLossStreak(BaseModel):
    """Win/loss streak information."""
    current_streak: int  # Positive for wins, negative for losses
    current_streak_type: str  # "win" or "loss"
    longest_win_streak: int
    longest_loss_streak: int
    last_10_results: list[str]  # ["W", "L", "W", ...]


class MoneynessPerformance(BaseModel):
    """Performance by moneyness at entry."""
    moneyness: str  # "ITM", "ATM", "OTM"
    trade_count: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class ExtendedAnalyticsResponse(BaseModel):
    """Response containing all extended analytics data."""
    win_rate_by_ticker: list[WinRateByTicker]
    win_rate_by_dte: list[WinRateByDTE]
    hold_time_analysis: HoldTimeAnalysis
    day_of_week_performance: list[DayOfWeekPerformance]
    avg_profit_by_strategy: list[AvgProfitByStrategy]
    top_winners: list[TopTrade]
    top_losers: list[TopTrade]
    streak_info: WinLossStreak
    moneyness_performance: list[MoneynessPerformance]


# ===== STRATEGY GROUP MODELS =====

class StrategyGroupCreate(BaseModel):
    """Create a new strategy group."""
    name: Optional[str] = None
    strategy_type: StrategyType
    underlying_ticker: str
    opened_date: date
    notes: Optional[str] = None

    @field_validator('underlying_ticker')
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper().strip()


class StrategyGroup(BaseModel):
    """Strategy group model."""
    id: int
    name: Optional[str] = None
    strategy_type: str
    underlying_ticker: str
    opened_date: date
    closed_date: Optional[date] = None
    notes: Optional[str] = None
    stop_loss: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StrategyGroupWithLegs(StrategyGroup):
    """Strategy group with associated trade legs."""
    legs: list = []  # List of Trade objects
    total_premium: float = 0
    realized_pnl: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    max_profit: Optional[float] = None
    max_loss: Optional[float] = None
    profit_pct: Optional[float] = None
    breakevens: list = []
    dte: Optional[int] = None
    expiration: Optional[str] = None
    leg_current_prices: dict = {}  # trade_id (str) -> current mid price
    underlying_price: Optional[float] = None
    short_leg_iv: Optional[float] = None  # IV of the short (sold) leg, as %
    iv_rank: Optional[float] = None       # IV rank of the underlying, 0-100
    total_theta: Optional[float] = None   # net position theta in $/day for open legs


class StrategyGroupUpdate(BaseModel):
    """Partial update for a strategy group."""
    stop_loss: Optional[float] = None
    notes: Optional[str] = None


class StrategyGroupClose(BaseModel):
    """Close all legs in a strategy group."""
    closed_date: date
    notes: Optional[str] = None


class MultiLegStrategyLeg(BaseModel):
    """Single leg in a multi-leg strategy."""
    leg_number: int = Field(..., ge=1, le=4)
    action: TradeAction
    option_type: OptionType
    strike: float = Field(..., gt=0)
    expiration: date
    quantity: int = Field(default=1, ge=1)
    price: float = Field(default=0, ge=0)


class MultiLegStrategyCreate(BaseModel):
    """Create a multi-leg strategy with all legs in one request."""
    ticker: str
    strategy_type: str  # Can be 'vertical_spread', 'iron_condor', etc.
    strategy_name: Optional[str] = None
    trade_date: date
    commission: float = Field(default=0, ge=0)
    notes: Optional[str] = None
    legs: list[MultiLegStrategyLeg] = Field(..., min_length=2, max_length=4)

    @field_validator('ticker')
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator('strategy_type')
    @classmethod
    def validate_strategy_type(cls, v: str) -> str:
        valid_types = ['vertical_spread', 'iron_condor', 'strangle', 'straddle',
                       'calendar_spread', 'diagonal', 'naked_put', 'naked_call',
                       'covered_call', 'custom']
        if v not in valid_types:
            raise ValueError(f"Invalid strategy type. Must be one of: {', '.join(valid_types)}")
        return v


class MultiLegStrategyResponse(BaseModel):
    """Response for multi-leg strategy creation."""
    strategy_group_id: int
    trade_ids: list[int]
    strategy_type: str
    ticker: str
    net_premium: float
    leg_count: int


# ===== TRADE ATTACHMENT MODELS =====

class TradeAttachmentCreate(BaseModel):
    """Create a trade attachment."""
    attachment_type: AttachmentType
    notes: Optional[str] = None


class TradeAttachment(BaseModel):
    """Trade attachment model."""
    id: int
    trade_id: int
    attachment_type: str
    filename: str
    file_path: str
    mime_type: Optional[str] = None
    uploaded_at: datetime
    notes: Optional[str] = None

    class Config:
        from_attributes = True


# ===== BENCHMARK MODELS =====

class BenchmarkSnapshotCreate(BaseModel):
    """Create a benchmark snapshot."""
    snapshot_date: date
    spy_price: float = Field(..., gt=0)
    portfolio_value: float
    cash_balance: float


class BenchmarkSnapshot(BaseModel):
    """Benchmark snapshot model."""
    id: int
    snapshot_date: date
    spy_price: float
    portfolio_value: float
    cash_balance: float
    created_at: datetime

    class Config:
        from_attributes = True


class EquityCurvePoint(BaseModel):
    """Single point on the equity curve."""
    date: str
    portfolio_value: float
    spy_value: float
    portfolio_return_pct: float
    spy_return_pct: float


class RollingReturn(BaseModel):
    """Rolling return statistics."""
    period: str  # "30d", "60d", "90d"
    portfolio_return: float
    spy_return: float
    alpha: float


class MaxDrawdown(BaseModel):
    """Maximum drawdown statistics."""
    max_drawdown_pct: float
    max_drawdown_amount: float
    peak_date: Optional[str] = None
    trough_date: Optional[str] = None
    recovery_date: Optional[str] = None
    current_drawdown_pct: float


class PerformanceBenchmark(BaseModel):
    """Complete performance benchmark data."""
    equity_curve: list[EquityCurvePoint]
    rolling_returns: list[RollingReturn]
    max_drawdown: MaxDrawdown
    total_return_pct: float
    spy_total_return_pct: float
    alpha: float
    sharpe_ratio: Optional[float] = None


# ===== IV ANALYSIS MODELS =====

class IVPerformanceAnalysis(BaseModel):
    """IV-based performance analysis."""
    high_iv_trades: int  # IV rank >= 50
    low_iv_trades: int  # IV rank < 50
    high_iv_win_rate: float
    low_iv_win_rate: float
    high_iv_avg_pnl: float
    low_iv_avg_pnl: float
    high_iv_total_pnl: float
    low_iv_total_pnl: float
    optimal_iv_range: Optional[str] = None  # e.g., "40-60"


# ===== STOCK WATCHLIST MODELS =====

class StockWatchlistItemCreate(BaseModel):
    """Create a stock watchlist item."""
    ticker: str = Field(..., min_length=1, max_length=10)
    notes: Optional[str] = None

    @field_validator('ticker')
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper().strip()


class StockWatchlistItem(BaseModel):
    """Stock watchlist item."""
    id: int
    account_id: int
    ticker: str
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StockWatchlistItemWithQuote(StockWatchlistItem):
    """Stock watchlist item with live quote data."""
    price: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[int] = None


# ===== API KEY MODELS =====

class APIKeyCreate(BaseModel):
    """Create an API key."""
    name: str = Field(..., min_length=1, max_length=100)
    expires_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    """API key response (without the key itself)."""
    id: int
    account_id: int
    name: str
    last_used_at: Optional[datetime] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    is_active: bool

    class Config:
        from_attributes = True


class APIKeyCreateResponse(APIKeyResponse):
    """API key response with the key (only returned on creation)."""
    key: str


class APIKeyListResponse(BaseModel):
    """List of API keys."""
    api_keys: list[APIKeyResponse]
    count: int


# ===== MAGIC MIRROR MODELS =====

class MagicMirrorWatchlistStock(BaseModel):
    """Stock in the Magic Mirror watchlist."""
    ticker: str
    price: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None


class MagicMirrorSummary(BaseModel):
    """Magic Mirror summary response."""
    open_trades_count: int
    weekly_pnl: float
    weekly_pnl_trades_count: int
    watchlist: list[MagicMirrorWatchlistStock]
    as_of: datetime


# ===== TRUE P&L ANALYTICS MODELS =====

class TickerTruePnL(BaseModel):
    """True P&L breakdown for a single ticker."""
    ticker: str
    premium_pnl: float  # Net realized premium from options
    stock_realized_pnl: float  # P&L from sold stock (using effective basis)
    stock_unrealized_pnl: float  # P&L on held stock (using effective basis)
    options_unrealized_pnl: float  # P&L on open options
    total_realized_pnl: float  # premium + stock_realized
    total_unrealized_pnl: float  # options_unrealized + stock_unrealized
    true_pnl: float  # Everything combined
    stock_shares_held: int
    stock_effective_basis: Optional[float] = None  # Effective cost basis per share
    stock_market_value: float


class MonthlyTruePnL(BaseModel):
    """Monthly True P&L breakdown."""
    month: str  # YYYY-MM
    premium_pnl: float
    stock_realized_pnl: float
    total_realized_pnl: float


class TruePnLResponse(BaseModel):
    """Complete True P&L analytics response."""
    # Summary totals
    premium_pnl: float  # Total realized option premium P&L
    stock_realized_pnl: float  # P&L from sold stock
    stock_unrealized_pnl: float  # P&L on held stock
    options_unrealized_pnl: float  # P&L on open options
    total_realized_pnl: float  # premium + stock_realized
    total_unrealized_pnl: float  # options_unrealized + stock_unrealized
    true_pnl: float  # Everything combined

    # Breakdowns
    by_ticker: list[TickerTruePnL]
    by_month: list[MonthlyTruePnL]


class WheelLegSummary(BaseModel):
    """Summary of a single leg in a wheel strategy."""
    trade_id: int
    leg_type: str  # 'csp', 'cc', 'assignment'
    premium: float
    status: str  # 'open', 'closed', 'assigned'
    trade_date: str
    closed_date: Optional[str] = None


class WheelSummary(BaseModel):
    """Summary of a complete wheel cycle."""
    wheel_id: int
    ticker: str
    start_date: str
    end_date: Optional[str] = None
    status: str  # 'active', 'completed'
    csp_premium: float  # Premium from cash-secured puts
    cc_premium: float  # Premium from covered calls
    total_premium: float
    effective_cost_basis: Optional[float] = None  # Adjusted cost basis on stock
    stock_pnl: Optional[float] = None  # Realized P&L when stock sold/called away
    total_pnl: float  # Everything combined
    legs_count: int


class WheelSummaryResponse(BaseModel):
    """Response for wheel analytics endpoint."""
    active_wheels: list[WheelSummary]
    completed_wheels: list[WheelSummary]
    total_wheel_pnl: float


# ---------------------------------------------------------------------------
# Tastytrade Sync Models
# ---------------------------------------------------------------------------

class SyncStatus(BaseModel):
    tastytrade_account_number: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    sync_status: str = "idle"
    last_sync_error: Optional[str] = None
    sync_from_date: Optional[date] = None
    auto_sync_enabled: bool = True
    tastytrade_configured: bool = False


class SyncConfig(BaseModel):
    tastytrade_account_number: Optional[str] = None
    sync_from_date: Optional[date] = None
    auto_sync_enabled: Optional[bool] = None


class SyncResult(BaseModel):
    trades_created: int = 0
    trades_closed: int = 0
    trades_expired: int = 0
    trades_assigned: int = 0
    stock_positions_created: int = 0
    cash_transactions_synced: int = 0
    duplicates_skipped: int = 0
    errors: list[str] = []
    duration_seconds: float = 0.0
