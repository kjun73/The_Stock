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
    """슬롯 A: 1차 분할익절(+3.0%), 본전보존(0.0%), 최종익절(+6.0%), 초기손절(-3.5%) 검증"""
    slot_a = SlotAInstitutionalSwing()

    # 보유 포지션 설정 (진입가 100,000원, 30주)
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

    # 1. 1차 50% 분할 익절 (+3.0% 이상 도달 시 15주 분할 매도)
    tick_partial = TickData(ticker="005930", price=103_200.0, bid_price=103_200.0, ask_price=103_300.0)
    sig_partial = await slot_a.evaluate_market(tick_partial, pos)
    assert sig_partial is not None
    assert sig_partial.action == "SELL"
    assert sig_partial.exit_reason == "STRATEGY_PARTIAL_TP"
    assert sig_partial.quantity == 15  # 30주의 50%인 15주 매도
    assert slot_a.partial_tp_tracker["005930"] is True
    assert slot_a.breakeven_tracker["005930"] is True

    # 2. 1차 분할 익절 후 잔여 수량(15주) 상태에서 최종 익절 (+6.0% 도달)
    pos_rem = SlotPosition(
        slot_id="SLOT_A",
        ticker="005930",
        asset_type="STOCK",
        quantity=15,
        entry_price=100_000.0,
        current_price=106_200.0,
        stop_loss_price=100_000.0,
        take_profit_price=106_000.0
    )
    tick_tp = TickData(ticker="005930", price=106_200.0, bid_price=106_200.0, ask_price=106_300.0)
    sig_tp = await slot_a.evaluate_market(tick_tp, pos_rem)
    assert sig_tp is not None
    assert sig_tp.action == "SELL"
    assert sig_tp.exit_reason == "STRATEGY_TAKE_PROFIT"
    assert sig_tp.quantity == 15

    # 3. 1차 분할 익절 후 지수 반락으로 본전(100,000원 이하) 이탈 시 본전 보존 손절 검증
    slot_a_be = SlotAInstitutionalSwing()
    slot_a_be.partial_tp_tracker["005930"] = True
    slot_a_be.breakeven_tracker["005930"] = True
    tick_be = TickData(ticker="005930", price=99_800.0, bid_price=99_800.0, ask_price=99_900.0)
    sig_be = await slot_a_be.evaluate_market(tick_be, pos_rem)
    assert sig_be is not None
    assert sig_be.exit_reason == "STRATEGY_BREAKEVEN_STOP"

    # 4. 1차 익절 전 초기 손절 (-3.5% 이하 이탈)
    slot_a_sl = SlotAInstitutionalSwing()
    tick_sl = TickData(ticker="005930", price=96_400.0, bid_price=96_400.0, ask_price=96_500.0)
    sig_sl = await slot_a_sl.evaluate_market(tick_sl, pos)
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


