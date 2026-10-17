import asyncio
import logging
import time
from datetime import datetime, time as dtime
from typing import Callable, Optional

from core.ledger import SqliteLedger
from core.models import TickData

logger = logging.getLogger(__name__)


class WatchdogManager:
    """
    정규장(09:00~15:30) 네트워크 및 하트비트 감시,
    60초 이상 단절 후 복구 시 Emergency Flush(비상 탈출)를 자동 실행하는 워치독
    """
    def __init__(self, ledger: SqliteLedger, get_tick_func: Callable[[str], asyncio.Future]):
        self.ledger = ledger
        self.get_tick_func = get_tick_func
        self.last_healthy_at = time.time()
        self.is_disconnected = False
        self.disconnected_at: Optional[float] = None
        self.emergency_flush_count = 0
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def is_regular_market_hours(self) -> bool:
        """현재 시간이 한국 정규장(09:00 ~ 15:30)인지 확인"""
        now = datetime.now().time()
        return dtime(9, 0) <= now <= dtime(15, 30)

    def mark_heartbeat(self):
        """정상 핑 수신 시 갱신"""
        self.last_healthy_at = time.time()
        if self.is_disconnected:
            logger.info("네트워크 연결 복구 감지! Emergency Flush 프로토콜 검사 시작...")
            asyncio.create_task(self.on_reconnect())

    async def on_reconnect(self):
        """통신 복구 시 비상 탈출 프로토콜 집행"""
        self.is_disconnected = False
        discon_duration = time.time() - (self.disconnected_at or self.last_healthy_at)
        logger.warning(f"통신 재연결 완료 (단절 지속 시간: {discon_duration:.1f}초)")

        if discon_duration >= 60.0:
            await self.execute_emergency_flush(reason="EMERGENCY_FLUSH_NETWORK_RECOVERY")
        self.disconnected_at = None

    async def execute_emergency_flush(self, reason: str = "EMERGENCY_FLUSH_NETWORK_RECOVERY"):
        """보유 포지션 전수 검사 후 손절선 이탈 종목 즉시 실세가 시장가 매도"""
        positions = await self.ledger.get_positions()
        if not positions:
            return

        logger.warning(f"🚨 [비상 탈출 발동] 보유 {len(positions)}개 포지션 실세가 점검 시작 ({reason})")

        for pos in positions:
            tick = await self.get_tick_func(pos.ticker)
            if not tick:
                continue

            # 현재 매수1호가가 손절가 이하인지 점검
            if tick.bid_price <= pos.stop_loss_price:
                logger.error(
                    f"🚨 [비상 탈출 청산] {pos.slot_id} - {pos.ticker}: "
                    f"손절가({pos.stop_loss_price}원) 하회 (현재1호가: {tick.bid_price}원) -> 즉시 청산"
                )
                await self.ledger.close_position(
                    slot_id=pos.slot_id,
                    ticker=pos.ticker,
                    tick=tick,
                    exit_reason=reason,
                    order_type="EMERGENCY_FLUSH"
                )
                self.emergency_flush_count += 1

    async def check_on_reboot(self):
        """PC 재부팅/정전 직후 시스템 시작 시 포지션 손절선 점검"""
        logger.info("PC 부팅/재시작 완료 -> 보유 포지션 손절가 이탈 여부 긴급 전수 점검")
        await self.execute_emergency_flush(reason="EMERGENCY_FLUSH_REBOOT")

    async def start(self):
        """워치독 루프 시작"""
        self._running = True
        self.last_healthy_at = time.time()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while self._running:
            try:
                await asyncio.sleep(5.0)

                # 정규장 시간이 아닐 경우 점검 스킵
                if not self.is_regular_market_hours():
                    self.last_healthy_at = time.time()
                    continue

                now = time.time()
                elapsed = now - self.last_healthy_at

                if elapsed > 30.0 and not self.is_disconnected:
                    self.is_disconnected = True
                    self.disconnected_at = now
                    logger.critical(f"⚠️ [통신 단절 감지] {elapsed:.1f}초 동안 시세 응답 없음! 신규 매수 동결")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"워치독 루프 예외: {e}")
