from datetime import datetime, time as dtime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest

from core.models import TickData
from intelligence.gemini_client import GeminiAnalyst
from intelligence.jev_client import JevCascadeClient
from intelligence.us_market import (
    USMarketMonitor,
    classify_us_regime,
    is_us_data_fresh,
)
from strategies.market_filter import MarketFilterManager
from strategies.slot_c_volatility_breakout import SlotCVolatilityBreakout

KST = timezone(timedelta(hours=9))


def test_us_regime_classification():
    """미국장 4단계 레짐 판정 및 오래된 데이터(is_fresh=False) 중립 폴백 검증"""
    assert classify_us_regime(-2.0, -5.5, -2.5, 22.0, is_fresh=True) == "US_PANIC"
    assert classify_us_regime(-1.0, -2.0, -3.2, 20.0, is_fresh=True) == "US_PANIC"
    assert classify_us_regime(-0.5, -1.0, -0.5, 29.0, is_fresh=True) == "US_PANIC"

    assert classify_us_regime(-0.8, -3.4, -1.2, 20.0, is_fresh=True) == "US_RISK_OFF"
    assert classify_us_regime(-0.5, -1.0, -1.8, 19.0, is_fresh=True) == "US_RISK_OFF"
    assert classify_us_regime(-1.8, -1.2, -0.4, 19.0, is_fresh=True) == "US_RISK_OFF"

    assert classify_us_regime(1.2, 2.5, 1.8, 16.0, is_fresh=True) == "US_RISK_ON"
    assert classify_us_regime(0.3, 0.5, -0.2, 18.0, is_fresh=True) == "US_NEUTRAL"

    # 미국 휴장일 등으로 데이터가 오래된 경우 US_PANIC 조건이어도 US_NEUTRAL로 폴백
    assert classify_us_regime(-4.0, -7.0, -5.0, 32.0, is_fresh=False) == "US_NEUTRAL"


def test_us_data_freshness_weekend_and_holiday():
    """월요일 아침(금요일 종가 허용) 및 미국 휴장일(화요일에 금요일 종가 걸러냄) 신선도 검증"""
    fri_open_ts = int(datetime(2026, 11, 13, 23, 30, tzinfo=KST).timestamp())
    mon_open_ts = int(datetime(2026, 11, 16, 23, 30, tzinfo=KST).timestamp())

    # 1. 한국 월요일 08:45 -> 금요일 미국장 허용 (Fresh)
    kr_mon_morning = datetime(2026, 11, 16, 8, 45, tzinfo=KST)
    assert is_us_data_fresh(fri_open_ts, now_kst=kr_mon_morning) is True

    # 2. 한국 화요일 08:45에 월요일 휴장(최신 데이터가 금요일) -> Stale 처리
    kr_tue_morning = datetime(2026, 11, 17, 8, 45, tzinfo=KST)
    assert is_us_data_fresh(fri_open_ts, now_kst=kr_tue_morning) is False

    # 3. 한국 화요일 08:45에 월요일 정상 개장 데이터 -> Fresh
    assert is_us_data_fresh(mon_open_ts, now_kst=kr_tue_morning) is True


def test_market_filter_with_us_regimes():
    """마켓필터의 US 레짐별(PANIC / RISK_OFF / RISK_ON) 슬롯 차별화 제어 검증"""
    # 코스피 20일선 상방 기준
    mf = MarketFilterManager(base_index=2600.0, ma20=2550.0)
    mf.kospi_daily_change_pct = 0.2

    # 1. US_PANIC: A/D 당일 보류, B는 09:20 이전만 관망
    mf.update_us_market("US_PANIC", "SOX -7.2%, EWY -5.5%")
    assert mf.can_enter("SLOT_A", current_time=dtime(10, 0)) is False
    assert mf.can_enter("SLOT_D", current_time=dtime(10, 0)) is False
    assert mf.can_enter("SLOT_B", current_time=dtime(9, 10)) is False
    assert mf.can_enter("SLOT_B", current_time=dtime(9, 25)) is True

    # 2. US_RISK_OFF: A/D는 09:00~09:30 투매 지연, 09:30 이후 허용
    mf.update_us_market("US_RISK_OFF", "EWY -1.8%")
    assert mf.can_enter("SLOT_A", current_time=dtime(9, 15)) is False
    assert mf.can_enter("SLOT_D", current_time=dtime(9, 29)) is False
    assert mf.can_enter("SLOT_A", current_time=dtime(9, 30)) is True
    assert mf.can_enter("SLOT_B", current_time=dtime(9, 5)) is True

    # 3. US_RISK_ON: 코스피 20일선 아래여도 +0.3% 이상이면 SLOT_C 조기 허용
    mf_bear = MarketFilterManager(base_index=2500.0, ma20=2600.0)
    mf_bear.kospi_daily_change_pct = 0.4
    assert mf_bear.can_enter("SLOT_C", current_time=dtime(9, 15)) is False  # US_NEUTRAL 시 차단
    mf_bear.update_us_market("US_RISK_ON", "SOX +4.0%, EWY +3.0%")
    assert mf_bear.can_enter("SLOT_C", current_time=dtime(9, 15)) is True


