import asyncio
import http.server
import json
import logging
import os
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import socket
from typing import Optional

# 프로젝트 루트 경로 설정
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 로그 디렉토리 및 파일 핸들러 설정
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "sidecar_daemon.log"

formatter = logging.Formatter("%(asctime)s [%(levelname)s] (SidecarDaemon) %(message)s")

class UnbufferedFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

file_handler = UnbufferedFileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

logger = logging.getLogger("SidecarDaemon")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

try:
    import firebase_admin
    from firebase_admin import credentials, db
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False
    logger.error("❌ firebase-admin이 설치되지 않았습니다.")


APK_PATH = PROJECT_ROOT / "android-app" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"


def get_local_ip() -> str:
    """홈 PC의 로컬 네트워크 IP를 자동 조회합니다."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.45.12"


class ApkFileHandler(http.server.BaseHTTPRequestHandler):
    """무선 인앱 업데이트 APK 전용 초경량 스트리밍 핸들러 (포트 8080)"""
    def do_GET(self):
        if self.path in ("/The_Stock_C2.apk", "/api/download/apk"):
            if APK_PATH.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Length", str(APK_PATH.stat().st_size))
                self.send_header("Content-Disposition", 'attachment; filename="The_Stock_C2.apk"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with open(APK_PATH, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
            else:
                self.send_error(404, "APK not found")
        elif self.path in ("/api/version", "/version"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            local_ip = get_local_ip()
            data = json.dumps({
                "versionCode": 7,
                "versionName": "1.0.9",
                "apkUrl": f"http://{local_ip}:8080/The_Stock_C2.apk",
                "notes": "v1.0.9 KRX 공식 연동, 14일 자산 곡선(Equity Curve), 본전/트레일링 배지, 청산 손익비 요약 및 날짜별 복기 뷰어 탑재",
                "isForce": False,
                "hasApk": APK_PATH.is_file()
            }, ensure_ascii=False)
            self.wfile.write(data.encode("utf-8"))
    def do_HEAD(self):
        if self.path in ("/The_Stock_C2.apk", "/api/download/apk"):
            if APK_PATH.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Length", str(APK_PATH.stat().st_size))
                self.send_header("Content-Disposition", 'attachment; filename="The_Stock_C2.apk"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
            else:
                self.send_error(404, "APK not found")
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        logger.info(f"🌐 [ApkServer] {self.address_string()} - {format % args}")


def start_apk_server(port: int = 8080):
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), ApkFileHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        logger.info(f"📦 [ApkServer] C2 무선 업데이트 APK 서버 가동 완료: http://0.0.0.0:{port}")
        return server
    except Exception as e:
        logger.warning(f"⚠️ [ApkServer] APK 서버 바인딩 실패 (포트 {port}): {e}")
        return None


class ServerSidecarDaemon:
    """
    초경량 홈 PC 사이드카 데몬:
    - 백그라운드에서 상시 대기하며 Firebase RTDB (/server_control) 원격 명령 감시
    - 스마트폰 앱에서 [서버 기동] / [서버 종료] 클릭 시 백엔드 프로세스 자동 생성/종료
    """
    def __init__(self):
        self.server_process: Optional[subprocess.Popen] = None
        self.is_running = False
        self.last_handled_cmd_id = ""

    def initialize_firebase(self) -> bool:
        cred_path = PROJECT_ROOT / "firebase_credentials.json"
        if not cred_path.is_file():
            logger.error(f"❌ Firebase 자격증명 파일 없음: {cred_path}")
            return False

        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(str(cred_path))
                firebase_admin.initialize_app(cred, {
                    'databaseURL': 'https://thestock-513c0-default-rtdb.asia-southeast1.firebasedatabase.app/'
                })
            return True
        except Exception as e:
            logger.error(f"❌ Firebase 초기화 실패: {e}")
            return False

    def update_server_state(self, state: str):
        """서버 상태 Firebase RTDB에 기록 (RUNNING, STOPPED, STARTING)"""
        try:
            ref = db.reference('/server_control')
            ref.update({
                "state": state,
                "pid": self.server_process.pid if self.server_process and self.server_process.poll() is None else 0,
                "last_updated": int(time.time() * 1000)
            })
            logger.info(f"📡 [Sidecar] 서버 상태 동기화: {state}")
        except Exception as e:
            logger.warning(f"⚠️ 상태 동기화 실패: {e}")

    def start_trading_server(self) -> bool:
        """The Stock 백엔드 서버(FastAPI/Uvicorn) 프로세스 기동"""
        if self.server_process and self.server_process.poll() is None:
            logger.info("ℹ️ 서버가 이미 실행 중입니다.")
            self.update_server_state("RUNNING")
            return True

        self.update_server_state("STARTING")
        logger.info("🚀 [Sidecar] The Stock 트레이딩 코어 서버 기동 시작...")

        venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
        if not venv_python.is_file():
            venv_python = Path(sys.executable)

        cmd = [
            str(venv_python),
            "-m", "uvicorn",
            "api.server:app",
            "--host", "0.0.0.0",
            "--port", "8000"
        ]

        try:
            # stdout/stderr는 파이프 대신 파일 또는 NUL로 분리하여 버퍼 블로킹 방지
            log_dir = PROJECT_ROOT / "logs"
            log_dir.mkdir(exist_ok=True)
            server_log = open(log_dir / "server_daemon_stdout.log", "a", encoding="utf-8")

            self.server_process = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=server_log,
                stderr=subprocess.STDOUT
            )
            time.sleep(2)  # 초기 기동 확인

            if self.server_process.poll() is None:
                logger.info(f"✅ [Sidecar] 서버 프로세스 기동 성공! (PID: {self.server_process.pid})")
                self.update_server_state("RUNNING")
                return True
            else:
                logger.error("❌ [Sidecar] 서버 프로세스가 즉시 비정상 종료되었습니다.")
                self.update_server_state("STOPPED")
                return False
        except Exception as e:
            logger.error(f"❌ [Sidecar] 서버 기동 예외: {e}")
            self.update_server_state("STOPPED")
            return False

    def stop_trading_server(self) -> bool:
        """트레이딩 서버 프로세스 안전 종료"""
        if not self.server_process or self.server_process.poll() is not None:
            logger.info("ℹ️ 서버가 이미 종료되어 있습니다.")
            self.update_server_state("STOPPED")
            return True

        logger.info(f"🛑 [Sidecar] 서버 프로세스(PID: {self.server_process.pid}) 종료 중...")
        try:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
                self.server_process.wait(timeout=2)
            logger.info("✅ [Sidecar] 서버 프로세스 안전 종료 완료.")
        except Exception as e:
            logger.error(f"❌ [Sidecar] 서버 종료 중 에러: {e}")
        finally:
            self.server_process = None
            self.update_server_state("STOPPED")

            # Firebase RTDB system_status도 즉시 OFFLINE 마킹
            try:
                ref = db.reference('/system_status')
                ref.update({"status": "OFFLINE", "server_status": "OFFLINE", "last_heartbeat": 0})
            except Exception:
                pass

        return True

    def run(self):
        """메인 데몬 감시 루프"""
        if not self.initialize_firebase():
            return

        self.is_running = True
        logger.info("👁️ [SidecarDaemon] 스마트폰 원격 제어 명령 수신 대기 중...")
        self.update_server_state("STOPPED")

        # 1. 무선 인앱 업데이트 APK 전용 서버 상시 가동
        start_apk_server(port=8080)

        # 2. Firebase RTDB /app_version 메타데이터 최신화 (글로벌 CDN 초고속 무선 다운로드)
        try:
            github_cdn_apk = "https://github.com/kjun73/The_Stock/releases/download/v1.0.4/app-debug.apk"
            ref_ver = db.reference('/app_version')
            ref_ver.set({
                "versionCode": 5,
                "versionName": "1.0.4",
                "apkUrl": github_cdn_apk,
                "notes": "15:40 AI 일일 결산 복기 및 장후 스크리닝 후보군 실시간 연동",
                "isForce": False,
                "updatedAt": datetime.now().isoformat()
            })
            logger.info(f"📱 [SidecarDaemon] /app_version 메타데이터 동기화 완료 (CDN: {github_cdn_apk})")
        except Exception as e:
            logger.warning(f"⚠️ /app_version 동기화 실패: {e}")

        ref = db.reference('/server_control')

        while self.is_running:
            try:
                # 1. 서버 프로세스 생존 여부 점검
                if self.server_process:
                    if self.server_process.poll() is not None:
                        logger.warning("⚠️ 서버 프로세스가 종료됨을 감지했습니다.")
                        self.server_process = None
                        self.update_server_state("STOPPED")

                # 2. 스마트폰 원격 명령 폴링
                control_data = ref.get()
                if isinstance(control_data, dict):
                    cmd = control_data.get("command")
                    cmd_id = control_data.get("command_id", "")

                    if cmd and cmd_id and cmd_id != self.last_handled_cmd_id:
                        self.last_handled_cmd_id = cmd_id
                        logger.info(f"📩 [Sidecar] 스마트폰 원격 명령 수신: {cmd} (ID: {cmd_id})")

                        if cmd == "START":
                            self.start_trading_server()
                        elif cmd == "STOP":
                            self.stop_trading_server()

                        # 명령 처리 완료 마킹
                        ref.update({
                            "command": "NONE",
                            "last_handled_id": cmd_id
                        })

                time.sleep(1.5)
            except KeyboardInterrupt:
                logger.info("종료 신호 수신")
                break
            except Exception as e:
                logger.error(f"루프 오류: {e}")
                time.sleep(3)

        self.stop_trading_server()


def acquire_single_instance_lock(port: int = 28492):
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", port))
        lock_socket.listen(1)
        return lock_socket
    except socket.error:
        return None


if __name__ == "__main__":
    _lock = acquire_single_instance_lock()
    if not _lock:
        logger.warning("⚠️ 사이드카 데몬이 이미 실행 중입니다. 중복 프로세스를 종료합니다.")
        sys.exit(0)
    daemon = ServerSidecarDaemon()
    daemon.run()
