import logging
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

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
        self.take_profit_rate: float = 6.0    # +6.0% 최종 전량 익절
        self.partial_tp_rate: float = 3.0     # +3.0% 도달 시 50% 1차 분할 익절
        self.stop_loss_rate: float = -3.5     # 초기 손절선 -3.5%
        self.max_holding_days: int = 5
        self.split_buy_tracker: Dict[str, int] = {}
        self.partial_tp_tracker: Dict[str, bool] = {}    # 1차 익절 완료 여부 추적
        self.breakeven_tracker: Dict[str, bool] = {}     # 손절선 본전 상향 여부 추적
        self.bypass_time_filter: bool = False

    def update_candidates(self, candidates: List[str]):
        """D일 15:40 스크리닝된 종목 리스트 갱신"""
        self.selected_candidates = candidates[:self.max_stocks]
        self.universe = self.selected_candidates
        logger.info(f"[{self.name}] 익일(D+1) 매수 후보군 {len(self.selected_candidates)}종목 확정: {self.selected_candidates}")

    async def run_evening_screening(self):
        """D일 15:40 장마감 후 외인·기관 쌍끌이 순매수 상위 종목 스크리닝 (KRX 공식 세션 + 실증 OHLCV 백업)"""
        logger.info(f"🔍 [{self.name}] 외인·기관 쌍끌이 수급 스윙 스크리닝 가동...")
        candidates = ["005930", "000660", "005380", "035420"]
        screened: List[str] = []
        try:
            from pykrx import stock
            from datetime import timedelta
            today = datetime.now()
            today_str = today.strftime("%Y%m%d")
            start_str = (today - timedelta(days=7)).strftime("%Y%m%d")

            # 1순위: KRX 공식 로그인 세션 기반 외인·기관 쌍끌이 순매수 상위 종목 조회
            df_inst = stock.get_market_net_purchases_of_equities_by_ticker(today_str, today_str, "KOSPI", "기관합계")
            df_frgn = stock.get_market_net_purchases_of_equities_by_ticker(today_str, today_str, "KOSPI", "외국인")
            if (
                df_inst is not None and not df_inst.empty and "순매수거래대금" in df_inst.columns
                and df_frgn is not None and not df_frgn.empty and "순매수거래대금" in df_frgn.columns
            ):
                inst_pos = df_inst[df_inst["순매수거래대금"] > 0]
                frgn_pos = df_frgn[df_frgn["순매수거래대금"] > 0]
                common_tickers = inst_pos.index.intersection(frgn_pos.index)
                if len(common_tickers) > 0:
                    combined = inst_pos.loc[common_tickers, "순매수거래대금"] + frgn_pos.loc[common_tickers, "순매수거래대금"]
                    screened = list(combined.sort_values(ascending=False).index[:self.max_stocks])

            # 2순위: KRX 미로그인 환경일 경우 대표 우량주 풀에서 실증 거래대금·모멘텀 상위 종목 산출
            if not screened:
                pool = ["005930", "000660", "005380", "000270", "035420", "068270", "012450", "267260", "373220", "207940"]
                scored = []
                for t in pool:
                    df = stock.get_market_ohlcv_by_date(start_str, today_str, t)
                    if df is not None and len(df) >= 3 and "종가" in df.columns and "거래량" in df.columns:
                        last_close = float(df["종가"].iloc[-1])
                        prev_close = float(df["종가"].iloc[-2])
                        last_vol = float(df["거래량"].iloc[-1])
                        avg_vol = float(df["거래량"].iloc[:-1].mean()) if len(df) > 1 else last_vol
                        ret_pct = ((last_close - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
                        vol_ratio = last_vol / max(avg_vol, 1.0)
                        if ret_pct > -1.0:
                            scored.append((t, ret_pct + vol_ratio))
                if scored:
                    scored.sort(key=lambda x: x[1], reverse=True)
                    screened = [x[0] for x in scored[:self.max_stocks]]

            if screened:
                candidates = screened
        except Exception as e:
            logger.info(f"[{self.name}] pykrx 쌍끌이 수급 조회 폴백 적용: {e}")

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

        # 1. 이미 보유 중인 경우 -> 청산 조건(최종익절, 1차 분할익절, 본전보존/손절, 5일 만기) 검사
        if current_position:
            # 평가 수익률 계산
            pnl_rate = ((bid_price - current_position.entry_price) / current_position.entry_price) * 100.0

            # 오버나잇 복원: DB stop_loss_price가 진입가 이상이면 이미 1차 익절 및 본전보존 활성화 상태로 인식
            has_done_partial_tp = (
                self.partial_tp_tracker.get(tick.ticker, False)
                or (current_position.stop_loss_price >= current_position.entry_price)
            )
            is_breakeven_active = (
                self.breakeven_tracker.get(tick.ticker, False)
                or (current_position.stop_loss_price >= current_position.entry_price)
            )

            # (1) 최종 목표 익절 (+6.0% 이상 도달 시 잔여 수량 전량 익절)
            if pnl_rate >= self.take_profit_rate:
                self.split_buy_tracker[tick.ticker] = 0
                self.partial_tp_tracker.pop(tick.ticker, None)
                self.breakeven_tracker.pop(tick.ticker, None)
                return Signal(
                    slot_id=self.slot_id,
                    ticker=tick.ticker,
                    asset_type="STOCK",
                    action="SELL",
                    order_type="LIMIT_NORMAL",
                    price=bid_price,
                    quantity=current_position.quantity,
                    reason=f"목표 최종 익절 도달 (+{pnl_rate:.2f}%)",
                    exit_reason="STRATEGY_TAKE_PROFIT"
                )

            # (2) 1차 50% 분할 익절 (+3.0% 도달 시) & 손절선 본전(+0.3%) 상향
            if pnl_rate >= self.partial_tp_rate and not has_done_partial_tp:
                if current_position.quantity >= 2:
                    sell_qty = max(1, current_position.quantity // 2)
                    self.partial_tp_tracker[tick.ticker] = True
                    self.breakeven_tracker[tick.ticker] = True
                    new_sl = round(current_position.entry_price * 1.003, 2)
                    logger.info(f"🎯 [{self.name}] {tick.ticker} +{pnl_rate:.2f}% 도달 -> 1차 50%({sell_qty}주) 분할 익절 및 손절선 본전({new_sl:,.0f}원) 상향")
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="STOCK",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=sell_qty,
                        reason=f"수급 스윙 1차 50% 분할 익절 (+{pnl_rate:.2f}%) 및 손절선 본전 상향",
                        stop_loss_price=new_sl,
                        exit_reason="STRATEGY_PARTIAL_TP"
                    )
                else:
                    # 1주 보유 시에는 분할 매도가 불가하므로 1차 목표가에서 전량 익절
                    self.split_buy_tracker[tick.ticker] = 0
                    self.partial_tp_tracker.pop(tick.ticker, None)
                    self.breakeven_tracker.pop(tick.ticker, None)
                    logger.info(f"🎯 [{self.name}] {tick.ticker} 1주 보유 전량 익절 (+{pnl_rate:.2f}%)")
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="STOCK",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=1,
                        reason=f"수급 스윙 1주 보유 전량 익절 (+{pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_TAKE_PROFIT"
                    )

            # (3) 손절 로직: 1차 익절 완료 시 실질 본전(+0.3% 수수료 보정) 보존, 미완료 시 초기 손절(-3.5%)
            if is_breakeven_active:
                if pnl_rate <= 0.3:
                    self.split_buy_tracker[tick.ticker] = 0
                    self.partial_tp_tracker.pop(tick.ticker, None)
                    self.breakeven_tracker.pop(tick.ticker, None)
                    return Signal(
                        slot_id=self.slot_id,
                        ticker=tick.ticker,
                        asset_type="STOCK",
                        action="SELL",
                        order_type="LIMIT_NORMAL",
                        price=bid_price,
                        quantity=current_position.quantity,
                        reason=f"1차 익절 후 실질 본전(+0.3%) 보존 손절 발동 ({pnl_rate:.2f}%)",
                        exit_reason="STRATEGY_BREAKEVEN_STOP"
                    )
            else:
                if pnl_rate <= self.stop_loss_rate:
                    self.split_buy_tracker[tick.ticker] = 0
                    self.partial_tp_tracker.pop(tick.ticker, None)
                    self.breakeven_tracker.pop(tick.ticker, None)
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

            # (4) 보유 기한 만기 (5영업일 경과 & 15:15 이후)
            if current_position.holding_days >= self.max_holding_days and (self.bypass_time_filter or current_time >= dtime(15, 15)):
                self.split_buy_tracker[tick.ticker] = 0
                self.partial_tp_tracker.pop(tick.ticker, None)
                self.breakeven_tracker.pop(tick.ticker, None)
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

            # (4) 2차 50% 분할 매수 (D+1 진입 당일 09:15 ~ 10:00)
            if current_position.holding_days == 0 and dtime(9, 15) <= current_time <= dtime(10, 0):
                stage = self.split_buy_tracker.get(tick.ticker, 1)
                # 현재 포지션 가치가 목표금액의 70% 미만일 때(1차 50%만 매수된 상태)
                if stage < 2 and (current_position.quantity * current_position.entry_price) < (self.target_capital_per_stock * 0.7):
                    half_capital = self.target_capital_per_stock * 0.5
                    qty = int(half_capital // ask_price)
                    if qty > 0:
                        self.split_buy_tracker[tick.ticker] = 2
                        stop_price = round(ask_price * (1.0 + self.stop_loss_rate / 100.0), 2)
                        take_price = round(ask_price * (1.0 + self.take_profit_rate / 100.0), 2)
                        return Signal(
                            slot_id=self.slot_id,
                            ticker=tick.ticker,
                            asset_type="STOCK",
                            action="BUY",
                            order_type="SPLIT_BUY_2",
                            price=ask_price,
                            quantity=qty,
                            reason="수급 스윙 2차 50% 분할 매수",
                            stop_loss_price=stop_price,
                            take_profit_price=take_price
                        )

            return None

        # 2. 미보유 상태인 경우 -> 후보군 대상 분할 매수 검사 (09:00 ~ 10:00 1차 50%)
        if tick.ticker in self.selected_candidates:
            if self.split_buy_tracker.get(tick.ticker, 0) >= 1:
                return None  # 이미 당일 1차 매수 주문이 제출된 종목은 중복 매수 차단
            if dtime(9, 0) <= current_time <= dtime(10, 0):
                half_capital = self.target_capital_per_stock * 0.5  # 약 165만 원
                qty = int(half_capital // ask_price)
                if qty > 0:
                    self.split_buy_tracker[tick.ticker] = 1
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

    def reset_daily_state(self):
        """매일 장 시작 전(08:45) 일일 상태 초기화"""
        self.split_buy_tracker.clear()
        self.partial_tp_tracker.clear()
        self.breakeven_tracker.clear()
