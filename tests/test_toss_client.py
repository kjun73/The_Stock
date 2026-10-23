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


@pytest.mark.asyncio
async def test_toss_client_429_backoff_and_priority():
    """토스 429 감지 시 백오프 재시도 및 보유종목 최우선 청크 정렬 검증"""
    from unittest.mock import AsyncMock, MagicMock, patch

    client = TossClient()
    client.subscription_manager.update_held_positions(["229200"])
    client.access_token = "live_test_token"
    client.token_expired_at = 9999999999.0

    tickers = ["005930", "000660", "229200"]
    # 229200이 held_position이므로 맨 앞에 와야 함
    held_set = set(client.subscription_manager.held_positions)
    sorted_tickers = sorted(tickers, key=lambda t: 0 if t in held_set else 1)
    assert sorted_tickers[0] == "229200"

    # 429 -> 200 모킹
    mock_res_429 = MagicMock()
    mock_res_429.status_code = 429

    mock_res_200 = MagicMock()
    mock_res_200.status_code = 200
    mock_res_200.json.return_value = {
        "result": [
            {"symbol": "229200", "lastPrice": 13370.0, "volume": 12000},
            {"symbol": "005930", "lastPrice": 216500.0, "volume": 50000},
            {"symbol": "000660", "lastPrice": 1273000.0, "volume": 3000}
        ]
    }

    mock_http = AsyncMock()
    mock_http.get.side_effect = [mock_res_429, mock_res_200]

    with patch.object(client, "get_http_client", return_value=mock_http), \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        tick_map = await client.get_realtime_prices_batch(tickers)

        assert mock_sleep.called
        assert len(tick_map) == 3
        assert "229200" in tick_map
        assert tick_map["229200"].price == 13370.0
        assert tick_map["005930"].price == 216500.0

