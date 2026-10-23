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
        self.selected_candidates: List[str] = ["005930", "000660", "035420"]
        super().__init__(
            slot_id=slot_id,
            name="전고점 돌파 첫 눌림목 스윙",
            universe=list(self.selected_candidates)
        )
        self.max_stocks: int = 3
        self.capital_per_stock: float = 3_300_000.0
        self.support_levels: Dict[str, float] = {}  # 종목별 전고점 지지선 가격 (실시간 시세 기반 동적 자동 캘리브레이션)
        self.take_profit_rate: float = 8.0          # +8.0% 목표 익절
        self.stop_loss_rate: float = -3.0           # -3.0% 초기 손절
        self.max_holding_days: int = 7
        self.bypass_time_filter: bool = False

        # 동적 트레일링 스탑 & 본전 보존 파라미터
        self.high_watermarks: Dict[str, float] = {}     # 진입 후 최고가 추적
        self.trailing_active: Dict[str, bool] = {}       # +3.5% 도달 시 트레일링 활성화
        self.trailing_activation_rate: float = 3.5      # +3.5% 도달 시 발동
        self.trailing_drop_rate: float = 1.5            # 최고가 대비 -1.5% 반락 시 익절 확정
        self.stopped_out_tickers: set[str] = set()      # 당일 손절 발생 종목 재진입 차단 집합
        self.day_open_prices: Dict[str, float] = {}     # 당일 시초가 기록 (하락 칼날 방어용)

    def update_candidates(self, candidates: List[str], support_map: Optional[Dict[str, float]] = None):
        self.selected_candidates = list(candidates[:self.max_stocks])
        self.universe = list(self.selected_candidates)
        if support_map:
            self.support_levels.update(support_map)
        logger.info(f"[{self.name}] 신고가 눌림목 후보군 {len(self.universe)}종목 갱신: {self.universe}")

    async def run_morning_screening(self, exclude_tickers: Optional[List[str]] = None):
        """매일 08:45 또는 지연 기동 시 전고점 돌파 첫 눌림목 스윙 후보군 및 20MA 지지선 스크리닝 (슬롯 A 중복 배제)"""
        logger.info(f"🔍 [{self.name}] 장전/기동 시 신고가 눌림목 후보군 스크리닝 가동...")
        excluded = set(exclude_tickers or [])
        default_pool = ["035420", "068270", "012450", "267260", "196170", "000270", "005930", "000660", "005380"]
        candidates = [t for t in default_pool if t not in excluded][:5]
        if len(candidates) < self.max_stocks:
            candidates = default_pool[:5]

        support_map: Dict[str, float] = {}
        try:
            from pykrx import stock
            from datetime import timedelta
            today = datetime.now()
            today_str = today.strftime("%Y%m%d")
            start_str = (today - timedelta(days=40)).strftime("%Y%m%d")

            # 1순위: KRX 공식 시총 상위 조회 (로그인 세션 활성 시)
            try:
                market_cap = stock.get_market_cap_by_ticker(today_str)
                if market_cap is not None and not market_cap.empty and "시가총액" in market_cap.columns:
                    top_tickers = [
                        t for t in market_cap.sort_values(by="시가총액", ascending=False).index[:15]
                        if t not in excluded
                    ]
                    if len(top_tickers) >= self.max_stocks:
                        candidates = top_tickers[:5]
            except Exception:
                pass

            # 2순위: 후보 풀 대상 20일 고점 근접도 및 20MA 지지선 정밀 산출
            scored_candidates = []
            for t in candidates:
                df = stock.get_market_ohlcv_by_date(start_str, today_str, t)
                if df is not None and len(df) >= 5 and "종가" in df.columns:
                    closes = df["종가"].tail(20)
                    ma20 = float(closes.mean())
                    high20 = float(closes.max())
                    last_c = float(closes.iloc[-1])
                    support_map[t] = round(ma20, 0)
                    near_high_ratio = last_c / high20 if high20 > 0 else 0.0
                    scored_candidates.append((t, near_high_ratio))

            if scored_candidates:
                scored_candidates.sort(key=lambda x: x[1], reverse=True)
                candidates = [x[0] for x in scored_candidates[:self.max_stocks]]
        except Exception as e:
            logger.info(f"[{self.name}] pykrx 동적 스크리닝 폴백 (사유: {e})")

        self.update_candidates(candidates, support_map if support_map else None)
        logger.info(f"✅ [{self.name}] 스크리닝 완료: {self.universe} (지지선: {self.support_levels})")

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

        # 1. 포지션 보유 중인 경우 -> 목표익절 / 트레일링익절 / 본전보존 / 손절 / 7일 만기 검사
        if current_position:
            pnl_rate = ((bid_price - current_position.entry_price) / current_position.entry_price) * 100.0

            # (1) 목표 익절 (+8.0% 이상 도달 시 전량 익절)
            if pnl_rate >= self.take_profit_rate:
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
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

            # 최고가(High-Water Mark) 추적
            if tick.ticker not in self.high_watermarks:
                self.high_watermarks[tick.ticker] = max(current_position.entry_price, bid_price)
            else:
                if bid_price > self.high_watermarks[tick.ticker]:
                    self.high_watermarks[tick.ticker] = bid_price

            high_price = self.high_watermarks[tick.ticker]

            # 트레일링 모드 활성화 검사 (+3.5% 도달 시 또는 오버나잇 복원 시)
            if pnl_rate >= self.trailing_activation_rate or current_position.stop_loss_price >= current_position.entry_price:
                self.trailing_active[tick.ticker] = True

            # (2) 동적 트레일링 익절: 최고가 대비 1.5% 이상 반락 시 익절 확정
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
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=current_position.quantity,
                        reason=f"눌림목 트레일링 익절 발동 (최고가 {high_price:,.0f}원 대비 -{drop_from_high:.2f}% 반락, 확정손익률 +{pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_TRAILING_STOP"
                    )

                # 트레일링 활성화 상태에서 본전(0.0%) 밑으로 내려가면 본전 보존
                if pnl_rate <= 0.0:
                    self.high_watermarks.pop(tick.ticker, None)
                    self.trailing_active.pop(tick.ticker, None)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="STOCK",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=current_position.quantity,
                        reason=f"눌림목 수익 후 본전(0.0%) 보존 손절 ({pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_BREAKEVEN_STOP"
                    )

            # (3) 초기 지지선 붕괴 손절 (-3.0%) -> 당일 동일 종목 재진입 원천 차단
            if pnl_rate <= self.stop_loss_rate:
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
                self.stopped_out_tickers.add(tick.ticker)
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

            # (4) 7영업일 만기
            if current_position.holding_days >= self.max_holding_days and (self.bypass_time_filter or current_time >= dtime(15, 15)):
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
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

        # 2. 미보유 상태 -> 당일 손절 이력이 없고 지지선 눌림목 반등 확인 시 매수
        if tick.ticker in self.stopped_out_tickers:
            return None

        if tick.ticker in self.universe:
            day_open = self.day_open_prices.setdefault(tick.ticker, tick.price)
            support = self.support_levels.get(tick.ticker)
            # 지지선이 미설정되었거나 비정상 괴리(>25%)된 경우에만 초기 지지선(-2.0%) 1회 설정 (장중 하락 시 지지선 하향 이동 방지)
            if support is None or abs(support - tick.price) / tick.price > 0.25:
                support = round(tick.price * 0.98, 0)
                self.support_levels[tick.ticker] = support

            # 하락 칼날 방어: 당일 시초가 대비 -0.8% 이상 급락 중이거나 체결강도가 95% 미만이면 진입 보류
            if tick.price < (day_open * 0.992) or tick.exec_strength < 95.0:
                return None

            # 주가가 지지선 위 1.5% 이내에 위치하면서 매수세 유입 시 (눌림목 타점)
            if support <= ask_price <= (support * 1.015):
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
                        reason=f"전고점 지지선({support:,.0f}원) 눌림목 반등 매수",
                        stop_loss_price=stop_p,
                        take_profit_price=take_p
                    )

        return None

    def reset_daily_state(self):
        """매일 장 시작 전(08:45) 당일 손절 블랙리스트 및 시초가 초기화"""
        self.stopped_out_tickers.clear()
        self.day_open_prices.clear()
