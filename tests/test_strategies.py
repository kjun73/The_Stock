import asyncio
from datetime import datetime, time as dtime
import pytest
from core.models import SlotPosition, TickData
from strategies.market_filter import MarketFilterManager
from strategies.slot_a_institutional_swing import SlotAInstitutionalSwing
from strategies.slot_b_bollinger_grid import SlotBBollingerGrid
from strategies.slot_c_volatility_breakout import SlotCVolatilityBreakout
from strategies.slot_d_retest_swing import SlotDRetestSwing


def test_market_filter_rules():
    """마켓 필터: 추세 추종(A, C, D) 차단 vs 역추세 ETF(B) 허용 검증"""
    filter_mgr = MarketFilterManager()

    # 1. 상승장 (지수 2600 > 20일선 2580)
    filter_mgr.update_kospi_status(current_index=2600.0, ma20=2580.0, daily_change_pct=0.5)
    assert filter_mgr.is_bear_market is False
    assert filter_mgr.can_enter("SLOT_A") is True
    assert filter_mgr.can_enter("SLOT_B") is True
    assert filter_mgr.can_enter("SLOT_C") is True
    assert filter_mgr.can_enter("SLOT_D") is True

    # 2. 하락장 (지수 2500 < 20일선 2580)
    filter_mgr.update_kospi_status(current_index=2500.0, ma20=2580.0, daily_change_pct=-0.8)
    assert filter_mgr.is_bear_market is True
    # 슬롯 A, C, D는 전면 차단 (현금 보존)
    assert filter_mgr.can_enter("SLOT_A") is False
    assert filter_mgr.can_enter("SLOT_C") is False
    assert filter_mgr.can_enter("SLOT_D") is False
    # 슬롯 B (ETF 역추세 분할매수)는 하락장에서도 진입 허용!
    assert filter_mgr.can_enter("SLOT_B") is True

    # 3. 대폭락 패닉셀 (-3.5% 급락)
    filter_mgr.update_kospi_status(current_index=2450.0, ma20=2580.0, daily_change_pct=-3.5)
    assert filter_mgr.is_panic_sell_day is True
    # 슬롯 B도 -3% 이상 패닉셀 시에는 위험 방어를 위해 차단!
    assert filter_mgr.can_enter("SLOT_B") is False


@pytest.mark.asyncio
async def test_slot_a_tp_and_sl():
    """슬롯 A: 익절 +6.0%, 손절 -3.5% 트리거 검증"""
    slot_a = SlotAInstitutionalSwing()

    # 보유 포지션 설정 (진입가 100,000원)
    pos = SlotPosition(
        slot_id="SLOT_A",
        ticker="005930",
        asset_type="STOCK",
        quantity=30,
        entry_price=100_000.0,
        current_price=100_000.0,
        stop_loss_price=96_500.0,  # -3.5%
        take_profit_price=106_000.0  # +6.0%
    )

    # 1. 익절 시그널 (+6.0% 이상 도달)
    tick_tp = TickData(ticker="005930", price=106_200.0, bid_price=106_200.0, ask_price=106_300.0)
    sig_tp = await slot_a.evaluate_market(tick_tp, pos)
    assert sig_tp is not None
    assert sig_tp.action == "SELL"
    assert sig_tp.exit_reason == "STRATEGY_TAKE_PROFIT"

    # 2. 손절 시그널 (-3.5% 이하 이탈)
    tick_sl = TickData(ticker="005930", price=96_400.0, bid_price=96_400.0, ask_price=96_500.0)
    sig_sl = await slot_a.evaluate_market(tick_sl, pos)
    assert sig_sl is not None
    assert sig_sl.action == "SELL"
    assert sig_sl.exit_reason == "STRATEGY_STOP_LOSS"


@pytest.mark.asyncio
async def test_slot_b_bollinger_grid():
    """슬롯 B: KODEX 200/코스닥150 3분할 매수 및 중심선 익절 검증"""
    slot_b = SlotBBollingerGrid()
    slot_b.bypass_time_filter = True

    # 069500 (KODEX 200)에 대해 볼린저 밴드 하단 이탈 시그널
    slot_b.update_bands(ticker="069500", upper=36000.0, middle=35000.0, lower=34000.0)

    # 하단 밴드(34000) 이하로 하락 시 1차 분할 매수 발생 (300만원 어치)
    tick_lower = TickData(ticker="069500", price=33900.0, bid_price=33850.0, ask_price=33900.0)
    sig_buy1 = await slot_b.evaluate_market(tick_lower, None)
    assert sig_buy1 is not None
    assert sig_buy1.action == "BUY"
    assert sig_buy1.order_type == "SPLIT_BUY_1"
    assert sig_buy1.asset_type == "ETF"

    # 중심선(35000) 도달 시 50% 분할 익절 (1회 한정 검증)
    pos_holding = SlotPosition(
        slot_id="SLOT_B",
        ticker="069500",
        asset_type="ETF",
        quantity=10,
        entry_price=33900.0,
        current_price=35100.0,
        stop_loss_price=32700.0
    )
    tick_middle = TickData(ticker="069500", price=35100.0, bid_price=35100.0, ask_price=35150.0)
    sig_tp_half = await slot_b.evaluate_market(tick_middle, pos_holding)
    assert sig_tp_half is not None
    assert sig_tp_half.action == "SELL"
    assert sig_tp_half.quantity == 5  # 10주 중 50%인 5주만 매도!
    assert "중심선" in sig_tp_half.reason

    # 동일 틱 재유입 시 중복 분할 매도 방지 검증 (partial_tp_tracker가 True이므로 None 반환)
    sig_duplicate = await slot_b.evaluate_market(tick_middle, pos_holding)
    assert sig_duplicate is None


@pytest.mark.asyncio
async def test_pre_market_hours_block():
    """장전(09:00 이전) 또는 장후(15:20 이후)에는 신규 매수 시그널 원천 차단 검증"""
    now = datetime.now()
    current_time = now.time()

    # 현재 시각이 09:00 이전이거나 15:20 이후인 경우 테스트
    if current_time < dtime(9, 0) or current_time > dtime(15, 20):
        # 1. 슬롯 B
        slot_b = SlotBBollingerGrid()
        slot_b.bypass_time_filter = False  # 정규 시간 필터 활성화
        slot_b.update_bands(ticker="069500", upper=36000.0, middle=35000.0, lower=34000.0)
        tick_lower = TickData(ticker="069500", price=33900.0, bid_price=33850.0, ask_price=33900.0)
        sig_b = await slot_b.evaluate_market(tick_lower, None)
        assert sig_b is None, "장전/장후에는 슬롯 B 신규 매수가 차단되어야 합니다"

        # 2. 슬롯 D
        slot_d = SlotDRetestSwing()
        slot_d.bypass_time_filter = False
        slot_d.update_candidates(["005930"], {"005930": 70000.0})
        tick_d = TickData(ticker="005930", price=70100.0, bid_price=70000.0, ask_price=70100.0)
        sig_d = await slot_d.evaluate_market(tick_d, None)
        assert sig_d is None, "장전/장후에는 슬롯 D 신규 매수가 차단되어야 합니다"


