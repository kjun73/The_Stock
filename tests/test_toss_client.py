import asyncio
import pytest
from broker.toss_client import TossClient, TossSubscriptionManager


@pytest.mark.asyncio
async def test_toss_subscription_manager():
    """토스 구독 매니저: 보유 종목 우선 배정 및 무제한 유니버스 검증"""
    mgr = TossSubscriptionManager()

    # 1. 보유 종목 등록
    mgr.update_held_positions(["005930", "000660"])
    assert mgr.get_held_tickers() == ["005930", "000660"] or mgr.get_held_tickers() == ["000660", "005930"]

    # 2. 감시 종목 등록 (37개 제한 없이 등록 가능)
    watchlist = [f"0000{i:02d}" for i in range(50)]
    mgr.update_momentum_watchlist(watchlist)

    tickers = mgr.get_subscribed_tickers()
    # 보유 2 + 감시 50 + 기본 ETF 4 = 56 종목
    assert len(tickers) >= 52
    assert "005930" in tickers
    assert "000660" in tickers
    assert "069500" in tickers  # KODEX 200 기본 포함


@pytest.mark.asyncio
async def test_toss_client_batch_prices():
    """토스 클라이언트: 다중 종목 배치 일괄 조회 검증"""
    client = TossClient()
    tickers = ["005930", "000660", "069500", "229200", "035420"]
    tick_map = await client.get_realtime_prices_batch(tickers)

    assert len(tick_map) == 5
    for ticker in tickers:
        assert ticker in tick_map
        tick = tick_map[ticker]
        assert tick.price > 0
        assert tick.bid_price > 0
        assert tick.ask_price >= tick.bid_price


@pytest.mark.asyncio
async def test_toss_all_strategy_universes_subscription():
    """모든 슬롯 전략(A, B, C, D) 유니버스 종목이 빠짐없이 구독되는지 검증"""
    mgr = TossSubscriptionManager()
    mgr.update_held_positions(["229200"])
    mgr.update_strategy_universe("SLOT_A", ["005930", "000660", "005380"])
    mgr.update_strategy_universe("SLOT_C", ["196170", "247540", "028300"])
    mgr.update_strategy_universe("SLOT_D", ["035420", "068270"])

    subscribed = mgr.get_subscribed_tickers()
    assert "229200" in subscribed
    assert "005930" in subscribed
    assert "196170" in subscribed
    assert "035420" in subscribed
    assert "069500" in subscribed  # 기본 ETF 포함
    assert len(subscribed) >= 9