@pytest.mark.asyncio
async def test_slot_a_split_buy_2():
    """슬롯 A: D일 진입 후 09:15~10:00 사이 잔여 50% 2차 분할 매수 검증"""
    slot_a = SlotAInstitutionalSwing()
    # 1차 매수된 포지션 (165만 원 보유, holding_days = 0)
    pos = SlotPosition(
        slot_id="SLOT_A",
        ticker="005930",
        asset_type="STOCK",
        quantity=7,
        entry_price=211700.0,
        current_price=212000.0,
        stop_loss_price=204000.0,
        take_profit_price=224000.0,
        holding_days=0
    )
    tick = TickData(ticker="005930", price=212000.0, ask_price=212500.0, bid_price=211500.0)

    # 09:20 시점 가정
    from unittest.mock import patch
    with patch("strategies.slot_a_institutional_swing.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 10, 16, 9, 20, 0)
        sig = await slot_a.evaluate_market(tick, pos)
        assert sig is not None
        assert sig.action == "BUY"
        assert sig.order_type == "SPLIT_BUY_2"
        assert sig.quantity > 0


@pytest.mark.asyncio
async def test_slot_d_dynamic_support_calibration():
    """슬롯 D: 지지선이 현재가와 괴리되어 있을 때 실시간 시세 기준으로 자동 캘리브레이션 검증"""
    slot_d = SlotDRetestSwing()
    slot_d.bypass_time_filter = True
    # 현재가 217,000원인 삼성전자 유입 (하드코딩된 78,000원과 64% 괴리)
    tick = TickData(ticker="005930", price=217000.0, ask_price=217200.0, bid_price=216800.0)
    sig = await slot_d.evaluate_market(tick, None)
    # 지지선이 217000 * 0.98 = 212,660원으로 캘리브레이션 되었는지 확인
    assert slot_d.support_levels["005930"] == pytest.approx(217000.0 * 0.98, rel=1e-2)


@pytest.mark.asyncio
async def test_slot_c_dynamic_breakout():
    """슬롯 C: 시초가 기록 후 +1% 돌파 시 변동성 돌파 시그널 및 JEV 가드레일 정상 통과 검증"""
    from intelligence.jev_client import JevCascadeClient
    jev = JevCascadeClient()
    slot_c = SlotCVolatilityBreakout(jev_client=jev)
    slot_c.bypass_time_filter = True

    # 1. 시초가 200,000원 틱 유입
    tick_open = TickData(ticker="005930", price=200000.0, ask_price=200200.0, bid_price=199800.0, volume=1000, exec_strength=105.0)
    sig1 = await slot_c.evaluate_market(tick_open, None)
    assert sig1 is None  # 아직 미돌파 및 체결강도 미달

    # 2. 시초가 대비 +1.5% 돌파(203,000원) + 체결강도 125% + 거래량 급증 틱 유입
    tick_breakout = TickData(ticker="005930", price=203000.0, ask_price=203200.0, bid_price=202800.0, volume=5000, exec_strength=125.0)
    sig2 = await slot_c.evaluate_market(tick_breakout, None)
    assert sig2 is not None
    assert sig2.action == "BUY"
    assert sig2.order_type == "MARKET_BREAKOUT"


@pytest.mark.asyncio
async def test_slot_c_trailing_stop():
    """슬롯 C: +1.5% 돌파 시 트레일링 활성화 및 고점 대비 -1.0% 반락 시 익절 확정 검증"""
    from intelligence.jev_client import JevCascadeClient
    jev = JevCascadeClient()
    slot_c = SlotCVolatilityBreakout(jev_client=jev)
    slot_c.bypass_time_filter = True

    pos = SlotPosition(
        slot_id="SLOT_C",
        ticker="005930",
        asset_type="STOCK",
        quantity=10,
        entry_price=200_000.0,
        current_price=200_000.0,
        stop_loss_price=194_400.0  # -2.8%
    )

    # 1. +2.0% 상승(204,000원) -> 트레일링 모드 활성화 (+1.5% 초과), 청산은 미발생
    tick1 = TickData(ticker="005930", price=204000.0, bid_price=204000.0, ask_price=204200.0)
    sig1 = await slot_c.evaluate_market(tick1, pos)
    assert sig1 is None
    assert slot_c.trailing_active["005930"] is True
    assert slot_c.high_watermarks["005930"] == 204000.0

    # 2. +3.0% 추가 상승(206,000원) -> 최고가 206,000원으로 갱신
    tick2 = TickData(ticker="005930", price=206000.0, bid_price=206000.0, ask_price=206200.0)
    sig2 = await slot_c.evaluate_market(tick2, pos)
    assert sig2 is None
    assert slot_c.high_watermarks["005930"] == 206000.0

    # 3. 최고가(206,000원) 대비 -1.1% 반락 (203,700원) -> 트레일링 스탑 익절 발동!
    tick3 = TickData(ticker="005930", price=203700.0, bid_price=203700.0, ask_price=203900.0)
    sig3 = await slot_c.evaluate_market(tick3, pos)
    assert sig3 is not None
    assert sig3.action == "SELL"
    assert sig3.exit_reason == "STRATEGY_TRAILING_STOP"
    assert "트레일링 익절 발동" in sig3.reason

    # 4. 트레일링 활성화 전 -2.8% 이하로 하락 시 절대 손절 발동 검증
    slot_c_sl = SlotCVolatilityBreakout(jev_client=jev)
    slot_c_sl.bypass_time_filter = True
    tick_sl = TickData(ticker="005930", price=194300.0, bid_price=194300.0, ask_price=194400.0)
    sig_sl = await slot_c_sl.evaluate_market(tick_sl, pos)
    assert sig_sl is not None
    assert sig_sl.exit_reason == "STRATEGY_STOP_LOSS"


@pytest.mark.asyncio
async def test_slot_d_trailing_stop_and_breakeven():
    """슬롯 D: +3.5% 도달 시 트레일링 활성화, -1.5% 반락 시 익절 및 본전 보존 검증"""
    slot_d = SlotDRetestSwing()
    slot_d.bypass_time_filter = True

    pos = SlotPosition(
        slot_id="SLOT_D",
        ticker="005930",
        asset_type="STOCK",
        quantity=15,
        entry_price=100_000.0,
        current_price=100_000.0,
        stop_loss_price=97_000.0  # -3.0%
    )

    # 1. +4.0% 상승 (104,000원) -> 트레일링 활성화
    tick1 = TickData(ticker="005930", price=104000.0, bid_price=104000.0, ask_price=104100.0)
    sig1 = await slot_d.evaluate_market(tick1, pos)
    assert sig1 is None
    assert slot_d.trailing_active["005930"] is True
    assert slot_d.high_watermarks["005930"] == 104000.0

    # 2. 최고가(104,000원) 대비 -1.6% 반락 (102,300원) -> 트레일링 익절 발동
    tick2 = TickData(ticker="005930", price=102300.0, bid_price=102300.0, ask_price=102400.0)
    sig2 = await slot_d.evaluate_market(tick2, pos)
    assert sig2 is not None
    assert sig2.exit_reason == "STRATEGY_TRAILING_STOP"
    assert sig2.action == "SELL"


def test_market_filter_update_from_kodex200():
    """KODEX 200 실시간 틱 기반 마켓필터 동적 연동 검증"""
    filter_mgr = MarketFilterManager(base_index=2600.0, ma20=2580.0)

    # 1. 시초 틱 등록 (open=35,000)
    filter_mgr.update_from_kodex200(35000.0)
    assert filter_mgr.kodex200_open == 35000.0
    assert filter_mgr.kospi_daily_change_pct == 0.0
    assert filter_mgr.kospi_index == 2600.0

    # 2. +1.0% 상승 틱 (35,350)
    filter_mgr.update_from_kodex200(35350.0)
    assert filter_mgr.kospi_daily_change_pct == 1.0
    assert filter_mgr.kospi_index == 2626.0
    assert filter_mgr.is_bear_market is False

    # 3. -3.5% 급락 틱 (33,775) -> 패닉셀 발동
    filter_mgr.update_from_kodex200(33775.0)
    assert filter_mgr.kospi_daily_change_pct == -3.5
    assert filter_mgr.is_panic_sell_day is True
    assert filter_mgr.can_enter("SLOT_B") is False


def test_slot_b_and_d_dynamic_attributes():
    """슬롯 B 동적 밴드 및 슬롯 D selected_candidates 속성 무결성 검증"""
    slot_b = SlotBBollingerGrid()
    # 1. 069500 실제 또는 캐시 밴드 확인
    bands = slot_b.get_bands("069500", current_price=50000.0)
    assert bands["lower"] < bands["middle"] < bands["upper"]
    assert bands["middle"] > 0

    # 2. 미등록/실패 종목에 대한 시세 기반 동적 fallback 밴드 (±3%) 확인
    fallback_bands = slot_b.get_bands("999999", current_price=50000.0)
    assert fallback_bands["middle"] == 50000.0
    assert fallback_bands["lower"] == 48500.0  # -3.0%
    assert fallback_bands["upper"] == 51500.0  # +3.0%

    slot_d = SlotDRetestSwing()
    assert hasattr(slot_d, "selected_candidates")
    assert isinstance(slot_d.selected_candidates, list)
    assert len(slot_d.selected_candidates) > 0



