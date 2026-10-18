import logging
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

from core.models import Signal, SlotPosition, TickData
from intelligence.jev_client import JevCascadeClient
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


DEFAULT_MOMENTUM_UNIVERSE = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "196170",  # 알테오젠
    "247540",  # 에코프로비엠
    "028300",  # HLB
    "068270",  # 셀트리온
    "035420",  # NAVER
    "035720",  # 카카오
    "005380",  # 현대차
    "000270"   # 기아
]


class SlotCVolatilityBreakout(BaseStrategy):
    """
    슬롯 C: 래리 윌리엄스 변동성 돌파 + JEV 1.13 초고속 필터 단타 전략
    - 유니버스: 당일 장중 거래대금/모멘텀 상위 주도주 (기본 10종목 + 실시간 확장)
    - 배정 자금: 1,000만 원 (종목당 최대 250만 원, 최대 4종목)
    - 진입: 목표가 돌파 + 1단계 로컬 정량 필터 + 2단계 JEV 판정 통과 시 시장가 진입
    - 청산: 15:15 당일 전량 청산 (오버나이트 X) | 익절: +3.5% | 손절: -2.5%
    """
    def __init__(self, jev_client: JevCascadeClient, slot_id: str = "SLOT_C"):
        super().__init__(
            slot_id=slot_id,
            name="변동성 돌파 + JEV 단타",
            universe=list(DEFAULT_MOMENTUM_UNIVERSE)
        )
        self.jev_client = jev_client
        self.target_prices: Dict[str, float] = {}       # 종목별 래리 돌파 목표가
        self.avg_volumes: Dict[str, float] = {}         # 20분 평균 거래량
        self.recent_1m_volumes: Dict[str, float] = {}   # 직전 1분 거래량
        self.max_stocks: int = 4
        self.capital_per_stock: float = 2_500_000.0
        self.bypass_time_filter: bool = False

        # 동적 트레일링 스탑 & 손절선 파라미터 (최근 고변동성 장세 최적화)
        self.high_watermarks: Dict[str, float] = {}     # 진입 후 종목별 최고가 추적
        self.trailing_active: Dict[str, bool] = {}       # 트레일링 발동 여부 (+1.5% 도달 시 True)
        self.trailing_activation_rate: float = 1.5      # +1.5% 도달 시 트레일링 활성화
        self.trailing_drop_rate: float = 1.0            # 최고가 대비 -1.0% 반락 시 익절
        self.stop_loss_rate: float = -2.8               # 일중 휩소 노이즈 방어를 위해 -2.8%로 유연화

    def update_watchlist(self, top_tickers: List[str], target_prices_map: Dict[str, float]):
        self.universe = top_tickers[:25]
        self.target_prices.update(target_prices_map)

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

        # 1. 포지션 보유 중인 경우 -> 청산(15:15 타임아웃, 트레일링 익절, 절대 손절) 검사
        if current_position:
            pnl_rate = ((bid_price - current_position.entry_price) / current_position.entry_price) * 100.0

            # (1) 15:15 당일 장마감 전량 청산 (테스트/시뮬레이션 우회 지원)
            if not self.bypass_time_filter and current_time >= dtime(15, 15):
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="MARKET_BREAKOUT",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"15:15 당일 단타 전량 청산 ({pnl_rate:.2f}%)",
                    exit_reason="TIME_EXIT_1515"
                )

            # 최고가(High-Water Mark) 추적
            if tick.ticker not in self.high_watermarks:
                self.high_watermarks[tick.ticker] = max(current_position.entry_price, bid_price)
            else:
                if bid_price > self.high_watermarks[tick.ticker]:
                    self.high_watermarks[tick.ticker] = bid_price

            high_price = self.high_watermarks[tick.ticker]

            # 트레일링 모드 활성화 검사 (+1.5% 도달 시)
            if pnl_rate >= self.trailing_activation_rate:
                self.trailing_active[tick.ticker] = True

            # (2) 동적 트레일링 익절: 트레일링 활성화 상태에서 최고가 대비 1.0% 이상 반락 시 이익 확정
            if self.trailing_active.get(tick.ticker, False):
                drop_from_high = ((high_price - bid_price) / high_price) * 100.0
                if drop_from_high >= self.trailing_drop_rate:
                    self.high_watermarks.pop(tick.ticker, None)
                    self.trailing_active.pop(tick.ticker, None)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="STOCK",
                        action="SELL",
                        order_type="MARKET_BREAKOUT",
                        price=bid_price,
                        quantity=current_position.quantity,
                        reason=f"트레일링 익절 발동 (최고가 {high_price:,.0f}원 대비 -{drop_from_high:.2f}% 반락, 확정손익률 +{pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_TRAILING_STOP"
                    )

            # (3) 하방 절대 손절 (-2.8%)
            if pnl_rate <= self.stop_loss_rate:
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="MARKET_BREAKOUT",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"단타 절대 손절선 이탈 ({pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_STOP_LOSS"
                )

            return None

        # 2. 미보유 상태 -> 목표가 돌파 및 2단계 JEV 캐스케이드 검사
        # 14:30 이후에는 신규 단타 진입 금지 (테스트/시뮬레이션 시 우회 허용)
        if not self.bypass_time_filter and (current_time >= dtime(14, 30) or current_time < dtime(9, 5)):
            return None

        # 종목별 당일 시초가 기록 및 변동성 돌파 목표가 산출
        if not hasattr(self, "open_prices"):
            self.open_prices = {}
        if tick.ticker not in self.open_prices:
            self.open_prices[tick.ticker] = tick.price

        open_p = self.open_prices[tick.ticker]
        # 래리 윌리엄스 변동성 돌파: 시초가 대비 +1.0% 상승 돌파 시 매수 신호
        target_price = self.target_prices.get(tick.ticker, round(open_p * 1.01, 2))
        avg_vol = self.avg_volumes.get(tick.ticker, 1000.0)
        vol_1m = self.recent_1m_volumes.get(tick.ticker, max(float(tick.volume), 3000.0))

        # 1단계: 로컬 정량 필터 (0.1ms, $0)
        passed_stage1, reason1 = self.jev_client.check_stage1_filter(
            tick=tick,
            target_price=target_price,
            avg_vol_20m=avg_vol,
            vol_1m=vol_1m
        )
        if not passed_stage1:
            return None

        # 2단계: JEV 1.13 초고속 API 호출 (700ms 타임아웃)
        surge_ratio = vol_1m / max(avg_vol, 1.0)
        is_approved, fakeout_prob, reason2 = await self.jev_client.evaluate_stage2_jev(
            ticker=tick.ticker,
            tick=tick,
            target_price=target_price,
            surge_ratio=surge_ratio
        )

        if is_approved:
            qty = int(self.capital_per_stock // ask_price)
            if qty > 0:
                logger.info(f"⚡ [슬롯 C 진입 승인] {tick.ticker} (가짜돌파확률: {fakeout_prob:.2f}) -> {qty}주 매수")
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="BUY",
                    order_type="MARKET_BREAKOUT",
                    price=ask_price,
                    quantity=qty,
                    reason=f"변동성 돌파 및 JEV 승인 ({reason2})",
                    stop_loss_price=round(ask_price * 0.972, 2),
                    take_profit_price=None
                )

        return None
