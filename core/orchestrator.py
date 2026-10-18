import asyncio
from datetime import datetime, time as dtime
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from broker.kis_client import KisClient
from core.config import settings
from core.executor import SqlPaperExecutor
from core.ledger import SqliteLedger
from core.models import ExitReason, Signal, SlotAccount, SlotPosition, TickData, TradeRecord
from intelligence.gemini_client import GeminiAnalyst
from intelligence.jev_client import JevCascadeClient
from resilience.watchdog import WatchdogManager
from strategies.base import BaseStrategy
from strategies.market_filter import MarketFilterManager
from strategies.slot_a_institutional_swing import SlotAInstitutionalSwing
from strategies.slot_b_bollinger_grid import SlotBBollingerGrid
from strategies.slot_c_volatility_breakout import SlotCVolatilityBreakout
from strategies.slot_d_retest_swing import SlotDRetestSwing

logger = logging.getLogger(__name__)


class TradingOrchestrator:
    """
    4대 전략 슬롯, SQLite 가상 원장, KIS 실전 시세, 마켓 필터, 워치독을 총괄하는 중앙 오케스트레이터
    """
    def __init__(self, ledger: SqliteLedger, kis_client: KisClient):
        self.ledger = ledger
        self.kis_client = kis_client
        self.market_filter = MarketFilterManager()
        self.gemini_analyst = GeminiAnalyst()
        self.jev_client = JevCascadeClient()
        self.watchdog = WatchdogManager(
            ledger=self.ledger,
            get_tick_func=self.get_tick_for_watchdog
        )

        # 4대 전략 슬롯 인스턴스화
        self.strategies: Dict[str, BaseStrategy] = {
            "SLOT_A": SlotAInstitutionalSwing(slot_id="SLOT_A"),
            "SLOT_B": SlotBBollingerGrid(slot_id="SLOT_B"),
            "SLOT_C": SlotCVolatilityBreakout(jev_client=self.jev_client, slot_id="SLOT_C"),
            "SLOT_D": SlotDRetestSwing(slot_id="SLOT_D"),
        }

        self.is_running = False
        self._main_task: Optional[asyncio.Task] = None
        self._price_cache: Dict[str, TickData] = {}
        self._last_morning_screen_date: Optional[str] = None
        self._last_evening_screen_date: Optional[str] = None
        self._last_exit_1515_date: Optional[str] = None

        self.latest_daily_review: str = ""
        self.latest_morning_briefing: str = ""
        self.latest_weekly_review: str = ""
        self.latest_screening_candidates: Dict[str, List[str]] = {
            "SLOT_A": ["005930", "000660", "005380"],
            "SLOT_D": ["005930", "000660", "035420"]
        }
        self._load_cached_reports()

    def _load_cached_reports(self):
        """기존 디스크에 저장된 복기 및 브리핑 리포트 메모리 캐시 로드"""
        try:
            d_path = Path("reports/daily_review.md")
            if d_path.exists():
                self.latest_daily_review = d_path.read_text(encoding="utf-8").strip()
            m_path = Path("reports/morning_briefing.md")
            if m_path.exists():
                self.latest_morning_briefing = m_path.read_text(encoding="utf-8").strip()
            w_path = Path("reports/weekly_review.md")
            if w_path.exists():
                self.latest_weekly_review = w_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"기존 리포트 캐시 로드 실패: {e}")

    async def get_tick_for_watchdog(self, ticker: str) -> Optional[TickData]:
        """워치독 비상 탈출 시 호가 조회 헬퍼"""
        if ticker in self._price_cache:
            return self._price_cache[ticker]
        return await self.kis_client.get_realtime_price(ticker)

    async def _check_and_run_morning_catchup(self):
        """08:45 이후(정규장 중 포함) 기동되었을 때 장전 스크리닝이 누락되지 않도록 즉시 실행"""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        current_time = now.time()

        if current_time >= dtime(8, 45) and current_time < dtime(15, 30):
            if self._last_morning_screen_date != today_str:
                logger.info("☀️ [Orchestrator] 08:45 이후(정규장 중) 기동 감지 -> 슬롯 D 장전 스크리닝 및 브리핑 즉시 캐치업 실행")
                slot_d = self.strategies.get("SLOT_D")
                if isinstance(slot_d, SlotDRetestSwing):
                    await slot_d.run_morning_screening()
                    self.sync_all_subscriptions()

                try:
                    await self.gemini_analyst.generate_morning_briefing({
                        "kospi_index": self.market_filter.kospi_index,
                        "kospi_20ma": self.market_filter.kospi_20ma,
                        "watchlist_count": len(self.strategies["SLOT_C"].universe)
                    })
                except Exception as e:
                    logger.warning(f"장전 브리핑 캐치업 예외: {e}")

                self._last_morning_screen_date = today_str

    async def _check_and_run_evening_catchup(self):
        """15:40 이후 또는 장마감 후 기동되었을 때 슬롯 A 스크리닝 및 당일 복기가 누락되지 않도록 즉시 실행"""
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        current_time = now.time()

        if (current_time >= dtime(15, 40) or now.weekday() >= 5) and self._last_evening_screen_date != today_str:
            logger.info("🌙 [Orchestrator] 15:40 이후/장마감 기동 감지 -> 슬롯 A 장후 스크리닝 및 일일 복기 즉시 캐치업 실행")
            slot_a = self.strategies.get("SLOT_A")
            if isinstance(slot_a, SlotAInstitutionalSwing):
                await slot_a.run_evening_screening()
                self.sync_all_subscriptions()

            accounts = await self.ledger.get_accounts()
            positions = await self.ledger.get_positions()
            trades = await self.ledger.get_recent_trades(limit=20)

            slot_d = self.strategies.get("SLOT_D")
            screening = {
                "SLOT_A": getattr(slot_a, "selected_candidates", getattr(slot_a, "universe", ["005930", "000660", "005380"])),
                "SLOT_D": getattr(slot_d, "selected_candidates", getattr(slot_d, "universe", ["005930", "000660", "035420"]))
            }
            self.latest_screening_candidates = screening

            try:
                self.latest_daily_review = await self.gemini_analyst.generate_daily_review(
                    accounts=[a.model_dump() for a in accounts],
                    trades=[t.model_dump() for t in trades],
                    positions=[p.model_dump() for p in positions],
                    screening_candidates=screening,
                    force_regenerate=True
                )
                await self._save_daily_settlement_record(self.latest_daily_review, screening)

                # 금요일 또는 주말 기동 시 주간 퀀트 손익비 진단 리포트 동시 생성
                if now.weekday() >= 4:
                    try:
                        weekly_trades = await self.ledger.get_trades_for_days(days=7)
                        weekly_settlements = await self.ledger.get_daily_settlements(limit=7)
                        self.latest_weekly_review = await self.gemini_analyst.generate_weekly_evaluation(
                            weekly_trades=[t.model_dump() for t in weekly_trades],
                            weekly_settlements=[s.model_dump() for s in weekly_settlements],
                            accounts=[a.model_dump() for a in accounts],
                            force_regenerate=True
                        )
                    except Exception as e_w:
                        logger.warning(f"주간 손익비 복기 캐치업 예외: {e_w}")
            except Exception as e:
                logger.warning(f"장후 복기 캐치업 예외: {e}")

            self._last_evening_screen_date = today_str

    async def _save_daily_settlement_record(self, review_markdown: str, screening: dict):
        """당일 결산 및 AI 복기 결과를 SQLite daily_settlements 원장에 영구 기록"""
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            accounts = await self.ledger.get_accounts()
            positions = await self.ledger.get_positions()
            trades = await self.ledger.get_recent_trades(limit=50)

            # 당일 청산된 거래만 필터링
            today_trades = [
                t for t in trades 
                if (isinstance(t.exit_time, datetime) and t.exit_time.strftime("%Y-%m-%d") == today_str) or
                   (isinstance(t.exit_time, str) and t.exit_time.startswith(today_str))
            ]

            total_initial = sum(a.initial_capital for a in accounts) or 40_000_000.0
            total_asset = sum(a.total_asset for a in accounts)
            total_cash = sum(a.cash_balance for a in accounts)
            stock_value = sum(a.stock_value for a in accounts)
            total_profit = sum(a.total_profit for a in accounts)
            profit_rate = (total_profit / total_initial) * 100.0 if total_initial > 0 else 0.0

            realized_pnl = sum(t.net_pnl for t in today_trades)
            unrealized_pnl = total_profit - realized_pnl

            await self.ledger.save_daily_settlement(
                date=today_str,
                total_asset=total_asset,
                cash_balance=total_cash,
                stock_value=stock_value,
                total_profit=total_profit,
                profit_rate=profit_rate,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                trade_count=len(today_trades),
                review_markdown=review_markdown,
                screening_json=json.dumps(screening, ensure_ascii=False)
            )
            logger.info(f"💾 [Orchestrator] SQLite daily_settlements 원장에 {today_str} 일일 결산 영구 기록 완료")
        except Exception as e:
            logger.error(f"❌ [Orchestrator] 일일 결산 원장 기록 실패: {e}")

    def sync_all_subscriptions(self):
        """4대 슬롯 전략의 모든 유니버스 종목을 브로커 시세 구독 매니저에 일괄 등록"""
        quota_mgr = getattr(self.kis_client, "subscription_manager", getattr(self.kis_client, "quota_manager", None))
        if not quota_mgr:
            return

        for slot_id, strategy in self.strategies.items():
            if hasattr(quota_mgr, "update_strategy_universe"):
                quota_mgr.update_strategy_universe(slot_id, getattr(strategy, "universe", []))

        # 슬롯 C 하위호환성
        slot_c = self.strategies.get("SLOT_C")
        if slot_c and hasattr(quota_mgr, "update_momentum_watchlist"):
            quota_mgr.update_momentum_watchlist(slot_c.universe)

        subscribed = quota_mgr.get_subscribed_tickers() if hasattr(quota_mgr, "get_subscribed_tickers") else []
        logger.info(f"📡 [Orchestrator] 전 슬롯 시세 구독 동기화 완료: 총 {len(subscribed)}개 종목 관제 중 ({subscribed})")

    async def start(self):
        """오케스트레이터 시작 (DB 초기화, 틱 콜백 등록, 스케줄러 태스크 시작)"""
        logger.info("🚀 [Orchestrator] 주식 자동매매 오케스트레이터 시작")
        await self.ledger.init_db()

        # 틱 수신 콜백 등록
        self.kis_client.register_tick_callback(self.on_tick_received)

        # 시장 방어 필터 실증 20MA 및 기준지수 동기화
        self.market_filter.refresh_from_history()

        # 지연 기동(08:45 이후) 장전 스크리닝 즉시 캐치업
        await self._check_and_run_morning_catchup()
        # 15:40 이후 또는 주말 기동 시 장후 스크리닝 및 복기 즉시 캐치업
        await self._check_and_run_evening_catchup()

        # 웹소켓/배치 쿼터 매니저에 초기 보유 종목 등록
        positions = await self.ledger.get_positions()
        held_tickers = list({p.ticker for p in positions})
        self.kis_client.quota_manager.update_held_positions(held_tickers)

        # 전 슬롯 유니버스 종목 브로커 시세 구독 매니저에 일괄 등록
        self.sync_all_subscriptions()

        self.is_running = True
        self._main_task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """오케스트레이터 정상 종료"""
        logger.info("🛑 [Orchestrator] 오케스트레이터 종료 요청")
        self.is_running = False
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
        await self.watchdog.stop()
        await self.kis_client.stop()
        logger.info("✅ [Orchestrator] 오케스트레이터 완전 종료")

    async def on_tick_received(self, tick: TickData):
        """실시간 틱 데이터 유입 시 각 슬롯 전략 평가 및 원장 집행"""
        self._price_cache[tick.ticker] = tick

        # 원장 내 평가가 반영
        await self.ledger.update_market_prices({tick.ticker: tick.price})

        # KODEX 200 (069500) 틱을 기반으로 실시간 시장 등락률 및 마켓필터 상태 동적 연동
        if tick.ticker == "069500":
            self.market_filter.update_from_kodex200(tick.price)

        # 보유 중인 포지션 조회
        positions = await self.ledger.get_positions()
        positions_by_slot = {f"{p.slot_id}:{p.ticker}": p for p in positions}

        # 관련 슬롯 전략 순회 및 평가
        for slot_id, strategy in self.strategies.items():
            pos_key = f"{slot_id}:{tick.ticker}"
            current_pos = positions_by_slot.get(pos_key)

            # 포지션이 있거나 유니버스에 포함된 경우에만 전략 평가
            if current_pos is not None or tick.ticker in strategy.universe:
                try:
                    signal = await strategy.evaluate_market(tick, current_pos)
                    if signal:
                        await self._process_signal(signal, tick)
                except Exception as e:
                    logger.error(f"❌ [Orchestrator] {slot_id} 전략 평가 중 오류: {e}", exc_info=True)

    async def _process_signal(self, signal: Signal, tick: TickData):
        """시그널 발생 시 마켓 필터 및 자금 검증 후 체결 엔진 집행"""
        if signal.action == "SELL":
            # 매도/청산은 마켓필터와 무관하게 즉시 집행 (손절/익절/시간청산)
            exit_reason = signal.exit_reason or "STRATEGY_TAKE_PROFIT"
            trade = await self.ledger.close_position(
                slot_id=signal.slot_id,
                ticker=signal.ticker,
                tick=tick,
                exit_reason=exit_reason,
                order_type=signal.order_type,
                quantity=signal.quantity
            )
            if trade:
                logger.info(
                    f"🎯 [체결 완료] {signal.slot_id} {signal.ticker} 청산: "
                    f"수익률 {trade.profit_rate:+.2f}%, 순손익 {trade.net_pnl:+,.0f}원, 사유={exit_reason}"
                )
                # 보유종목 목록 갱신
                remaining_positions = await self.ledger.get_positions()
                self.kis_client.quota_manager.update_held_positions(
                    list({p.ticker for p in remaining_positions})
                )

        elif signal.action == "BUY":
            # 신규 진입 시 마켓필터 통과 여부 확인
            if not self.market_filter.can_enter(signal.slot_id):
                logger.info(f"🚫 [진입 차단] 마켓필터 하락장 방어막에 의해 {signal.slot_id} {signal.ticker} 매수 보류")
                return

            # 보유 종목 수 상한 확인 (슬롯 A: 최대 3종목 등)
            positions = await self.ledger.get_positions(signal.slot_id)
            if signal.slot_id == "SLOT_A" and len(positions) >= 3:
                # 추가 분할 매수가 아닌 신규 종목 진입인 경우 차단
                if not any(p.ticker == signal.ticker for p in positions):
                    logger.info("🚫 [SLOT_A] 최대 보유 종목 수(3종목) 도달로 신규 진입 차단")
                    return

            new_pos = await self.ledger.open_position(signal, tick)
            if new_pos:
                logger.info(
                    f"🛒 [매수 체결] {signal.slot_id} {signal.ticker} {signal.quantity}주 @ {new_pos.entry_price:,.0f}원 "
                    f"({signal.order_type})"
                )
                # 보유종목 웹소켓 쿼터 우선 승격
                all_positions = await self.ledger.get_positions()
                self.kis_client.quota_manager.update_held_positions(
                    list({p.ticker for p in all_positions})
                )

    async def execute_emergency_flush(self):
        """
        워치독 통신 장애(60초 이상 단절 후 복구) 시 호출되는 비상 손절 집행 루틴:
        현재 시세 기준 손절가(-3.5%)를 하회한 종목들을 즉시 긴급 청산
        """
        logger.warning("🚨 [Emergency Flush] 통신 복구 후 손절선 이탈 포지션 긴급 청산 가동")
        positions = await self.ledger.get_positions()
        for pos in positions:
            tick = self._price_cache.get(pos.ticker)
            if not tick:
                continue

            # 손절가 이탈 여부 점검
            if tick.price <= pos.stop_loss_price:
                logger.critical(
                    f"⚠️ [비상 청산] {pos.slot_id} {pos.ticker}: 현재가({tick.price}) <= 손절가({pos.stop_loss_price}) -> 즉시 비상 청산"
                )
                await self.ledger.close_position(
                    slot_id=pos.slot_id,
                    ticker=pos.ticker,
                    tick=tick,
                    exit_reason="EMERGENCY_FLUSH_NETWORK_RECOVERY",
                    order_type="EMERGENCY_FLUSH"
                )

    async def master_kill_switch(self) -> Dict[str, str]:
        """
        안드로이드 앱 또는 마스터 API에서 호출되는 마스터 킬스위치:
        전 슬롯 전 포지션을 시장가 비상 청산(-0.1% 슬리피지 페널티)하고 모든 슬롯 비활성화
        """
        logger.critical("🛑🛑🛑 [MASTER KILL SWITCH] 마스터 킬스위치 작동! 전 포지션 즉시 전량 매도 🛑🛑🛑")
        positions = await self.ledger.get_positions()
        results = {}

        for pos in positions:
            tick = self._price_cache.get(
                pos.ticker,
                TickData(
                    ticker=pos.ticker,
                    price=pos.current_price,
                    bid_price=pos.current_price * 0.99,
                    ask_price=pos.current_price * 1.01
                )
            )
            trade = await self.ledger.close_position(
                slot_id=pos.slot_id,
                ticker=pos.ticker,
                tick=tick,
                exit_reason="MASTER_KILL_SWITCH",
                order_type="KILL_SWITCH"
            )
            if trade:
                results[f"{pos.slot_id}:{pos.ticker}"] = f"청산 완료 (순손익: {trade.net_pnl:+,.0f}원)"

        # 모든 슬롯 계좌 비활성화
        accounts = await self.ledger.get_accounts()
        for acc in accounts:
            await self.ledger.set_slot_active(acc.slot_id, False)

        return results

    async def toggle_slot(self, slot_id: str, is_active: bool):
        """특정 슬롯 활성화/비활성화"""
        await self.ledger.set_slot_active(slot_id, is_active)
        logger.info(f"⚙️ [Orchestrator] {slot_id} 활성화 상태 변경: {is_active}")

    async def close_slot_position(self, slot_id: str, ticker: str) -> Optional[TradeRecord]:
        """단일 포지션 수동 청산"""
        tick = self._price_cache.get(ticker)
        if not tick:
            positions = await self.ledger.get_positions(slot_id)
            target = next((p for p in positions if p.ticker == ticker), None)
            if target:
                tick = TickData(
                    ticker=ticker,
                    price=target.current_price,
                    bid_price=target.current_price,
                    ask_price=target.current_price
                )
            else:
                return None

        return await self.ledger.close_position(
            slot_id=slot_id,
            ticker=ticker,
            tick=tick,
            exit_reason="MANUAL_EXIT",
            order_type="LIMIT_NORMAL"
        )

    async def _run_loop(self):
        """정규장 스케줄링 및 상시 백그라운드 관리 루프"""
        while self.is_running:
            try:
                now = datetime.now()
                today_str = now.strftime("%Y-%m-%d")
                current_time = now.time()

                # 1. 08:45 이후 슬롯 D 전고점 돌파 스크리닝 및 Gemini 브리핑
                if current_time >= dtime(8, 45) and current_time < dtime(15, 30) and self._last_morning_screen_date != today_str:
                    logger.info("⏰ [08:45] 슬롯 D 전고점 돌파 첫 눌림목 장전 스크리닝 및 브리핑 실행")
                    slot_d = self.strategies.get("SLOT_D")
                    if isinstance(slot_d, SlotDRetestSwing):
                        await slot_d.run_morning_screening()
                        self.sync_all_subscriptions()

                    try:
                        await self.gemini_analyst.generate_morning_briefing({
                            "kospi_index": self.market_filter.kospi_index,
                            "kospi_20ma": self.market_filter.kospi_20ma,
                            "watchlist_count": len(self.strategies["SLOT_C"].universe)
                        })
                    except Exception as e:
                        logger.warning(f"장전 브리핑 예외: {e}")

                    self._last_morning_screen_date = today_str

                # 2. 15:15 슬롯 C 단타 당일 전량 청산
                if current_time >= dtime(15, 15) and current_time < dtime(15, 30) and getattr(self, '_last_exit_1515_date', None) != today_str:
                    logger.info("⏰ [15:15] 슬롯 C 변동성 돌파 단타 당일 전량 청산 가동")
                    positions_c = await self.ledger.get_positions("SLOT_C")
                    for pos in positions_c:
                        tick = self._price_cache.get(pos.ticker, TickData(
                            ticker=pos.ticker,
                            price=pos.current_price,
                            bid_price=pos.current_price,
                            ask_price=pos.current_price
                        ))
                        await self.ledger.close_position(
                            slot_id="SLOT_C",
                            ticker=pos.ticker,
                            tick=tick,
                            exit_reason="TIME_EXIT_1515",
                            order_type="LIMIT_NORMAL"
                        )
                    self._last_exit_1515_date = today_str

                # 3. 15:40 슬롯 A 외인·기관 쌍끌이 스크리닝 및 Gemini 장후 복기
                if current_time >= dtime(15, 40) and self._last_evening_screen_date != today_str:
                    logger.info("⏰ [15:40] 장마감 후 작업: 슬롯 A 스크리닝 및 당일 복기 생성")
                    slot_a = self.strategies.get("SLOT_A")
                    if isinstance(slot_a, SlotAInstitutionalSwing):
                        await slot_a.run_evening_screening()
                        self.sync_all_subscriptions()

                    accounts = await self.ledger.get_accounts()
                    positions = await self.ledger.get_positions()
                    trades = await self.ledger.get_recent_trades(limit=20)
                    slot_d = self.strategies.get("SLOT_D")
                    screening = {
                        "SLOT_A": getattr(slot_a, "selected_candidates", getattr(slot_a, "universe", ["005930", "000660", "005380"])),
                        "SLOT_D": getattr(slot_d, "selected_candidates", getattr(slot_d, "universe", ["005930", "000660", "035420"]))
                    }
                    self.latest_screening_candidates = screening
                    try:
                        self.latest_daily_review = await self.gemini_analyst.generate_daily_review(
                            accounts=[a.model_dump() for a in accounts],
                            trades=[t.model_dump() for t in trades],
                            positions=[p.model_dump() for p in positions],
                            screening_candidates=screening,
                            force_regenerate=True
                        )
                        await self._save_daily_settlement_record(self.latest_daily_review, screening)

                        # 금요일 15:40 주간 퀀트 손익비 진단 리포트 동시 발행
                        if now.weekday() == 4:
                            try:
                                weekly_trades = await self.ledger.get_trades_for_days(days=7)
                                weekly_settlements = await self.ledger.get_daily_settlements(limit=7)
                                self.latest_weekly_review = await self.gemini_analyst.generate_weekly_evaluation(
                                    weekly_trades=[t.model_dump() for t in weekly_trades],
                                    weekly_settlements=[s.model_dump() for s in weekly_settlements],
                                    accounts=[a.model_dump() for a in accounts],
                                    force_regenerate=True
                                )
                            except Exception as e_w:
                                logger.warning(f"주간 손익비 복기 예외: {e_w}")
                    except Exception as e:
                        logger.warning(f"장후 복기 예외: {e}")

                    self._last_evening_screen_date = today_str

                # 4. 15:45 장후 결산 및 복기 완료 후 서버 자체 자동 종료 (정규장 마감 세션)
                if current_time >= dtime(15, 45) and current_time < dtime(16, 0) and self._last_evening_screen_date == today_str and not getattr(self, '_shutdown_initiated', False):
                    self._shutdown_initiated = True
                    logger.info("⏰ [15:45] 정규장 마감 결산/복기 완료 -> 매매 서버 자체 자동 종료 시퀀스 가동")
                    asyncio.create_task(self._trigger_auto_shutdown())

                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [Orchestrator] 스케줄러 루프 오류: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _trigger_auto_shutdown(self):
        """15:45 장마감 결산 완료 후 매매 서버 자체 안전 종료"""
        await asyncio.sleep(2)
        logger.info("🛑 [Orchestrator] 서버 프로세스 안전 종료 중...")
        try:
            import os, signal
            os.kill(os.getpid(), signal.SIGINT)
        except Exception:
            import os
            os._exit(0)

