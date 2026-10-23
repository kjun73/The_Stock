import asyncio
import aiosqlite
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional
from core.config import settings
from core.executor import SqlPaperExecutor
from core.models import (
    STOCK_NAME_MAP,
    ExitReason,
    OrderType,
    Signal,
    SlotAccount,
    SlotPosition,
    TickData,
    TradeRecord,
    DailySettlement,
)

DEFAULT_SLOTS = [
    ("SLOT_A", "외인·기관 수급 스윙 (T+1)", 10_000_000.0),
    ("SLOT_B", "KODEX 현물 ETF 볼린저 그리드", 10_000_000.0),
    ("SLOT_C", "변동성 돌파 + JEV 단타", 10_000_000.0),
    ("SLOT_D", "전고점 돌파 첫 눌림목 스윙", 10_000_000.0),
]


class SqliteLedger:
    def __init__(self, db_path: str = settings.DB_PATH):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._lock: Optional[asyncio.Lock] = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get_connection(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL;")
            await self._db.execute("PRAGMA busy_timeout=5000;")
        return self._db

    async def close(self):
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def init_db(self):
        """데이터베이스 및 4개 슬롯 가상 원장 테이블 초기화"""
        async with self.lock:
            db = await self.get_connection()
            await db.execute("""
                CREATE TABLE IF NOT EXISTS slot_accounts (
                    slot_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    initial_capital REAL NOT NULL,
                    cash_balance REAL NOT NULL,
                    stock_value REAL DEFAULT 0.0,
                    total_asset REAL NOT NULL,
                    total_profit REAL DEFAULT 0.0,
                    profit_rate REAL DEFAULT 0.0,
                    is_active INTEGER DEFAULT 1,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS slot_positions (
                    slot_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    asset_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    stop_loss_price REAL NOT NULL,
                    take_profit_price REAL,
                    profit_rate REAL DEFAULT 0.0,
                    entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    holding_days INTEGER DEFAULT 0,
                    PRIMARY KEY (slot_id, ticker)
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS trade_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slot_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT DEFAULT '',
                    asset_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    buy_price REAL NOT NULL,
                    sell_price REAL NOT NULL,
                    gross_pnl REAL NOT NULL,
                    total_cost REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    profit_rate REAL NOT NULL,
                    entry_time TIMESTAMP NOT NULL,
                    exit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    exit_reason TEXT NOT NULL
                );
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS daily_settlements (
                    date TEXT PRIMARY KEY,
                    total_asset REAL NOT NULL,
                    cash_balance REAL NOT NULL,
                    stock_value REAL NOT NULL,
                    total_profit REAL NOT NULL,
                    profit_rate REAL NOT NULL,
                    realized_pnl REAL DEFAULT 0.0,
                    unrealized_pnl REAL DEFAULT 0.0,
                    trade_count INTEGER DEFAULT 0,
                    review_markdown TEXT DEFAULT '',
                    screening_json TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 기존 DB 스키마 하위 호환 마이그레이션
            for tbl in ["slot_positions", "trade_records"]:
                try:
                    await db.execute(f"ALTER TABLE {tbl} ADD COLUMN name TEXT DEFAULT ''")
                except Exception:
                    pass

            # 4대 슬롯 계좌 기본 레코드 등록 (없을 시에만)
            for slot_id, name, capital in DEFAULT_SLOTS:
                await db.execute("""
                    INSERT OR IGNORE INTO slot_accounts 
                    (slot_id, name, initial_capital, cash_balance, total_asset)
                    VALUES (?, ?, ?, ?, ?)
                """, (slot_id, name, capital, capital, capital))

            await db.commit()

    async def get_accounts(self) -> List[SlotAccount]:
        """전체 슬롯 계좌 현황 조회"""
        async with self.lock:
            db = await self.get_connection()
            cursor = await db.execute("SELECT * FROM slot_accounts")
            rows = await cursor.fetchall()
            accounts = []
            for r in rows:
                c_pos = await db.execute(
                    "SELECT COUNT(*) FROM slot_positions WHERE slot_id = ?",
                    (r["slot_id"],)
                )
                pos_count = (await c_pos.fetchone())[0]

                accounts.append(SlotAccount(
                    slot_id=r["slot_id"],
                    name=r["name"],
                    initial_capital=r["initial_capital"],
                    cash_balance=r["cash_balance"],
                    stock_value=r["stock_value"],
                    total_asset=r["total_asset"],
                    total_profit=r["total_profit"],
                    profit_rate=r["profit_rate"],
                    is_active=bool(r["is_active"]),
                    active_positions_count=pos_count
                ))
            return accounts

    async def get_slot_account(self, slot_id: str) -> Optional[SlotAccount]:
        """특정 슬롯 계좌 현황 단일 조회"""
        async with self.lock:
            db = await self.get_connection()
            cursor = await db.execute("SELECT * FROM slot_accounts WHERE slot_id = ?", (slot_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            c_pos = await db.execute(
                "SELECT COUNT(*) FROM slot_positions WHERE slot_id = ?",
                (slot_id,)
            )
            pos_count = (await c_pos.fetchone())[0]
            return SlotAccount(
                slot_id=row["slot_id"],
                name=row["name"],
                initial_capital=row["initial_capital"],
                cash_balance=row["cash_balance"],
                stock_value=row["stock_value"],
                total_asset=row["total_asset"],
                total_profit=row["total_profit"],
                profit_rate=row["profit_rate"],
                is_active=bool(row["is_active"]),
                active_positions_count=pos_count
            )

    async def get_positions(self, slot_id: Optional[str] = None) -> List[SlotPosition]:
        """보유 포지션 조회 (entry_time 대비 실제 영업일 경과 일수 자동 계산 반영)"""
        async with self.lock:
            db = await self.get_connection()
            query = "SELECT * FROM slot_positions"
            params = ()
            if slot_id:
                query += " WHERE slot_id = ?"
                params = (slot_id,)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            positions = []
            now_date = datetime.now().date()
            from datetime import timedelta
            for r in rows:
                pos_name = r["name"] if "name" in r.keys() and r["name"] else STOCK_NAME_MAP.get(r["ticker"], "")
                raw_entry = r["entry_time"]
                entry_dt = datetime.fromisoformat(raw_entry) if isinstance(raw_entry, str) else raw_entry

                # entry_dt부터 오늘까지의 영업일(월~금) 경과 일수 자동 계산
                calc_days = 0
                if isinstance(entry_dt, datetime) and entry_dt.date() < now_date:
                    cur_d = entry_dt.date()
                    while cur_d < now_date:
                        cur_d += timedelta(days=1)
                        if cur_d.weekday() < 5:
                            calc_days += 1
                effective_holding_days = max(int(r["holding_days"] or 0), calc_days)

                positions.append(SlotPosition(
                    slot_id=r["slot_id"],
                    ticker=r["ticker"],
                    name=pos_name,
                    asset_type=r["asset_type"],
                    quantity=r["quantity"],
                    entry_price=r["entry_price"],
                    current_price=r["current_price"],
                    stop_loss_price=r["stop_loss_price"],
                    take_profit_price=r["take_profit_price"],
                    profit_rate=r["profit_rate"],
                    entry_time=entry_dt,
                    holding_days=effective_holding_days
                ))
            return positions

    async def refresh_holding_days(self):
        """장전 루틴: DB slot_positions의 holding_days 컬럼을 실제 영업일 경과 수치로 일괄 갱신"""
        async with self.lock:
            db = await self.get_connection()
            cursor = await db.execute("SELECT slot_id, ticker, entry_time, holding_days FROM slot_positions")
            rows = await cursor.fetchall()
            now_date = datetime.now().date()
            from datetime import timedelta
            for r in rows:
                raw_entry = r["entry_time"]
                entry_dt = datetime.fromisoformat(raw_entry) if isinstance(raw_entry, str) else raw_entry
                calc_days = 0
                if isinstance(entry_dt, datetime) and entry_dt.date() < now_date:
                    cur_d = entry_dt.date()
                    while cur_d < now_date:
                        cur_d += timedelta(days=1)
                        if cur_d.weekday() < 5:
                            calc_days += 1
                effective_days = max(int(r["holding_days"] or 0), calc_days)
                if effective_days != r["holding_days"]:
                    await db.execute(
                        "UPDATE slot_positions SET holding_days = ? WHERE slot_id = ? AND ticker = ?",
                        (effective_days, r["slot_id"], r["ticker"])
                    )
            await db.commit()

    async def open_position(self, signal: Signal, tick: TickData) -> Optional[SlotPosition]:
        """
        신규 포지션 진입 및 현금 차감:
        SqlPaperExecutor의 체결 호가 규칙 적용
        """
        exec_price = SqlPaperExecutor.compute_execution_price("BUY", signal.order_type, tick)
        order_cost = exec_price * signal.quantity
        buy_fee = order_cost * SqlPaperExecutor.FEE_RATE
        total_required_cash = order_cost + buy_fee

        stock_name = signal.name or STOCK_NAME_MAP.get(signal.ticker, "")

        async with self.lock:
            db = await self.get_connection()
            # 잔고 확인
            cursor = await db.execute(
                "SELECT cash_balance, is_active FROM slot_accounts WHERE slot_id = ?",
                (signal.slot_id,)
            )
            row = await cursor.fetchone()
            if not row or not row[1] or row[0] < total_required_cash:
                return None  # 슬롯 비활성 또는 잔고 부족 시 기각

            new_cash = row[0] - total_required_cash
            stop_loss = signal.stop_loss_price or round(exec_price * 0.965, 2)

            # 포지션 삽입/갱신 (분할 매수 시 수량 누적, 평단가 및 손절/익절가 비례 재계산)
            await db.execute("""
                INSERT INTO slot_positions 
                (slot_id, ticker, name, asset_type, quantity, entry_price, current_price, stop_loss_price, take_profit_price, profit_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)
                ON CONFLICT(slot_id, ticker) DO UPDATE SET
                    entry_price = (entry_price * quantity + excluded.entry_price * excluded.quantity) / (quantity + excluded.quantity),
                    quantity = quantity + excluded.quantity,
                    current_price = excluded.current_price,
                    stop_loss_price = CASE 
                        WHEN excluded.stop_loss_price > 0 AND excluded.entry_price > 0 
                        THEN round(((entry_price * quantity + excluded.entry_price * excluded.quantity) / (quantity + excluded.quantity)) * (excluded.stop_loss_price / excluded.entry_price), 2)
                        ELSE round(((entry_price * quantity + excluded.entry_price * excluded.quantity) / (quantity + excluded.quantity)) * 0.965, 2)
                    END,
                    take_profit_price = CASE 
                        WHEN excluded.take_profit_price > 0 AND excluded.entry_price > 0 
                        THEN round(((entry_price * quantity + excluded.entry_price * excluded.quantity) / (quantity + excluded.quantity)) * (excluded.take_profit_price / excluded.entry_price), 2)
                        ELSE take_profit_price 
                    END,
                    name = CASE WHEN excluded.name != '' THEN excluded.name ELSE name END
            """, (
                signal.slot_id,
                signal.ticker,
                stock_name,
                signal.asset_type,
                signal.quantity,
                exec_price,
                exec_price,
                stop_loss,
                signal.take_profit_price
            ))

            # 계좌 잔고 갱신
            await db.execute(
                "UPDATE slot_accounts SET cash_balance = ?, updated_at = CURRENT_TIMESTAMP WHERE slot_id = ?",
                (new_cash, signal.slot_id)
            )

            await db.commit()

            # 갱신된 실제 누적 포지션 조회
            c_saved = await db.execute(
                "SELECT * FROM slot_positions WHERE slot_id = ? AND ticker = ?",
                (signal.slot_id, signal.ticker)
            )
            saved = await c_saved.fetchone()

        # 실시간 가격 반영하여 자산 재계산
        await self.update_market_prices({signal.ticker: tick.price})

        if saved:
            return SlotPosition(
                slot_id=saved["slot_id"],
                ticker=saved["ticker"],
                name=saved["name"] if "name" in saved.keys() and saved["name"] else stock_name,
                asset_type=saved["asset_type"],
                quantity=saved["quantity"],
                entry_price=saved["entry_price"],
                current_price=saved["current_price"],
                stop_loss_price=saved["stop_loss_price"],
                take_profit_price=saved["take_profit_price"],
                profit_rate=saved["profit_rate"]
            )

        return SlotPosition(
            slot_id=signal.slot_id,
            ticker=signal.ticker,
            name=stock_name,
            asset_type=signal.asset_type,
            quantity=signal.quantity,
            entry_price=exec_price,
            current_price=exec_price,
            stop_loss_price=stop_loss,
            take_profit_price=signal.take_profit_price,
            profit_rate=0.0
        )

    async def close_position(
        self,
        slot_id: str,
        ticker: str,
        tick: TickData,
        exit_reason: ExitReason,
        order_type: OrderType = "LIMIT_NORMAL",
        quantity: Optional[int] = None
    ) -> Optional[TradeRecord]:
        """
        보유 포지션 청산 및 차등 세금/수수료 정산:
        - quantity 지정 시 부분 청산 지원 (지정 안 하거나 보유 수량 이상이면 전량 청산)
        """
        async with self.lock:
            db = await self.get_connection()
            cursor = await db.execute(
                "SELECT * FROM slot_positions WHERE slot_id = ? AND ticker = ?",
                (slot_id, ticker)
            )
            pos = await cursor.fetchone()
            if not pos:
                return None

            pos_qty = pos["quantity"]
            sell_qty = quantity if (quantity and 0 < quantity < pos_qty) else pos_qty
            remaining_qty = pos_qty - sell_qty

            exec_price = SqlPaperExecutor.compute_execution_price("SELL", order_type, tick)
            gross_pnl, total_cost, net_pnl, profit_rate = SqlPaperExecutor.calculate_costs_and_net_pnl(
                pos["entry_price"],
                exec_price,
                sell_qty,
                pos["asset_type"]
            )

            settled_cash = (exec_price * sell_qty) - (
                (exec_price * sell_qty * SqlPaperExecutor.FEE_RATE) +
                (0.0 if pos["asset_type"] == "ETF" else (exec_price * sell_qty * SqlPaperExecutor.STOCK_TAX_RATE))
            )

            # 전량 청산 시 삭제, 부분 청산 시 잔여 수량 차감
            if remaining_qty <= 0:
                await db.execute(
                    "DELETE FROM slot_positions WHERE slot_id = ? AND ticker = ?",
                    (slot_id, ticker)
                )
            else:
                await db.execute(
                    "UPDATE slot_positions SET quantity = ? WHERE slot_id = ? AND ticker = ?",
                    (remaining_qty, slot_id, ticker)
                )

            # 현금 환입 및 총 실현손익 갱신
            await db.execute("""
                UPDATE slot_accounts 
                SET cash_balance = cash_balance + ?,
                    total_profit = total_profit + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE slot_id = ?
            """, (settled_cash, net_pnl, slot_id))

            pos_name = pos["name"] if "name" in pos.keys() and pos["name"] else STOCK_NAME_MAP.get(ticker, "")
            # 거래 기록 저장
            c_ins = await db.execute("""
                INSERT INTO trade_records 
                (slot_id, ticker, name, asset_type, quantity, buy_price, sell_price, gross_pnl, total_cost, net_pnl, profit_rate, entry_time, exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                slot_id,
                ticker,
                pos_name,
                pos["asset_type"],
                sell_qty,
                pos["entry_price"],
                exec_price,
                gross_pnl,
                total_cost,
                net_pnl,
                profit_rate,
                pos["entry_time"],
                exit_reason
            ))
            trade_id = c_ins.lastrowid
            await db.commit()

            # 락 내에서 실시간 자산 재계산 (동일 커넥션 사용, 데드락 방지)
            await self._update_market_prices_unlocked(db, {ticker: tick.price})

        return TradeRecord(
            id=trade_id,
            slot_id=slot_id,
            ticker=ticker,
            name=pos_name,
            asset_type=pos["asset_type"],
            quantity=sell_qty,
            buy_price=pos["entry_price"],
            sell_price=exec_price,
            gross_pnl=gross_pnl,
            total_cost=total_cost,
            net_pnl=net_pnl,
            profit_rate=profit_rate,
            entry_time=datetime.fromisoformat(pos["entry_time"]) if isinstance(pos["entry_time"], str) else pos["entry_time"],
            exit_reason=exit_reason
        )

    async def _update_market_prices_unlocked(self, db: aiosqlite.Connection, price_map: Dict[str, float]):
        """이미 락을 보유한 상태에서 호출되는 자산 및 평가손익 갱신 헬퍼"""
        for ticker, price in price_map.items():
            await db.execute("""
                UPDATE slot_positions 
                SET current_price = ?,
                    profit_rate = ROUND(((? - entry_price) / entry_price) * 100.0, 2)
                WHERE ticker = ?
            """, (price, price, ticker))

        # 슬롯별 총 평가금액 갱신
        cursor = await db.execute("SELECT slot_id FROM slot_accounts")
        slot_ids = [r[0] for r in await cursor.fetchall()]

        for s_id in slot_ids:
            c_val = await db.execute("""
                SELECT COALESCE(SUM(current_price * quantity), 0.0) 
                FROM slot_positions WHERE slot_id = ?
            """, (s_id,))
            stock_val = (await c_val.fetchone())[0]

            await db.execute("""
                UPDATE slot_accounts 
                SET stock_value = ?,
                    total_asset = cash_balance + ?,
                    total_profit = (cash_balance + ? - initial_capital),
                    profit_rate = ROUND(((cash_balance + ? - initial_capital) / initial_capital) * 100.0, 2)
                WHERE slot_id = ?
            """, (stock_val, stock_val, stock_val, stock_val, s_id))

        await db.commit()

    async def update_market_prices(self, price_map: Dict[str, float]):
        """실시간 시세 반영하여 평가손익 및 계좌 총자산 재계산 (외부 호출용)"""
        async with self.lock:
            db = await self.get_connection()
            await self._update_market_prices_unlocked(db, price_map)

    async def set_slot_active(self, slot_id: str, is_active: bool):
        """특정 슬롯 활성화/비활성화 상태 업데이트"""
        async with self.lock:
            db = await self.get_connection()
            await db.execute(
                "UPDATE slot_accounts SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE slot_id = ?",
                (1 if is_active else 0, slot_id)
            )
            await db.commit()

    async def update_stop_loss_price(self, slot_id: str, ticker: str, new_stop_loss_price: float):
        """보유 포지션의 손절가를 상향(Breakeven 또는 Trailing) 갱신"""
        async with self.lock:
            db = await self.get_connection()
            await db.execute(
                "UPDATE slot_positions SET stop_loss_price = ? WHERE slot_id = ? AND ticker = ?",
                (new_stop_loss_price, slot_id, ticker)
            )
            await db.commit()

    async def get_recent_trades(
        self,
        slot_id: Optional[str] = None,
        limit: int = 20
    ) -> List[TradeRecord]:
        """최근 체결/청산 거래 내역 조회"""
        async with self.lock:
            db = await self.get_connection()
            query = "SELECT * FROM trade_records"
            params = []
            if slot_id:
                query += " WHERE slot_id = ?"
                params.append(slot_id)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
            trades = []
            for r in rows:
                t_name = r["name"] if "name" in r.keys() and r["name"] else STOCK_NAME_MAP.get(r["ticker"], "")
                trades.append(TradeRecord(
                    id=r["id"],
                    slot_id=r["slot_id"],
                    ticker=r["ticker"],
                    name=t_name,
                    asset_type=r["asset_type"],
                    quantity=r["quantity"],
                    buy_price=r["buy_price"],
                    sell_price=r["sell_price"],
                    gross_pnl=r["gross_pnl"],
                    total_cost=r["total_cost"],
                    net_pnl=r["net_pnl"],
                    profit_rate=r["profit_rate"],
                    entry_time=datetime.fromisoformat(r["entry_time"]) if isinstance(r["entry_time"], str) else r["entry_time"],
                    exit_time=datetime.fromisoformat(r["exit_time"]) if isinstance(r["exit_time"], str) else r["exit_time"],
                    exit_reason=r["exit_reason"]
                ))
            return trades

    async def get_trades_for_days(
        self,
        days: int = 7,
        slot_id: Optional[str] = None
    ) -> List[TradeRecord]:
        """최근 N일간의 체결 거래 내역 조회"""
        async with self.lock:
            db = await self.get_connection()
            query = "SELECT * FROM trade_records WHERE exit_time >= datetime('now', ? || ' days')"
            params = [f"-{days}"]
            if slot_id:
                query += " AND slot_id = ?"
                params.append(slot_id)
            query += " ORDER BY id DESC"

            cursor = await db.execute(query, tuple(params))
            rows = await cursor.fetchall()
            trades = []
            for r in rows:
                t_name = r["name"] if "name" in r.keys() and r["name"] else STOCK_NAME_MAP.get(r["ticker"], "")
                trades.append(TradeRecord(
                    id=r["id"],
                    slot_id=r["slot_id"],
                    ticker=r["ticker"],
                    name=t_name,
                    asset_type=r["asset_type"],
                    quantity=r["quantity"],
                    buy_price=r["buy_price"],
                    sell_price=r["sell_price"],
                    gross_pnl=r["gross_pnl"],
                    total_cost=r["total_cost"],
                    net_pnl=r["net_pnl"],
                    profit_rate=r["profit_rate"],
                    entry_time=datetime.fromisoformat(r["entry_time"]) if isinstance(r["entry_time"], str) else r["entry_time"],
                    exit_time=datetime.fromisoformat(r["exit_time"]) if isinstance(r["exit_time"], str) else r["exit_time"],
                    exit_reason=r["exit_reason"]
                ))
            return trades

    async def compute_half_kelly_capital(
        self,
        slot_id: str,
        days: int = 10,
        default_capital: float = 2_500_000.0,
        min_ratio: float = 0.10,
        max_ratio: float = 0.30
    ) -> float:
        """
        최근 days영업일 체결 내역 기반 Half-Kelly 동적 포지션 사이징 계산:
        - Win Rate(W)와 Payoff Ratio(R)을 산출하여 Kelly Fraction f* 계산
        - f_half = 0.5 * f*
        - 가용 예수금에 곱하여 10% ~ 30% 범위로 클램핑된 배정 금액 반환
        - 표본이 적거나(5건 미만) 기대값이 마이너스일 때는 default_capital 또는 min_ratio(10%) 적용
        """
        trades = await self.get_trades_for_days(days, slot_id=slot_id)
        if len(trades) < 5:
            return default_capital

        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl < 0]

        win_count = len(wins)
        loss_count = len(losses)
        total_count = len(trades)

        w = win_count / total_count if total_count > 0 else 0.0
        avg_gain = (sum(t.net_pnl for t in wins) / win_count) if win_count > 0 else 0.0
        avg_loss = (abs(sum(t.net_pnl for t in losses)) / loss_count) if loss_count > 0 else 0.0

        r = (avg_gain / avg_loss) if avg_loss > 0 else 1.0

        if r <= 0:
            kelly_f = 0.0
        else:
            kelly_f = w - ((1.0 - w) / r)

        if r <= 0 or kelly_f <= 0:
            # 기대값 마이너스 또는 연패 구간: 사용자 승인 정책에 따라 최소 방어 금액(100만 원)으로 탐색 매매
            account = await self.get_slot_account(slot_id)
            if not account or account.cash_balance <= 0:
                return default_capital
            return min(1_000_000.0, account.cash_balance)

        half_kelly = 0.5 * kelly_f
        clamped_ratio = max(min_ratio, min(half_kelly, max_ratio))

        account = await self.get_slot_account(slot_id)
        if not account or account.cash_balance <= 0:
            return default_capital

        allocated_capital = round(account.cash_balance * clamped_ratio, -4)
        return max(1_000_000.0, allocated_capital)

    async def save_daily_settlement(
        self,
        date: str,
        total_asset: float,
        cash_balance: float,
        stock_value: float,
        total_profit: float,
        profit_rate: float,
        realized_pnl: float = 0.0,
        unrealized_pnl: float = 0.0,
        trade_count: int = 0,
        review_markdown: str = "",
        screening_json: str = "{}"
    ) -> DailySettlement:
        """일일 장마감 결산 스냅샷 및 AI 복기 결과를 SQLite 원장에 영구 기록 (UPSERT)"""
        async with self.lock:
            db = await self.get_connection()
            await db.execute("""
                INSERT OR REPLACE INTO daily_settlements (
                    date, total_asset, cash_balance, stock_value,
                    total_profit, profit_rate, realized_pnl, unrealized_pnl,
                    trade_count, review_markdown, screening_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                date, total_asset, cash_balance, stock_value,
                total_profit, profit_rate, realized_pnl, unrealized_pnl,
                trade_count, review_markdown, screening_json
            ))
            await db.commit()

        return DailySettlement(
            date=date,
            total_asset=total_asset,
            cash_balance=cash_balance,
            stock_value=stock_value,
            total_profit=total_profit,
            profit_rate=profit_rate,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            trade_count=trade_count,
            review_markdown=review_markdown,
            screening_json=screening_json,
        )

    async def get_daily_settlements(self, limit: int = 30) -> List[DailySettlement]:
        """최근 일일 결산/복기 히스토리 목록 조회"""
        async with self.lock:
            db = await self.get_connection()
            cursor = await db.execute(
                "SELECT * FROM daily_settlements ORDER BY date DESC LIMIT ?",
                (limit,)
            )
            rows = await cursor.fetchall()
            return [
                DailySettlement(
                    date=r["date"],
                    total_asset=r["total_asset"],
                    cash_balance=r["cash_balance"],
                    stock_value=r["stock_value"],
                    total_profit=r["total_profit"],
                    profit_rate=r["profit_rate"],
                    realized_pnl=r["realized_pnl"],
                    unrealized_pnl=r["unrealized_pnl"],
                    trade_count=r["trade_count"],
                    review_markdown=r["review_markdown"],
                    screening_json=r["screening_json"],
                    created_at=datetime.fromisoformat(r["created_at"]) if isinstance(r["created_at"], str) else r["created_at"]
                )
                for r in rows
            ]

    async def get_daily_settlement(self, date: str) -> Optional[DailySettlement]:
        """특정 일자의 결산/복기 조회"""
        async with self.lock:
            db = await self.get_connection()
            cursor = await db.execute(
                "SELECT * FROM daily_settlements WHERE date = ?",
                (date,)
            )
            r = await cursor.fetchone()
            if not r:
                return None
            return DailySettlement(
                date=r["date"],
                total_asset=r["total_asset"],
                cash_balance=r["cash_balance"],
                stock_value=r["stock_value"],
                total_profit=r["total_profit"],
                profit_rate=r["profit_rate"],
                realized_pnl=r["realized_pnl"],
                unrealized_pnl=r["unrealized_pnl"],
                trade_count=r["trade_count"],
                review_markdown=r["review_markdown"],
                screening_json=r["screening_json"],
                created_at=datetime.fromisoformat(r["created_at"]) if isinstance(r["created_at"], str) else r["created_at"]
            )

