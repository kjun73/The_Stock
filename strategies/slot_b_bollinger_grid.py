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
        self.bypass_time_filter: bool = False          # 시뮬레이션 및 테스트 시 시간 필터 우회 플래그

    def update_bands(self, ticker: str, upper: float, middle: float, lower: float):
        """일봉/분봉 기반 계산된 볼린저 밴드 갱신"""
        self.bands[ticker] = {"upper": upper, "middle": middle, "lower": lower}

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

        band = self.bands.get(tick.ticker)
        if not band:
            return None

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

            # (1) 손절 (-3.5%)
            if pnl_rate <= -3.5:
                self.split_stage_tracker[tick.ticker] = 0
                self.partial_tp_tracker[tick.ticker] = False
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

            # (2) 상단선(+2σ) 전량 익절
            if bid_price >= upper:
                self.split_stage_tracker[tick.ticker] = 0
                self.partial_tp_tracker[tick.ticker] = False
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

            # (3) 중심선(20MA) 50% 분할 익절 (1회 한정)
            if bid_price >= middle and current_position.quantity >= 2 and not self.partial_tp_tracker.get(tick.ticker, False):
                half_qty = current_position.quantity // 2
                if half_qty > 0:
                    self.partial_tp_tracker[tick.ticker] = True
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="ETF",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=half_qty,
                        reason=f"볼린저 중심선 도달 50% 분할 익절 (+{pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_TAKE_PROFIT"
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
        self.split_stage_tracker[tick.ticker] = 0
        self.partial_tp_tracker[tick.ticker] = False
        if ask_price <= lower:
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

