import asyncio
import json
import logging
import os
import signal
from datetime import datetime
from typing import Any, Dict, Optional

from core.config import settings
from core.orchestrator import TradingOrchestrator

logger = logging.getLogger(__name__)

try:
    import firebase_admin
    from firebase_admin import credentials, db
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    logger.warning("firebase-admin 패키지가 설치되지 않았습니다. pip install firebase-admin 필요.")


class FirebaseBridge:
    """
    Firebase Realtime Database (RTDB) 중계 브리지:
    - 홈 PC 백엔드 <-> 안드로이드 모바일 관제 앱 간 양방향 실시간 통신
    - 포트포워딩 및 VPN(Tailscale) 불필요, 구글 클라우드 경유 초저지연 상태 동기화
    - 데몬 없이 매매 서버 자체에서 모바일 STOP 원격 종료 명령 직접 수신 및 안전 종료
    - 자격증명 파일 부재 시 Graceful Mock 모드로 안전하게 작동
    """
    def __init__(self, orchestrator: TradingOrchestrator):
        self.orchestrator = orchestrator
        self.app: Optional[firebase_admin.App] = None
        self.is_connected = False
        self.is_running = False
        self._sync_task: Optional[asyncio.Task] = None
        self._listener_registration = None
        self._server_control_listener_registration = None
        self._last_handled_server_control_id = ""
        self.mock_mode = False
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    def initialize(self) -> bool:
        """Firebase Admin SDK 초기화"""
        if not FIREBASE_AVAILABLE:
            logger.warning("⚠️ [FirebaseBridge] firebase-admin 라이브러리 없음 -> Mock 모드로 동작")
            self.mock_mode = True
            return False

        cred_path = settings.FIREBASE_CREDENTIALS_PATH
        if not os.path.exists(cred_path):
            logger.warning(
                f"⚠️ [FirebaseBridge] 자격증명 파일({cred_path})을 찾을 수 없습니다. "
                "안드로이드 연동을 위해 콘솔에서 서비스 계정 키를 발급받아 프로젝트 루트에 배치해 주세요. "
                "현재는 로컬 Mock 브리지 모드로 안전하게 실행됩니다."
            )
            self.mock_mode = True
            return False

        try:
            cred = credentials.Certificate(cred_path)
            # 이미 초기화된 앱이 없을 때만 initialize_app 호출
            if not firebase_admin._apps:
                self.app = firebase_admin.initialize_app(cred, {
                    'databaseURL': settings.FIREBASE_DB_URL
                })
            else:
                self.app = firebase_admin.get_app()

            self.is_connected = True
            self.mock_mode = False
            logger.info("🔥 [FirebaseBridge] Firebase Realtime Database 연결 성공!")
            return True
        except Exception as e:
            logger.error(f"❌ [FirebaseBridge] Firebase 초기화 실패: {e}", exc_info=True)
            self.mock_mode = True
            return False

    def _get_target_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """비동기 이벤트 루프 안전 탐색 (메인 루프 우선, 백그라운드 스레드 안전)"""
        if self._main_loop and not self._main_loop.is_closed():
            return self._main_loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            try:
                return asyncio.get_event_loop()
            except RuntimeError:
                return None

    async def start(self):
        """동기화 루프 및 안드로이드 원격 명령 리스너 시작"""
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self.initialize()
        self.is_running = True

        if not self.mock_mode and self.is_connected:
            self._start_command_listener()
            self._start_server_control_listener()
            self.sync_app_version()
            self.update_server_control_state("RUNNING")

        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("📡 [FirebaseBridge] 상태 동기화 서비스 가동 시작")

    async def stop(self):
        """브리지 정상 종료"""
        self.is_running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

        if self._listener_registration:
            try:
                self._listener_registration.close()
            except Exception:
                pass

        if self._server_control_listener_registration:
            try:
                self._server_control_listener_registration.close()
            except Exception:
                pass

        # 서버 종료 시 Firebase RTDB에 즉시 OFFLINE 및 STOPPED 마킹
        if not self.mock_mode and self.is_connected:
            try:
                ref = db.reference('/system_status')
                ref.update({"status": "OFFLINE", "server_status": "OFFLINE", "last_heartbeat": 0})
            except Exception:
                pass
            self.update_server_control_state("STOPPED")

        logger.info("✅ [FirebaseBridge] 상태 동기화 서비스 종료")

    def update_server_control_state(self, state: str):
        """Firebase RTDB /server_control 상태 동기화 (RUNNING / STOPPED)"""
        if self.mock_mode or not self.is_connected:
            return
        try:
            ref = db.reference('/server_control')
            ref.update({
                "state": state,
                "pid": os.getpid() if state == "RUNNING" else 0,
                "last_updated": int(datetime.now().timestamp() * 1000)
            })
            logger.info(f"📡 [FirebaseBridge] /server_control 상태 동기화: {state}")
        except Exception as e:
            logger.warning(f"⚠️ [FirebaseBridge] /server_control 상태 업데이트 실패: {e}")

    def _start_server_control_listener(self):
        """스마트폰 앱의 /server_control 원격 종료 명령 실시간 감시"""
        try:
            ref = db.reference('/server_control')

            def _on_server_control(event):
                if not event.data or not isinstance(event.data, dict):
                    return
                cmd = event.data.get("command")
                cmd_id = event.data.get("command_id", "")
                if cmd == "STOP" and cmd_id and cmd_id != self._last_handled_server_control_id:
                    self._last_handled_server_control_id = cmd_id
                    logger.info(f"🛑 [FirebaseBridge] 모바일 앱에서 서버 종료 명령 수신 (ID: {cmd_id})")
                    loop = self._get_target_loop()
                    if loop:
                        asyncio.run_coroutine_threadsafe(
                            self._handle_server_stop_command(cmd_id),
                            loop
                        )
                    else:
                        logger.error("❌ [FirebaseBridge] STOP 명령 처리를 위한 비동기 이벤트 루프를 찾을 수 없습니다.")

            self._server_control_listener_registration = ref.listen(_on_server_control)
            logger.info("🎧 [FirebaseBridge] /server_control 원격 종료 명령 수신 대기 중")
        except Exception as e:
            logger.error(f"❌ [FirebaseBridge] server_control 리스너 등록 실패: {e}")

    async def _handle_server_stop_command(self, cmd_id: str):
        """모바일 앱 STOP 명령 처리 및 서버 자체 안전 종료"""
        try:
            ref = db.reference('/server_control')
            ref.update({
                "command": "NONE",
                "state": "STOPPED",
                "last_handled_id": cmd_id
            })
            ref_status = db.reference('/system_status')
            ref_status.update({"status": "OFFLINE", "server_status": "OFFLINE", "last_heartbeat": 0})
        except Exception as e:
            logger.warning(f"상태 업데이트 실패: {e}")

        logger.info("🛑 [FirebaseBridge] 모바일 앱 요청에 따라 매매 서버 자체 안전 종료 시퀀스 가동...")
        await asyncio.sleep(1)
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except Exception:
            os._exit(0)

    def _start_command_listener(self):
        """안드로이드 앱에서 보낸 제어 명령(/commands) 실시간 리스닝"""
        try:
            ref = db.reference('/commands')

            def _on_command(event):
                # event.data가 dict 형태이고 processed가 아닐 때
                if not event.data or not isinstance(event.data, dict):
                    return

                # 비동기 이벤트 루프에 커맨드 실행 위임
                loop = self._get_target_loop()
                if loop:
                    asyncio.run_coroutine_threadsafe(
                        self._handle_commands(event.data),
                        loop
                    )
                else:
                    logger.error("❌ [FirebaseBridge] 제어 명령 처리를 위한 비동기 이벤트 루프를 찾을 수 없습니다.")

            self._listener_registration = ref.listen(_on_command)
            logger.info("🎧 [FirebaseBridge] /commands 원격 제어 명령 수신 대기 중")
        except Exception as e:
            logger.error(f"❌ [FirebaseBridge] 명령 리스너 등록 실패: {e}")

    async def _handle_commands(self, commands_data: Dict[str, Any]):
        """명령 처리 파이프라인"""
        for cmd_id, cmd in commands_data.items():
            if not isinstance(cmd, dict) or cmd.get("processed", False):
                continue

            cmd_type = cmd.get("type")
            logger.info(f"📩 [FirebaseBridge] 안드로이드 원격 명령 수신: {cmd_type} (ID: {cmd_id})")

            try:
                if cmd_type == "KILL_SWITCH":
                    # 마스터 킬스위치 실행
                    results = await self.orchestrator.master_kill_switch()
                    self._mark_command_processed(cmd_id, status="SUCCESS", detail=results)

                elif cmd_type == "TOGGLE_SLOT":
                    slot_id = cmd.get("slot_id")
                    is_active = cmd.get("is_active", True)
                    if slot_id:
                        await self.orchestrator.toggle_slot(slot_id, is_active)
                        self._mark_command_processed(cmd_id, status="SUCCESS", detail=f"{slot_id}={is_active}")

                elif cmd_type == "CLOSE_POSITION":
                    slot_id = cmd.get("slot_id")
                    ticker = cmd.get("ticker")
                    if slot_id and ticker:
                        trade = await self.orchestrator.close_slot_position(slot_id, ticker)
                        self._mark_command_processed(
                            cmd_id,
                            status="SUCCESS" if trade else "FAILED",
                            detail=trade.model_dump() if trade else "Position not found"
                        )
            except Exception as e:
                logger.error(f"❌ [FirebaseBridge] 명령({cmd_type}) 처리 중 오류: {e}", exc_info=True)
                self._mark_command_processed(cmd_id, status="ERROR", detail=str(e))

    def _mark_command_processed(self, cmd_id: str, status: str, detail: Any):
        """명령 완료 상태 기록"""
        if self.mock_mode or not self.is_connected:
            return
        try:
            ref = db.reference(f'/commands/{cmd_id}')
            ref.update({
                "processed": True,
                "status": status,
                "detail": detail,
                "processed_at": datetime.now().isoformat()
            })
        except Exception as e:
            logger.error(f"❌ [FirebaseBridge] 명령 상태 업데이트 실패: {e}")

    async def _sync_loop(self):
        """1~2초 주기 계좌 현황, 포지션, 마켓필터 Firebase RTDB 푸시"""
        while self.is_running:
            try:
                if self.is_connected and not self.mock_mode:
                    accounts = await self.orchestrator.ledger.get_accounts()
                    positions = await self.orchestrator.ledger.get_positions()
                    recent_trades = await self.orchestrator.ledger.get_recent_trades(limit=10)

                    # 포트폴리오 종합 계산
                    total_asset = sum(a.total_asset for a in accounts)
                    total_cash = sum(a.cash_balance for a in accounts)
                    total_stock = sum(a.stock_value for a in accounts)
                    total_profit = sum(a.total_profit for a in accounts)
                    initial_capital = sum(a.initial_capital for a in accounts)
                    total_profit_rate = (total_profit / initial_capital) * 100.0 if initial_capital > 0 else 0.0

                    payload = {
                        "status": "ONLINE",
                        "server_status": "ONLINE",
                        "last_heartbeat": int(datetime.now().timestamp() * 1000),
                        "portfolio": {
                            "initial_capital": initial_capital,
                            "total_asset": total_asset,
                            "total_cash": total_cash,
                            "total_stock": total_stock,
                            "total_stock_value": total_stock,
                            "total_profit": total_profit,
                            "total_profit_rate": round(total_profit_rate, 2),
                            "open_positions_count": len(positions),
                            "updated_at": datetime.now().isoformat()
                        },
                        "market_filter": {
                            "kospi_index": self.orchestrator.market_filter.kospi_index,
                            "kospi_20ma": self.orchestrator.market_filter.kospi_20ma,
                            "is_bear_market": self.orchestrator.market_filter.is_bear_market,
                            "is_panic_sell_day": self.orchestrator.market_filter.is_panic_sell_day,
                        },
                        "slots": {
                            acc.slot_id: {
                                **acc.model_dump(mode='json'),
                                "positions": [
                                    p.model_dump(mode='json') for p in positions if p.slot_id == acc.slot_id
                                ]
                            }
                            for acc in accounts
                        },
                        "recent_trades": [
                            t.model_dump(mode='json') for t in recent_trades
                        ],
                        "intelligence": {
                            "daily_review": getattr(self.orchestrator, "latest_daily_review", ""),
                            "weekly_review": getattr(self.orchestrator, "latest_weekly_review", ""),
                            "morning_briefing": getattr(self.orchestrator, "latest_morning_briefing", ""),
                            "screening_candidates": getattr(self.orchestrator, "latest_screening_candidates", {}),
                            "updated_at": datetime.now().isoformat()
                        }
                    }

                    # Firebase RTDB에 한 번의 atomic update로 전송 (대역폭 및 쿼터 최소화, datetime 안전 직렬화)
                    clean_payload = json.loads(json.dumps(payload, default=str))
                    ref = db.reference('/system_status')
                    ref.set(clean_payload)

                await asyncio.sleep(1.5)  # 1.5초 주기 동기화
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [FirebaseBridge] 동기화 루프 오류: {e}")
                await asyncio.sleep(5)

    def sync_app_version(
        self,
        version_code: int = 6,
        version_name: str = "1.0.5",
        apk_url: str = "https://github.com/kjun73/The_Stock/releases/download/v1.0.5/app-debug.apk",
        notes: str = "개장일 자동 기동/종료 엔진 및 모바일 원격 제어 보안 개편",
        is_force: bool = False
    ):
        """안드로이드 인앱 자동 업데이트 메타데이터 동기화"""
        if self.mock_mode or not self.is_connected:
            return
        try:
            ref = db.reference('/app_version')
            ref.set({
                "versionCode": version_code,
                "versionName": version_name,
                "apkUrl": apk_url,
                "notes": notes,
                "isForce": is_force,
                "updatedAt": datetime.now().isoformat()
            })
            logger.info("📱 [FirebaseBridge] /app_version 메타데이터 동기화 완료")
        except Exception as e:
            logger.warning(f"⚠️ [FirebaseBridge] /app_version 동기화 실패: {e}")
