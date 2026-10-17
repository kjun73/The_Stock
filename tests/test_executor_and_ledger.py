import asyncio
import os
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
