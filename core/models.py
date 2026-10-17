from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

AssetType = Literal["STOCK", "ETF"]

OrderType = Literal[
    "LIMIT_NORMAL",
    "MARKET_BREAKOUT",
    "EMERGENCY_FLUSH",
    "KILL_SWITCH",
    "SPLIT_BUY_1",
    "SPLIT_BUY_2",
    "SPLIT_BUY_3"
]

ExitReason = Literal[
    "STRATEGY_TAKE_PROFIT",
    "STRATEGY_STOP_LOSS",
    "TIME_EXIT_1515",
    "EMERGENCY_FLUSH_NETWORK_RECOVERY",
    "EMERGENCY_FLUSH_REBOOT",
    "MASTER_KILL_SWITCH",
    "MANUAL_EXIT"
]


class TickData(BaseModel):
    ticker: str
    price: float
    volume: int = 0
    bid_price: float  # 매수 1호가 (즉시 매도 체결 가능가)
    ask_price: float  # 매도 1호가 (즉시 매수 체결 가능가)
    exec_strength: float = 100.0  # 체결강도 (%)
    bid_ask_ratio: float = 1.0    # 호가 잔량비
    timestamp: datetime = Field(default_factory=datetime.now)


STOCK_NAME_MAP = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "000270": "기아",
    "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스",
    "068270": "셀트리온",
    "035420": "NAVER",
    "035720": "카카오",
    "005490": "POSCO홀딩스",
    "051910": "LG화학",
    "006400": "삼성SDI",
    "012450": "한화에어로스페이스",
    "042660": "한화오션",
    "010140": "삼성중공업",
    "267260": "HD현대일렉트릭",
    "028300": "HLB",
    "196170": "알테오젠",
    "086520": "에코프로",
    "247540": "에코프로비엠",
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "122630": "KODEX 레버리지",
    "233740": "KODEX 코스닥150레버리지",
    "114800": "KODEX 인버스",
    "252670": "KODEX 200선물인버스2X",
}


class Signal(BaseModel):
    slot_id: str
    ticker: str
    name: Optional[str] = ""
    asset_type: AssetType
    action: Literal["BUY", "SELL"]
    order_type: OrderType
    price: float
    quantity: int
    reason: str
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None


class SlotPosition(BaseModel):
    slot_id: str
    ticker: str
    name: Optional[str] = ""
    asset_type: AssetType
    quantity: int
    entry_price: float
    current_price: float
    stop_loss_price: float
    take_profit_price: Optional[float] = None
    profit_rate: float = 0.0
    entry_time: datetime = Field(default_factory=datetime.now)
    holding_days: int = 0


class SlotAccount(BaseModel):
    slot_id: str
    name: str
    initial_capital: float = 10_000_000.0
    cash_balance: float = 10_000_000.0
    stock_value: float = 0.0
    total_asset: float = 10_000_000.0
    total_profit: float = 0.0
    profit_rate: float = 0.0
    is_active: bool = True
    active_positions_count: int = 0


class TradeRecord(BaseModel):
    id: Optional[int] = None
    slot_id: str
    ticker: str
    name: Optional[str] = ""
    asset_type: AssetType
    quantity: int
    buy_price: float
    sell_price: float
    gross_pnl: float
    total_cost: float       # 거래세 + 위탁/유관기관 수수료
    net_pnl: float          # 세금·수수료 차감 후 순수익금
    profit_rate: float      # 순수익률 (%)
    entry_time: datetime
    exit_time: datetime = Field(default_factory=datetime.now)
    exit_reason: ExitReason


class DailySettlement(BaseModel):
    date: str  # YYYY-MM-DD
    total_asset: float
    cash_balance: float
    stock_value: float
    total_profit: float
    profit_rate: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trade_count: int = 0
    review_markdown: str = ""
    screening_json: str = "{}"
    created_at: Optional[datetime] = Field(default_factory=datetime.now)

