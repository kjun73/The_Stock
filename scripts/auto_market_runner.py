import datetime
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Optional, Tuple

# 프로젝트 루트 경로 등록
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 로그 디렉토리 및 파일 설정
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "auto_runner.log"

formatter = logging.Formatter("%(asctime)s [%(levelname)s] (AutoMarketRunner) %(message)s")
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)

logger = logging.getLogger("AutoMarketRunner")
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


# ----------------------------------------------------
# 한국거래소(KRX) 주식시장 휴장일 캘린더 (2025 ~ 2027)
# ----------------------------------------------------
KRX_HOLIDAYS = {
    # 2025년
    "2025-01-01": "신정",
    "2025-01-27": "설날 연휴(임시공휴일)",
    "2025-01-28": "설날 연휴",
    "2025-01-29": "설날",
    "2025-01-30": "설날 연휴",
    "2025-03-03": "삼일절 대체공휴일",
    "2025-05-01": "근로자의 날(KRX 휴장)",
    "2025-05-05": "어린이날",
    "2025-05-06": "부처님오신날 대체공휴일",
    "2025-06-06": "현충일",
    "2025-08-15": "광복절",
    "2025-10-03": "개천절",
    "2025-10-06": "추석 연휴",
    "2025-10-07": "추석",
    "2025-10-08": "추석 연휴",
    "2025-10-09": "한글날",
    "2025-12-25": "성탄절",
    "2025-12-31": "연말 폐장일(KRX 휴장)",

    # 2026년
    "2026-01-01": "신정",
    "2026-02-16": "설날 연휴",
    "2026-02-17": "설날",
    "2026-02-18": "설날 연휴",
    "2026-03-02": "삼일절 대체공휴일",
    "2026-05-01": "근로자의 날(KRX 휴장)",
    "2026-05-05": "어린이날",
    "2026-05-25": "부처님오신날 대체공휴일",
    "2026-06-06": "현충일",
    "2026-08-17": "광복절 대체공휴일",
    "2026-09-24": "추석 연휴",
    "2026-09-25": "추석",
    "2026-09-26": "추석 연휴",
    "2026-10-05": "개천절 대체공휴일",
    "2026-10-09": "한글날",
    "2026-12-25": "성탄절",
    "2026-12-31": "연말 폐장일(KRX 휴장)",

    # 2027년
    "2027-01-01": "신정",
    "2027-02-05": "설날 연휴",
    "2027-02-06": "설날",
    "2027-02-08": "설날 대체공휴일",
    "2027-03-01": "삼일절",
    "2027-05-01": "근로자의 날(KRX 휴장)",
    "2027-05-05": "어린이날",
    "2027-05-13": "부처님오신날",
    "2027-06-06": "현충일",
    "2027-08-16": "광복절 대체공휴일",
    "2027-09-14": "추석 연휴",
    "2027-09-15": "추석",
    "2027-09-16": "추석 연휴",
    "2027-10-04": "개천절 대체공휴일",
    "2027-10-09": "한글날",
    "2027-12-25": "성탄절",
    "2027-12-31": "연말 폐장일(KRX 휴장)",
}


def is_krx_market_open_day(target_date: Optional[datetime.date] = None) -> Tuple[bool, str]:
    """
    한국거래소(KRX) 정규장 개장일 여부 판별:
    - 주말(토/일)은 100% 휴장
    - 법정 공휴일, 대체공휴일, 근로자의 날(5/1), 연말 폐장일(12/31)은 100% 휴장
    """
    if target_date is None:
        target_date = datetime.datetime.now().date()

    # 1. 주말 체크 (월=0, ... 금=4, 토=5, 일=6)
    weekday = target_date.weekday()
    if weekday == 5:
        return False, "토요일 (주말 휴장)"
    if weekday == 6:
        return False, "일요일 (주말 휴장)"

    # 2. KRX 공휴일 및 지정 휴장일 체크
    date_str = target_date.strftime("%Y-%m-%d")
    if date_str in KRX_HOLIDAYS:
        reason = KRX_HOLIDAYS[date_str]
        return False, f"KRX 휴장일 ({reason})"

    # 3. 12월 마지막 평일 폐장일 방어 체크 (12/31이 주말인 경우 그 전 마지막 평일)
    if target_date.month == 12 and target_date.day >= 28:
        # 12월 31일의 요일 확인
        dec_31 = datetime.date(target_date.year, 12, 31)
        last_business_day = dec_31
        while last_business_day.weekday() >= 5: # 토, 일이면 전일로
            last_business_day -= datetime.timedelta(days=1)
        if target_date == last_business_day:
            return False, "연말 마지막 영업일 납회 휴장"

    return True, "정규 거래일"


def is_server_already_running(port: int = 8000) -> bool:
    """로컬 포트 8000 바인딩 여부를 통해 서버 기동 여부 확인"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False


def start_trading_server() -> bool:
    """The Stock 트레이딩 코어 서버(FastAPI/Uvicorn) 백그라운드 독립 기동"""
    logger.info("🚀 [AutoMarketRunner] The Stock 트레이딩 코어 서버 기동 준비...")

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

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    server_stdout_log = open(log_dir / "server_stdout.log", "a", encoding="utf-8")

    # Windows 독립 프로세스 분리 플래그 (스케줄러가 종료되어도 서버 계속 실행)
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    creation_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=server_stdout_log,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags
        )
        logger.info(f"⏳ 서버 프로세스 생성됨 (PID: {proc.pid}), 가동 검증 대기...")
        time.sleep(3)

        if proc.poll() is None or is_server_already_running(8000):
            logger.info(f"✅ [AutoMarketRunner] 트레이딩 서버 기동 성공! (PID: {proc.pid}, http://0.0.0.0:8000)")
            return True
        else:
            logger.error("❌ [AutoMarketRunner] 서버 프로세스가 즉시 비정상 종료되었습니다.")
            return False
    except Exception as e:
        logger.error(f"❌ [AutoMarketRunner] 서버 기동 실패 예외: {e}", exc_info=True)
        return False


def main():
    now = datetime.datetime.now()
    today = now.date()
    logger.info("==================================================================")
    logger.info(f"📅 [AutoMarketRunner] 장전 자동 기동 검사 시작: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("==================================================================")

    # 1. 개장일 여부 확인
    is_open, reason = is_krx_market_open_day(today)
    if not is_open:
        logger.info(f"🏖️ [휴장일 감지] 오늘({today})은 증시 휴장일입니다: {reason}")
        logger.info("ℹ️ 매매 서버를 기동하지 않고 정상 대기 상태로 종료합니다.")
        return

    logger.info(f"📈 [개장일 확인] 오늘({today})은 정규 거래일입니다. ({reason})")

    # 2. 서버 실행 상태 확인
    if is_server_already_running(8000):
        logger.info("ℹ️ 포트 8000에서 매매 서버가 이미 정상 실행 중입니다. 중복 기동을 건너뜁니다.")
        return

    # 3. 매매 서버 자동 기동
    success = start_trading_server()
    if success:
        logger.info("🎯 [AutoMarketRunner] 08:35 장전 자동 기동 작업 완료 (08:45 스크리닝 및 08:50 브리핑 준비 완료)")
    else:
        logger.error("🚨 [AutoMarketRunner] 장전 자동 기동 작업 실패!")


if __name__ == "__main__":
    main()
