import asyncio
import os
import aiosqlite
import pytest
from core.executor import SqlPaperExecutor
from core.ledger import SqliteLedger
from core.models import Signal, TickData


def test_executor_costs_stock_vs_etf():
    """주식(0.215%)과 ETF(0.015%) 차등 세율 계산 정밀 검증"""
    buy_price = 100_000.0
    sell_price = 110_000.0
    quantity = 10

    # 1. 주식 (STOCK): 매수 수수료 0.015% + 매도 수수료 0.015% + 증권거래세 0.20%
    gross, cost, net, rate = SqlPaperExecutor.calculate_costs_and_net_pnl(
        buy_price=buy_price,
        sell_price=sell_price,
        quantity=quantity,
        asset_type="STOCK"
    )
    # 총 매수금 1,000,000, 총 매도금 1,100,000, gross = 100,000
    # buy_fee = 150, sell_fee = 165, sell_tax = 2,200 => total_cost = 2,515
    assert gross == 100_000.0
    assert cost == 2515.0
    assert net == 97485.0
    assert round(rate, 2) == 9.75

    # 2. 현물 ETF: 매수 수수료 0.015% + 매도 수수료 0.015% (증권거래세 0% 면제!)
    gross_etf, cost_etf, net_etf, rate_etf = SqlPaperExecutor.calculate_costs_and_net_pnl(
        buy_price=buy_price,
        sell_price=sell_price,
        quantity=quantity,
        asset_type="ETF"
    )
    # buy_fee = 150, sell_fee = 165, sell_tax = 0 => total_cost = 315
    assert gross_etf == 100_000.0
    assert cost_etf == 315.0
    assert net_etf == 99685.0
    assert round(rate_etf, 2) == 9.97


def test_executor_slippage_penalty():
    """일반 주문 vs 돌파/비상청산 슬리피지(+0.1% / -0.1%) 페널티 검증"""
    tick = TickData(
        ticker="005930",
        price=70000.0,
        bid_price=69900.0,
        ask_price=70100.0
    )

    # 일반 지정가 매수: ask_price 그대로
    buy_normal = SqlPaperExecutor.compute_execution_price("BUY", "LIMIT_NORMAL", tick)
    assert buy_normal == 70100.0

    # 돌파 시장가 매수: ask_price + 0.1% 슬리피지 가산
    buy_breakout = SqlPaperExecutor.compute_execution_price("BUY", "MARKET_BREAKOUT", tick)
    assert buy_breakout == round(70100.0 * 1.001, 2)

    # 일반 매도: bid_price 그대로
    sell_normal = SqlPaperExecutor.compute_execution_price("SELL", "LIMIT_NORMAL", tick)
    assert sell_normal == 69900.0

    # 비상 청산 / 킬스위치 매도: bid_price - 0.1% 슬리피지 차감
    sell_emergency = SqlPaperExecutor.compute_execution_price("SELL", "EMERGENCY_FLUSH", tick)
    assert sell_emergency == round(69900.0 * 0.999, 2)


@pytest.mark.asyncio
async def test_ledger_capital_isolation_and_trading(tmp_path):
    """4개 슬롯 각 1,000만원 독립 원장 생성 및 자금 격리 거래 검증"""
    test_db = str(tmp_path / "test_paper_trading.db")
    ledger = SqliteLedger(db_path=test_db)
    await ledger.init_db()

    accounts = await ledger.get_accounts()
    assert len(accounts) == 4
    for acc in accounts:
        assert acc.initial_capital == 10_000_000.0
        assert acc.cash_balance == 10_000_000.0
        assert acc.total_asset == 10_000_000.0

    # 슬롯 A에서 매수 실행
    tick_buy = TickData(
        ticker="005930",
        price=70000.0,
        bid_price=69900.0,
        ask_price=70000.0
    )
    signal_a = Signal(
        slot_id="SLOT_A",
        ticker="005930",
        asset_type="STOCK",
        action="BUY",
        order_type="LIMIT_NORMAL",
        price=70000.0,
        quantity=50,  # 350만원 어치
        reason="외인 수급 스윙 1차 분할",
        stop_loss_price=67550.0,
        take_profit_price=74200.0
    )

    pos = await ledger.open_position(signal_a, tick_buy)
    assert pos is not None
    assert pos.quantity == 50
    assert pos.entry_price == 70000.0

    # 슬롯 A 계좌 현금만 차감되고, 타 슬롯(B, C, D) 자금은 1000만원 그대로 보존되는지 검증
    acc_a = next(a for a in (await ledger.get_accounts()) if a.slot_id == "SLOT_A")
    acc_b = next(a for a in (await ledger.get_accounts()) if a.slot_id == "SLOT_B")
    
    assert acc_a.cash_balance < 10_000_000.0
    assert acc_a.stock_value == 3_500_000.0
    assert acc_b.cash_balance == 10_000_000.0
    assert acc_b.stock_value == 0.0

    # 익절 청산 테스트
    tick_sell = TickData(
        ticker="005930",
        price=74200.0,
        bid_price=74200.0,
        ask_price=74300.0
    )
    trade = await ledger.close_position(
        slot_id="SLOT_A",
        ticker="005930",
        tick=tick_sell,
        exit_reason="STRATEGY_TAKE_PROFIT"
    )

    assert trade is not None
    assert trade.net_pnl > 0
    assert trade.exit_reason == "STRATEGY_TAKE_PROFIT"

    # 포지션이 비워졌는지 확인
    positions = await ledger.get_positions("SLOT_A")
    assert len(positions) == 0