@pytest.mark.asyncio
async def test_slot_c_us_regime_strength_and_inverse(monkeypatch):
    """슬롯 C: US 약세장 시 롱 종목 체결강도 문턱 상향(120~125%), 인버스 ETF(252670)는 110% 유지 검증"""
    monkeypatch.setattr("intelligence.jev_client.settings.OPENROUTER_API_KEY", "")
    jev = JevCascadeClient()
    slot_c = SlotCVolatilityBreakout(jev_client=jev)
    slot_c.bypass_time_filter = True

    # 1. US_RISK_OFF 상태에서 233740(레버리지 롱) 체결강도 115% -> 120% 미달 차단
    slot_c.set_us_context("US_RISK_OFF", "SOX -3.4%, EWY -1.8%")
    slot_c.prev_ranges["233740"] = 200.0
    await slot_c.evaluate_market(TickData(ticker="233740", price=10000.0, bid_price=9990.0, ask_price=10000.0, volume=1000), None)
    tick_long_weak = TickData(ticker="233740", price=10150.0, bid_price=10140.0, ask_price=10150.0, volume=2500, exec_strength=115.0)
    passed, reason = jev.check_stage1_filter(tick_long_weak, 10100.0, 1000.0, 2500.0, min_exec_strength=120.0)
    assert passed is False and "EXEC_STRENGTH_LOW" in reason
    sig_long_weak = await slot_c.evaluate_market(tick_long_weak, None)
    assert sig_long_weak is None

    # 2. US_PANIC 상태에서 252670(인버스2X)는 1단계 체결강도 110% 유지 (112%도 1단계 통과)
    tick_inv_112 = TickData(ticker="252670", price=2030.0, bid_price=2025.0, ask_price=2030.0, volume=2500, exec_strength=112.0)
    passed_inv, reason_inv = jev.check_stage1_filter(tick_inv_112, 2020.0, 1000.0, 2500.0, min_exec_strength=110.0)
    assert passed_inv is True and reason_inv == "STAGE1_PASSED"

    slot_c.set_us_context("US_PANIC", "SOX -7.2%, EWY -5.5%")
    slot_c.prev_ranges["252670"] = 40.0
    await slot_c.evaluate_market(TickData(ticker="252670", price=2000.0, bid_price=1995.0, ask_price=2000.0, volume=1000), None)
    sig_inv = await slot_c.evaluate_market(
        TickData(ticker="252670", price=2030.0, bid_price=2025.0, ask_price=2030.0, volume=2500, exec_strength=121.0),
        None
    )
    assert sig_inv is not None
    assert sig_inv.ticker == "252670"
    assert sig_inv.asset_type == "ETF"

    # 3. 같은 US_PANIC 상태에서 롱 종목(122630)은 체결강도 121%여도 125% 미달로 차단
    slot_c.prev_ranges["122630"] = 200.0
    await slot_c.evaluate_market(TickData(ticker="122630", price=20000.0, bid_price=19990.0, ask_price=20000.0, volume=1000), None)
    sig_long_panic = await slot_c.evaluate_market(
        TickData(ticker="122630", price=20200.0, bid_price=20190.0, ask_price=20200.0, volume=2500, exec_strength=121.0),
        None
    )
    assert sig_long_panic is None


@pytest.mark.asyncio
async def test_us_market_monitor_fallback_and_briefing(tmp_path, monkeypatch):
    """네트워크 장애 시 US_NEUTRAL 안전 폴백 및 장전 브리핑 미국장 섹션 렌더링 검증"""
    monitor = USMarketMonitor(timeout_sec=1.0)
    with patch.object(monitor, "_fetch_single_symbol", new_callable=AsyncMock, side_effect=RuntimeError("network down")):
        snap = await monitor.fetch_snapshot()
        assert snap.regime == "US_NEUTRAL"
        assert snap.is_fresh is False

    # 임시 디렉터리에서 브리핑 포맷 검증
    monkeypatch.setattr("intelligence.gemini_client.REPORT_DIR", tmp_path)
    monkeypatch.setattr("intelligence.gemini_client.REPORT_HISTORY_DIR", tmp_path / "history")
    (tmp_path / "history").mkdir(parents=True, exist_ok=True)

    analyst = GeminiAnalyst()
    analyst.api_key = ""  # 로컬 룰 기반 포맷 테스트
    briefing = await analyst.generate_morning_briefing({
        "kospi_index": 5300.0,
        "kospi_20ma": 5500.0,
        "kospi_daily_change_pct": -0.5,
        "us_market": {
            "regime": "US_RISK_OFF",
            "summary_line": "NASDAQ -0.91%, SOX -3.43%, EWY -1.79%, S&P500 -0.40%, VIX 20.6, USD/KRW 1,334원 (US_RISK_OFF)"
        }
    }, force_regenerate=True)

    assert "전일 미국 증시" in briefing
    assert "US_RISK_OFF" in briefing
    assert "09:00~09:30" in briefing
