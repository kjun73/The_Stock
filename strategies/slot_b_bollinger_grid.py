from datetime import datetime, time as dtime
import logging
from typing import Dict, Optional

from core.models import Signal, SlotPosition, TickData
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class SlotBBollingerGrid(BaseStrategy):
    """
    슬롯 B: KODEX 현물 ETF 볼린저 밴드 그리드 매매 전략
    - 유니버스: KODEX 200 (069500), KODEX 코스닥150 (229200) [ETF 거래세 0% 면제]
    - 자금: 1,000만 원 (종목당 500만 원 한도)
    - 3분할 매수:
      * 1차: 볼린저 하단(-2σ) 도달 시 30% (150만 원)
      * 2차: 추가 하락(-1.5%) 시 30% (150만 원)
      * 3차: 반등 확인 시 40% (200만 원)
    - 익절: 20MA 중심선 도달 시 50% 익절, 상단(+2σ) 도달 시 잔여 전량 익절
    - 손절: -3.5%
    """
    def __init__(self, slot_id: str = "SLOT_B"):
        super().__init__(
            slot_id=slot_id,
            name="KODEX 현물 ETF 볼린저 그리드",
            universe=["069500", "229200"]
        )
        self.allocated_capital_per_stock: float = 5_000_000.0
        # 종목별 볼린저 밴드 지표 캐시 (상단, 중심, 하단)
        self.bands: Dict[str, Dict[str, float]] = {
            "069500": {"upper": 37000.0, "middle": 35500.0, "lower": 34000.0},
            "229200": {"upper": 15000.0, "middle": 14200.0, "lower": 13500.0},
        }
        self.split_stage_tracker: Dict[str, int] = {}  # 종목별 현재 분할 단계 (0, 1, 2, 3)
        self.partial_tp_tracker: Dict[str, bool] = {}  # 종목별 중심선 50% 분할익절 완료 여부
        self.breakeven_tracker: Dict[str, bool] = {}   # 1차 익절 후 본전 보존 활성화 여부
        self.partial_tp_rate: float = 2.5              # 지수 ETF +2.5% 도달 시 중심선 미달이어도 1차 익절
        self.bypass_time_filter: bool = False          # 시뮬레이션 및 테스트 시 시간 필터 우회 플래그
        self._last_band_calc_time: Dict[str, float] = {}  # pykrx 과도 호출 방지 TTL 캐시

    def update_bands(self, ticker: str, upper: float, middle: float, lower: float):
        """일봉/분봉 기반 계산된 볼린저 밴드 갱신"""
        self.bands[ticker] = {"upper": upper, "middle": middle, "lower": lower}

    def calculate_bollinger_bands_from_history(self, ticker: str) -> Optional[Dict[str, float]]:
        """pykrx 최근 20영업일 종가 데이터 기반 정밀 볼린저 밴드(20, 2) 계산"""
        try:
            from pykrx import stock
            from datetime import timedelta
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=45)).strftime("%Y%m%d")
            df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
            if df is not None and len(df) >= 20:
                closes = df["종가"].tail(20)
                ma20 = float(closes.mean())
                std20 = float(closes.std())
                upper = round(ma20 + (2.0 * std20), 1)
                lower = round(ma20 - (2.0 * std20), 1)
                middle = round(ma20, 1)
                self.bands[ticker] = {"upper": upper, "middle": middle, "lower": lower}
                logger.info(f"📊 [{self.name}] {ticker} pykrx 실증 볼린저 밴드(20, 2) 산출 완료: 상단 {upper} / 중심 {middle} / 하단 {lower}")
                return self.bands[ticker]
        except Exception as e:
            logger.info(f"[{self.name}] pykrx 볼린저 밴드 계산 폴백 ({ticker}): {e}")
        return None

    def get_bands(self, ticker: str, current_price: Optional[float] = None) -> Dict[str, float]:
        """볼린저 밴드 조회 (없거나 괴리율이 클 경우 5분 TTL 내 pykrx 실증 계산 또는 동적 적응)"""
        band = self.bands.get(ticker)
        now_ts = datetime.now().timestamp()
        last_calc = self._last_band_calc_time.get(ticker, 0.0)

        if current_price and current_price > 0:
            if not band or (abs(band["middle"] - current_price) / current_price > 0.10 and (now_ts - last_calc) > 300.0):
                self._last_band_calc_time[ticker] = now_ts
                band = self.calculate_bollinger_bands_from_history(ticker)
                if not band:
                    mid = current_price
                    self.bands[ticker] = {
                        "upper": round(mid * 1.03, 1),
                        "middle": mid,
                        "lower": round(mid * 0.97, 1)
                    }
                    band = self.bands[ticker]
        return band or {"upper": 0.0, "middle": 0.0, "lower": 0.0}

    async def evaluate_market(
        self,
        tick: TickData,
        current_position: Optional[SlotPosition] = None
    ) -> Optional[Signal]:
        now = datetime.now()
        current_time = now.time()

        # 정규장 시간(09:00 ~ 15:20) 외 장전/장후에는 신규 매수 원천 차단
        if not self.bypass_time_filter:
            if current_time < dtime(9, 0) or current_time > dtime(15, 20):
                if not current_position:
                    return None

        if tick.ticker not in self.universe:
            return None

        band = self.get_bands(tick.ticker, tick.price)
        lower = band["lower"]
        middle = band["middle"]
        upper = band["upper"]

        ask_price = tick.ask_price if tick.ask_price > 0 else tick.price
        bid_price = tick.bid_price if tick.bid_price > 0 else tick.price
        if ask_price <= 0 or bid_price <= 0:
            return None

        # 1. 포지션 보유 중인 경우 -> 익절 및 추가 분할 매수 / 손절 검사
        if current_position:
            pnl_rate = ((bid_price - current_position.entry_price) / current_position.entry_price) * 100.0

            has_done_partial_tp = (
                self.partial_tp_tracker.get(tick.ticker, False)
                or (current_position.stop_loss_price >= current_position.entry_price)
            )
            is_breakeven_active = (
                self.breakeven_tracker.get(tick.ticker, False)
                or (current_position.stop_loss_price >= current_position.entry_price)
            )

            # (1) 손절: 1차 익절 완료 시 본전(+0.15%) 보존 손절, 미완료 시 초기 손절(-3.5%)
            if is_breakeven_active:
                if pnl_rate <= 0.15:
                    self.split_stage_tracker[tick.ticker] = 0
                    self.partial_tp_tracker.pop(tick.ticker, None)
                    self.breakeven_tracker.pop(tick.ticker, None)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="ETF",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=current_position.quantity,
                        reason=f"볼린저 1차 익절 후 본전(+0.15%) 보존 손절 ({pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_BREAKEVEN_STOP"
                    )
            else:
                if pnl_rate <= -3.5:
                    self.split_stage_tracker[tick.ticker] = 0
                    self.partial_tp_tracker.pop(tick.ticker, None)
                    self.breakeven_tracker.pop(tick.ticker, None)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="ETF",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=current_position.quantity,
                        reason=f"볼린저 하방 이탈 손절 ({pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_STOP_LOSS"
                    )

            # (2) 상단선(+2σ) 또는 +5.0% 이상 전량 익절
            if bid_price >= upper or pnl_rate >= 5.0:
                self.split_stage_tracker[tick.ticker] = 0
                self.partial_tp_tracker.pop(tick.ticker, None)
                self.breakeven_tracker.pop(tick.ticker, None)
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="ETF",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"볼린저 상단(+2σ) 도달 전량 익절 (+{pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_TAKE_PROFIT"
                )

            # (3) 중심선(20MA) 또는 목표수익률(+2.5%) 도달 시 50% 1차 분할 익절 & 본전 손절선 상향 (1회 한정)
            if (bid_price >= middle or pnl_rate >= self.partial_tp_rate) and current_position.quantity >= 2 and not has_done_partial_tp:
                half_qty = current_position.quantity // 2
                if half_qty > 0:
                    self.partial_tp_tracker[tick.ticker] = True
                    self.breakeven_tracker[tick.ticker] = True
                    new_sl = round(current_position.entry_price * 1.0015, 2)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="ETF",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=half_qty,
                        reason=f"볼린저 중심선 도달 50% 분할 익절 (+{pnl_rate:.2f}%)",
                        stop_loss_price=new_sl,
                        exit_reason="STRATEGY_PARTIAL_TP"
                    )

            # (4) 2차 / 3차 추가 분할 매수 검사
            current_stage = self.split_stage_tracker.get(tick.ticker, 1)

            # 2차 매수: 1차 진입가 대비 -1.5% 추가 하락 시 (30%, 150만 원)
            if current_stage == 1 and pnl_rate <= -1.5:
                qty = int(1_500_000 // ask_price)
                if qty > 0:
                    self.split_stage_tracker[tick.ticker] = 2
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="ETF",
                        action="BUY",
                        order_type="SPLIT_BUY_2",
                        price=ask_price,
                        quantity=qty,
                        reason="볼린저 하단 추가 하락 2차 30% 분할 매수",
                        stop_loss_price=round(ask_price * 0.965, 2)
                    )

            # 3차 매수: 2차 매수 후 평단가 대비 반등 시 (40%, 200만 원)
            elif current_stage == 2 and pnl_rate >= 0.5:
                qty = int(2_000_000 // ask_price)
                if qty > 0:
                    self.split_stage_tracker[tick.ticker] = 3
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="ETF",
                        action="BUY",
                        order_type="SPLIT_BUY_3",
                        price=ask_price,
                        quantity=qty,
                        reason="볼린저 하단 반등 확인 3차 40% 분할 매수",
                        stop_loss_price=round(ask_price * 0.965, 2)
                    )

            return None

        # 2. 미보유 상태 -> 하단선(-2σ) 터치 시 1차 30% 분할 매수
        if self.split_stage_tracker.get(tick.ticker, 0) >= 1:
            return None  # 이미 당일 1차 매수 주문이 제출되어 체결 대기 중이면 중복 매수 차단

        if lower > 0 and ask_price <= lower:
            qty = int(1_500_000 // ask_price)  # 30% (150만 원)
            if qty > 0:
                self.split_stage_tracker[tick.ticker] = 1
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="ETF",
                    action="BUY",
                    order_type="SPLIT_BUY_1",
                    price=ask_price,
                    quantity=qty,
                    reason="볼린저 하단(-2σ) 터치 1차 30% 분할 매수",
                    stop_loss_price=round(ask_price * 0.965, 2)
                )

        return None

    def reset_daily_state(self):
        """매일 장 시작 전(08:45) 일일 상태 초기화"""
        self.split_stage_tracker.clear()
        self.partial_tp_tracker.clear()
        self.breakeven_tracker.clear()

