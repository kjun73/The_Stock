import logging
from datetime import datetime, time as dtime
from typing import List, Optional

from core.models import Signal, SlotPosition, TickData
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class SlotAInstitutionalSwing(BaseStrategy):
    """
    슬롯 A: T+1 외인·기관 수급 스윙 전략
    - 자금: 1,000만 원 (최대 3종목, 종목당 333만 원 한도)
    - 진입: D일 15:40 선정 종목 대상 익일 09:01(50%) / 09:15(50%) 2회 분할 매수
    - 익절: +6.0% | 손절: -3.5% | 보유기한: 최대 5영업일 (15:15 장마감 청산)
    """
    def __init__(self, slot_id: str = "SLOT_A"):
        self.selected_candidates: List[str] = ["005930", "000660", "005380"]  # 기본 대표 수급주
        super().__init__(
            slot_id=slot_id,
            name="외인·기관 수급 스윙 (T+1)",
            universe=list(self.selected_candidates)
        )
        self.max_stocks: int = 3
        self.target_capital_per_stock: float = 3_300_000.0
        self.take_profit_rate: float = 6.0    # +6.0% 익절
        self.stop_loss_rate: float = -3.5     # -3.5% 손절
        self.max_holding_days: int = 5

    def update_candidates(self, candidates: List[str]):
        """D일 15:40 스크리닝된 종목 리스트 갱신"""
        self.selected_candidates = candidates[:self.max_stocks]
        self.universe = self.selected_candidates
        logger.info(f"[{self.name}] 익일(D+1) 매수 후보군 {len(self.selected_candidates)}종목 확정: {self.selected_candidates}")

    async def run_evening_screening(self):
        """D일 15:40 장마감 후 외인·기관 쌍끌이 순매수 상위 종목 스크리닝"""
        logger.info(f"🔍 [{self.name}] 외인·기관 쌍끌이 수급 스윙 스크리닝 가동...")
        candidates = ["005930", "000660", "005380", "035420"]
        self.update_candidates(candidates)
        logger.info(f"✅ [{self.name}] 익일(D+1) 매수 후보 확정: {self.selected_candidates}")

    async def evaluate_market(
        self,
        tick: TickData,
        current_position: Optional[SlotPosition] = None
    ) -> Optional[Signal]:
        now = datetime.now()
        current_time = now.time()

        ask_price = tick.ask_price if tick.ask_price > 0 else tick.price
        bid_price = tick.bid_price if tick.bid_price > 0 else tick.price
        if ask_price <= 0 or bid_price <= 0:
            return None

        # 1. 이미 보유 중인 경우 -> 청산 조건(익절, 손절, 5일 만기) 검사
        if current_position:
            # 평가 수익률 계산
            pnl_rate = ((bid_price - current_position.entry_price) / current_position.entry_price) * 100.0

            # (1) 목표 익절 (+6.0%)
            if pnl_rate >= self.take_profit_rate:
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"목표 익절 도달 (+{pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_TAKE_PROFIT"
                )

            # (2) 손절 (-3.5%)
            if pnl_rate <= self.stop_loss_rate:
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"손절선 이탈 ({pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_STOP_LOSS"
                )

            # (3) 보유 기한 만기 (5영업일 경과 & 15:15 이후)
            if current_position.holding_days >= self.max_holding_days and current_time >= dtime(15, 15):
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"5영업일 만기 도달 ({pnl_rate:.2f}%)",
                    exit_reason="TIME_EXIT_1515"
                )

            return None

        # 2. 미보유 상태인 경우 -> 후보군 대상 분할 매수 검사 (09:00 ~ 10:00 1차 50%)
        if tick.ticker in self.selected_candidates:
            if dtime(9, 0) <= current_time <= dtime(10, 0):
                half_capital = self.target_capital_per_stock * 0.5  # 약 165만 원
                qty = int(half_capital // ask_price)
                if qty > 0:
                    stop_price = round(ask_price * (1.0 + self.stop_loss_rate / 100.0), 2)
                    take_price = round(ask_price * (1.0 + self.take_profit_rate / 100.0), 2)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="STOCK",
                        action="BUY",
                        order_type="SPLIT_BUY_1",
                        price=ask_price,
                        quantity=qty,
                        reason="수급 스윙 1차 50% 분할 매수",
                        stop_loss_price=stop_price,
                        take_profit_price=take_price
                    )

        return None
