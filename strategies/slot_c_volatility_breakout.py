import logging
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

from core.models import Signal, SlotPosition, TickData
from intelligence.jev_client import JevCascadeClient
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


ETF_UNIVERSE_SET = {"069500", "229200", "122630", "233740", "114800", "252670"}
INVERSE_ETF_SET = {"114800", "252670"}

DEFAULT_MOMENTUM_UNIVERSE = [
    "233740",  # KODEX 코스닥150레버리지 (거래세 0% 면제, 최고 탄력 ETF)
    "122630",  # KODEX 레버리지 (거래세 0% 면제)
    "252670",  # KODEX 200선물인버스2X (거래세 0% 면제, 헷지/하락 변동성)
    "196170",  # 알테오젠 (코스닥 시총 1위 바이오 주도주)
    "247540",  # 에코프로비엠 (2차전지 모멘텀)
    "086520",  # 에코프로 (2차전지 지주사 탄력주)
    "028300",  # HLB (코스닥 바이오 모멘텀)
    "068270",  # 셀트리온 (바이오 대표주)
    "267260",  # HD현대일렉트릭 (AI 전력인프라 주도주)
    "277810",  # 두산로보틱스 (로봇 모멘텀)
    "041510",  # 에스엠 (엔터 모멘텀)
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
        self.k_factor: float = 0.5                      # 래리 윌리엄스 표준 k계수 (k=0.5)
        self.target_prices: Dict[str, float] = {}       # 종목별 래리 돌파 목표가
        self.open_prices: Dict[str, float] = {}         # 종목별 당일 시초가
        self.prev_ranges: Dict[str, float] = {}         # 종목별 전일 변동폭 (고가 - 저가)
        self.avg_volumes: Dict[str, float] = {}         # 20분 평균 거래량
        self.recent_1m_volumes: Dict[str, float] = {}   # 직전 1분 거래량
        self.max_stocks: int = 4
        self.capital_per_stock: float = 2_500_000.0
        self.bypass_time_filter: bool = False

        # 동적 트레일링 스탑, 본전 보존 & 칼손절 파라미터 (주간 퀀트 손익비 진단 반영)
        self.high_watermarks: Dict[str, float] = {}     # 진입 후 종목별 최고가 추적
        self.trailing_active: Dict[str, bool] = {}       # 트레일링 발동 여부 (+2.0% 도달 시 True)
        self.breakeven_active: Dict[str, bool] = {}      # 본전 보존 발동 여부 (+1.2% 도달 시 True)
        self.traded_today_tickers: set[str] = set()      # 당일 청산/손절 완료 종목 재진입 원천 차단 (1일 1종목 1회 원칙)
        self.breakeven_activation_rate: float = 1.2     # +1.2% 도달 시 본전(+0.35%) 보존 스탑 잠금
        self.trailing_activation_rate: float = 2.0      # +2.0% 도달 시 트레일링 활성화
        self.trailing_drop_rate: float = 0.8            # 최고가 대비 -0.8% 반락 시 익절 (최소 +1.2% 명목수익 확보)
        self.stop_loss_rate: float = -1.8               # 돌파 실패 시 -1.8% 칼손절 (손익비 비대칭성 확보)
        self.us_regime: str = "US_NEUTRAL"              # 전일 미국장 매크로 레짐
        self.us_context: str = ""                       # JEV 판정 주입용 미국장 요약

    def set_us_context(self, regime: str, summary: str = ""):
        """08:45 미국장 레짐 및 요약 컨텍스트 주입"""
        self.us_regime = regime or "US_NEUTRAL"
        self.us_context = summary

    def update_watchlist(self, top_tickers: List[str], target_prices_map: Dict[str, float], prev_ranges_map: Optional[Dict[str, float]] = None):
        self.universe = top_tickers[:25]
        self.target_prices.update(target_prices_map)
        if prev_ranges_map:
            self.prev_ranges.update(prev_ranges_map)

    async def run_morning_calibration(self, exclude_tickers: Optional[List[str]] = None):
        """08:45 장전 루틴: 슬롯 A 중복 종목 배제 및 pykrx로 감시 종목의 전일 변동폭(prev_ranges)과 전일 분당 평균 거래량(avg_volumes) 사전 로드"""
        exclude_set = set(exclude_tickers or [])
        # 슬롯 A 스윙 종목과 겹치는 종목 자동 배제
        active_candidates = [t for t in self.universe if t not in exclude_set]
        if not active_candidates:
            active_candidates = [t for t in DEFAULT_MOMENTUM_UNIVERSE if t not in exclude_set]
        self.universe = active_candidates

        try:
            from pykrx import stock
            from datetime import timedelta
            today = datetime.now()
            today_str = today.strftime("%Y%m%d")
            start_str = (today - timedelta(days=10)).strftime("%Y%m%d")
            for ticker in self.universe[:15]:
                df = stock.get_market_ohlcv_by_date(start_str, today_str, ticker)
                if df is not None and len(df) >= 2:
                    prev_row = df.iloc[-2] if today.strftime("%Y-%m-%d") in str(df.index[-1]) else df.iloc[-1]
                    high_p = float(prev_row["고가"])
                    low_p = float(prev_row["저가"])
                    vol = float(prev_row["거래량"])
                    self.prev_ranges[ticker] = max(1.0, high_p - low_p)
                    # 정규장 390분(09:00~15:30) 분당 평균 거래량 추정
                    self.avg_volumes[ticker] = max(100.0, vol / 390.0)
            logger.info(f"✅ [{self.name}] 08:45 실증 전일변동폭 및 분당평균거래량 캘리브레이션 완료 ({len(self.prev_ranges)}종목, 슬롯A 제외 {len(exclude_set)}개)")
        except Exception as e:
            logger.info(f"[{self.name}] 캘리브레이션 폴백: {e}")

    async def evaluate_market(
        self,
        tick: TickData,
        current_position: Optional[SlotPosition] = None
    ) -> Optional[Signal]:
        now = datetime.now()
        current_time = now.time()

        # 정규장 시간(09:00 ~ 15:20) 외 장전/장후에는 매수 및 청산 평가 모두 원천 차단 (08:35 프리마켓 왜곡 호가 방지)
        if not self.bypass_time_filter and (current_time < dtime(9, 0) or current_time > dtime(15, 20)):
            return None

        ask_price = tick.ask_price if tick.ask_price > 0 else tick.price
        bid_price = tick.bid_price if tick.bid_price > 0 else tick.price
        if ask_price <= 0 or bid_price <= 0:
            return None

        asset_type = "ETF" if tick.ticker in ETF_UNIVERSE_SET else "STOCK"

        # 1. 포지션 보유 중인 경우 -> 청산(15:15 타임아웃, 트레일링 익절, 본전 보존, 칼손절) 검사
        if current_position:
            pnl_rate = ((bid_price - current_position.entry_price) / current_position.entry_price) * 100.0

            # (1) 15:15 당일 장마감 전량 청산 (테스트/시뮬레이션 우회 지원)
            if not self.bypass_time_filter and current_time >= dtime(15, 15):
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
                self.breakeven_active.pop(tick.ticker, None)
                self.traded_today_tickers.add(tick.ticker)
                self.jev_client.record_cooldown(tick.ticker)
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type=current_position.asset_type,
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

            # 본전 보존 모드 활성화 검사 (+1.2% 도달 시)
            if pnl_rate >= self.breakeven_activation_rate:
                self.breakeven_active[tick.ticker] = True

            # 트레일링 모드 활성화 검사 (+2.0% 도달 시)
            if pnl_rate >= self.trailing_activation_rate:
                self.trailing_active[tick.ticker] = True

            # (2) 동적 트레일링 익절: 트레일링 활성화 상태에서 최고가 대비 0.8% 이상 반락 시 이익 확정
            if self.trailing_active.get(tick.ticker, False):
                drop_from_high = ((high_price - bid_price) / high_price) * 100.0
                if drop_from_high >= self.trailing_drop_rate:
                    self.high_watermarks.pop(tick.ticker, None)
                    self.trailing_active.pop(tick.ticker, None)
                    self.breakeven_active.pop(tick.ticker, None)
                    self.traded_today_tickers.add(tick.ticker)
                    self.jev_client.record_cooldown(tick.ticker)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type=current_position.asset_type,
                        action="SELL",
                        order_type="MARKET_BREAKOUT",
                        price=bid_price,
                        quantity=current_position.quantity,
                        reason=f"트레일링 익절 발동 (최고가 {high_price:,.0f}원 대비 -{drop_from_high:.2f}% 반락, 확정손익률 +{pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_TRAILING_STOP"
                    )

            # (3) 본전 보존 스탑: +1.2% 이상 수익 진입 후 +0.35% 이하로 밀릴 시 수수료 보전 탈출
            if self.breakeven_active.get(tick.ticker, False) and pnl_rate <= 0.35:
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
                self.breakeven_active.pop(tick.ticker, None)
                self.traded_today_tickers.add(tick.ticker)
                self.jev_client.record_cooldown(tick.ticker)
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type=current_position.asset_type,
                    action="SELL",
                    order_type="MARKET_BREAKOUT",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"단타 수익 후 실질 본전(+0.35%) 보존 스탑 ({pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_BREAKEVEN_STOP"
                )

            # (4) 하방 칼손절 (-1.8%)
            if pnl_rate <= self.stop_loss_rate:
                self.high_watermarks.pop(tick.ticker, None)
                self.trailing_active.pop(tick.ticker, None)
                self.breakeven_active.pop(tick.ticker, None)
                self.traded_today_tickers.add(tick.ticker)
                self.jev_client.record_cooldown(tick.ticker)
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type=current_position.asset_type,
                    action="SELL",
                    order_type="MARKET_BREAKOUT",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"단타 절대 손절선 이탈 ({pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_STOP_LOSS"
                )

            return None

        # 2. 미보유 상태 -> 당일 이미 매매 완료된 종목은 고점 재진입 원천 차단 (1일 1종목 1회 원칙)
        if tick.ticker in self.traded_today_tickers:
            return None

        # 14:30 이후에는 신규 단타 진입 금지 (테스트/시뮬레이션 시 우회 허용)
        if not self.bypass_time_filter and (current_time >= dtime(14, 30) or current_time < dtime(9, 5)):
            return None

        # 종목별 당일 시초가 기록 및 변동성 돌파 목표가 산출
        if tick.ticker not in self.open_prices:
            self.open_prices[tick.ticker] = tick.price
            if tick.ticker not in self.avg_volumes and tick.volume > 0:
                self.avg_volumes[tick.ticker] = float(tick.volume)

        open_p = self.open_prices[tick.ticker]
        prev_range = self.prev_ranges.get(tick.ticker, open_p * 0.02)
        # 래리 윌리엄스 변동성 돌파 정석: 목표가 = 시초가 + k * (전일 고가 - 전일 저가)
        computed_target = round(open_p + (self.k_factor * prev_range), 2)
        target_price = self.target_prices.get(tick.ticker, computed_target)
        avg_vol = self.avg_volumes.get(tick.ticker, 1000.0)
        vol_1m = self.recent_1m_volumes.get(
            tick.ticker,
            float(tick.volume) if tick.volume > 0 else (avg_vol * 1.8)
        )

        # US 매크로 레짐별 체결강도 문턱 동적 조정 (인버스 ETF 제외, 약세 시 롱 가짜 반등 엄격 차단)
        is_inverse = tick.ticker in INVERSE_ETF_SET
        min_strength = 110.0
        if not is_inverse:
            if self.us_regime == "US_PANIC":
                min_strength = 125.0
            elif self.us_regime == "US_RISK_OFF":
                min_strength = 120.0

        # 1단계: 로컬 정량 필터 (0.1ms, $0)
        passed_stage1, reason1 = self.jev_client.check_stage1_filter(
            tick=tick,
            target_price=target_price,
            avg_vol_20m=avg_vol,
            vol_1m=vol_1m,
            min_exec_strength=min_strength
        )
        if not passed_stage1:
            return None

        # 2단계: JEV 1.13 초고속 API 호출 (700ms 타임아웃, 미국장 컨텍스트 포함)
        surge_ratio = vol_1m / max(avg_vol, 1.0)
        is_approved, fakeout_prob, reason2 = await self.jev_client.evaluate_stage2_jev(
            ticker=tick.ticker,
            tick=tick,
            target_price=target_price,
            surge_ratio=surge_ratio,
            us_context=self.us_context
        )

        if is_approved:
            qty = int(self.capital_per_stock // ask_price)
            if qty > 0:
                self.jev_client.record_cooldown(tick.ticker)
                logger.info(f"⚡ [슬롯 C 진입 승인] {tick.ticker} ({asset_type}, 가짜돌파확률: {fakeout_prob:.2f}) -> {qty}주 매수")
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type=asset_type,
                    action="BUY",
                    order_type="MARKET_BREAKOUT",
                    price=ask_price,
                    quantity=qty,
                    reason=f"변동성 돌파 및 JEV 승인 ({reason2})",
                    stop_loss_price=round(ask_price * (1.0 + self.stop_loss_rate / 100.0), 2),
                    take_profit_price=None
                )

        return None

    def reset_daily_state(self):
        """매일 장 시작 전(08:45) 일일 상태 초기화"""
        self.open_prices.clear()
        self.high_watermarks.clear()
        self.trailing_active.clear()
        self.breakeven_active.clear()
        self.traded_today_tickers.clear()
        self.recent_1m_volumes.clear()
