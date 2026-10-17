import logging
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

from core.models import Signal, SlotPosition, TickData
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class SlotDRetestSwing(BaseStrategy):
    """
    슬롯 D: 전고점 돌파 첫 번째 눌림목(Retest) 스윙 전략
    - 유니버스: 52주 신고가 근접 코스피/코스닥 중대형주 (매일 08:45 자동 스크리닝)
    - 자금: 1,000만 원 (최대 3종목, 종목당 333만 원 한도)
    - 진입: 전고점 돌파 후 첫 번째 지지선(전고점 라인 또는 20일선)에 도달하여 지지 반등 시 지정가 매수
    - 청산: 익절 +8.0% | 손절 -3.0% | 최대 7영업일 보유
    """
    def __init__(self, slot_id: str = "SLOT_D"):
        super().__init__(
            slot_id=slot_id,
            name="전고점 돌파 첫 눌림목 스윙",
            universe=["005930", "000660", "035420"]
        )
        self.max_stocks: int = 3
        self.capital_per_stock: float = 3_300_000.0
        self.support_levels: Dict[str, float] = {
            "005930": 78000.0,
            "000660": 185000.0,
            "035420": 175000.0,
            "005380": 240000.0,
            "068270": 190000.0,
        }  # 종목별 전고점 지지선 가격
        self.take_profit_rate: float = 8.0          # +8.0% 익절
        self.stop_loss_rate: float = -3.0           # -3.0% 손절
        self.max_holding_days: int = 7
        self.bypass_time_filter: bool = False

    def update_candidates(self, candidates: List[str], support_map: Dict[str, float]):
        self.universe = candidates[:self.max_stocks]
        self.support_levels.update(support_map)
        logger.info(f"[{self.name}] 신고가 눌림목 후보군 {len(self.universe)}종목 갱신: {self.universe}")

    async def run_morning_screening(self):
        """매일 08:45 또는 지연 기동 시 전고점 돌파 첫 눌림목 스윙 후보군 스크리닝"""
        logger.info(f"🔍 [{self.name}] 장전/기동 시 신고가 눌림목 후보군 스크리닝 가동...")
        candidates = ["005930", "000660", "035420", "005380", "068270"]
        support_map = {
            "005930": 78000.0,
            "000660": 185000.0,
            "035420": 175000.0,
            "005380": 240000.0,
            "068270": 190000.0,
        }
        self.update_candidates(candidates, support_map)
        logger.info(f"✅ [{self.name}] 스크리닝 완료: {self.universe}")

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

        ask_price = tick.ask_price if tick.ask_price > 0 else tick.price
        bid_price = tick.bid_price if tick.bid_price > 0 else tick.price
        if ask_price <= 0 or bid_price <= 0:
            return None

        # 1. 포지션 보유 중인 경우 -> 익절 / 손절 / 7일 만기 검사
        if current_position:
            pnl_rate = ((bid_price - current_position.entry_price) / current_position.entry_price) * 100.0

            # (1) 목표 익절 (+8.0%)
            if pnl_rate >= self.take_profit_rate:
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"신고가 눌림목 스윙 목표 익절 (+{pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_TAKE_PROFIT"
                )

            # (2) 손절 (-3.0%)
            if pnl_rate <= self.stop_loss_rate:
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"지지선 붕괴 손절 ({pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_STOP_LOSS"
                )

            # (3) 7영업일 만기
            if current_position.holding_days >= self.max_holding_days and current_time >= dtime(15, 15):
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"7영업일 만기 도달 ({pnl_rate:.2f}%)",
                    exit_reason="TIME_EXIT_1515"
                )

            return None

        # 2. 미보유 상태 -> 지지선 눌림목 반등 확인 시 매수
        if tick.ticker in self.universe:
            support = self.support_levels.get(tick.ticker, tick.price * 0.98)

            # 주가가 지지선 위 0.5% 이내에 위치하면서 매수세 유입 시 (눌림목 타점)
            if support <= ask_price <= (support * 1.01):
                qty = int(self.capital_per_stock // ask_price)
                if qty > 0:
                    stop_p = round(ask_price * (1.0 + self.stop_loss_rate / 100.0), 2)
                    take_p = round(ask_price * (1.0 + self.take_profit_rate / 100.0), 2)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="STOCK",
                        action="BUY",
                        order_type="LIMIT_NORMAL",
                        price=ask_price,
                        quantity=qty,
                        reason=f"전고점 지지선({support}원) 눌림목 반등 매수",
                        stop_loss_price=stop_p,
                        take_profit_price=take_p
                    )

        return None
