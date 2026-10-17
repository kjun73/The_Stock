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

    async def init_db(self):
        """데이터베이스 및 4개 슬롯 가상 원장 테이블 초기화"""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM slot_accounts")
            rows = await cursor.fetchall()
            accounts = []
            for r in rows:
                # 활성 포지션 수 조회
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

    async def get_positions(self, slot_id: Optional[str] = None) -> List[SlotPosition]:
        """보유 포지션 조회"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM slot_positions"
            params = ()
            if slot_id:
                query += " WHERE slot_id = ?"
                params = (slot_id,)

            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            positions = []
            for r in rows:
                pos_name = r["name"] if "name" in r.keys() and r["name"] else STOCK_NAME_MAP.get(r["ticker"], "")
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
                    entry_time=datetime.fromisoformat(r["entry_time"]) if isinstance(r["entry_time"], str) else r["entry_time"],
                    holding_days=r["holding_days"]
                ))
            return positions

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

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
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

            # 포지션 삽입/갱신 (분할 매수 시 수량 누적 및 평단가 재계산)
            await db.execute("""
                INSERT INTO slot_positions 
                (slot_id, ticker, name, asset_type, quantity, entry_price, current_price, stop_loss_price, take_profit_price, profit_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)
                ON CONFLICT(slot_id, ticker) DO UPDATE SET
                    entry_price = (entry_price * quantity + excluded.entry_price * excluded.quantity) / (quantity + excluded.quantity),
                    quantity = quantity + excluded.quantity,
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
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
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

        await self.update_market_prices({ticker: tick.price})

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

    async def update_market_prices(self, price_map: Dict[str, float]):
        """실시간 시세 반영하여 평가손익 및 계좌 총자산 재계산"""
        async with aiosqlite.connect(self.db_path) as db:
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

    async def set_slot_active(self, slot_id: str, is_active: bool):
        """특정 슬롯 활성화/비활성화 상태 업데이트"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE slot_accounts SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE slot_id = ?",
                (1 if is_active else 0, slot_id)
            )
            await db.commit()

    async def get_recent_trades(
        self,
        slot_id: Optional[str] = None,
        limit: int = 20
    ) -> List[TradeRecord]:
        """최근 체결/청산 거래 내역 조회"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
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