@pytest.mark.asyncio
async def test_daily_settlement_persistence(tmp_path):
    """일일 장마감 결산 스냅샷 저장 및 날짜별 히스토리 조회 검증"""
    test_db = str(tmp_path / "test_settlement.db")
    ledger = SqliteLedger(db_path=test_db)
    await ledger.init_db()

    # 1. 초기 상태에서 결산 저장
    saved = await ledger.save_daily_settlement(
        date="2026-10-17",
        total_asset=40_152_750.0,
        cash_balance=34_237_460.0,
        stock_value=5_915_290.0,
        total_profit=152_750.0,
        profit_rate=0.38,
        realized_pnl=0.0,
        unrealized_pnl=152_750.0,
        trade_count=3,
        review_markdown="### 2026-10-17 복기 리포트",
        screening_json='{"SLOT_A": ["005930"]}'
    )
    assert saved.date == "2026-10-17"
    assert saved.total_asset == 40_152_750.0
    assert saved.profit_rate == 0.38

    # 2. 목록 조회 검증
    settlements = await ledger.get_daily_settlements(limit=10)
    assert len(settlements) == 1
    assert settlements[0].date == "2026-10-17"
    assert settlements[0].stock_value == 5_915_290.0

    # 3. 단일 일자 조회 검증
    s = await ledger.get_daily_settlement("2026-10-17")
    assert s is not None
    assert s.cash_balance == 34_237_460.0
    assert "2026-10-17 복기 리포트" in s.review_markdown

    # 4. 존재하지 않는 일자 조회 시 None
    non_existent = await ledger.get_daily_settlement("2099-01-01")
    assert non_existent is None


@pytest.mark.asyncio
async def test_half_kelly_capital_allocation(tmp_path):
    """Half-Kelly 기반 동적 포지션 사이징 계산 및 클램핑 검증"""
    test_db = str(tmp_path / "test_kelly.db")
    ledger = SqliteLedger(db_path=test_db)
    await ledger.init_db()

    # 1. 체결 데이터가 5건 미만일 때: 기본 배정금(250만 원) 반환
    default_cap = await ledger.compute_half_kelly_capital(slot_id="SLOT_C", default_capital=2_500_000.0)
    assert default_cap == 2_500_000.0

    # 2. 가상 체결 6건 생성 (4승 2패, 승률 66.7%, 손익비 2.0)
    # Win trades: +200,000원 x 4건 = 800,000원 (평균익절 200,000)
    # Loss trades: -100,000원 x 2건 = -200,000원 (평균손실 100,000)
    # W = 4/6 = 0.667, R = 2.0
    # Kelly f* = 0.667 - (0.333 / 2.0) = 0.667 - 0.167 = 0.50
    # Half-Kelly = 0.5 * 0.50 = 0.25 (25%)
    # 슬롯 C 예수금 10,000,000원 * 25% = 2,500,000원
    async with ledger._get_connection() if hasattr(ledger, "_get_connection") else aiosqlite.connect(test_db) as db:
        for i in range(4):
            await db.execute("""
                INSERT INTO trade_records 
                (slot_id, ticker, name, asset_type, quantity, buy_price, sell_price, gross_pnl, total_cost, net_pnl, profit_rate, entry_time, exit_time, exit_reason)
                VALUES ('SLOT_C', '005930', '삼성전자', 'STOCK', 10, 100000, 120000, 200000, 0, 200000, 2.0, datetime('now'), datetime('now'), 'STRATEGY_TRAILING_STOP')
            """)
        for i in range(2):
            await db.execute("""
                INSERT INTO trade_records 
                (slot_id, ticker, name, asset_type, quantity, buy_price, sell_price, gross_pnl, total_cost, net_pnl, profit_rate, entry_time, exit_time, exit_reason)
                VALUES ('SLOT_C', '005930', '삼성전자', 'STOCK', 10, 100000, 90000, -100000, 0, -100000, -1.0, datetime('now'), datetime('now'), 'STRATEGY_STOP_LOSS')
            """)
        await db.commit()

    cap_kelly = await ledger.compute_half_kelly_capital(slot_id="SLOT_C")
    # 25% 배정 = 2,500,000원
    assert cap_kelly == 2_500_000.0

