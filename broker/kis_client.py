import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set
import httpx
import websockets

from core.config import settings
from core.models import TickData

logger = logging.getLogger(__name__)


class StrictRateLimiter:
    """초당 호출 횟수(기본 15회)를 엄격히 제한하여 버스트 429 에러를 방지하는 락 기반 레이트 리미터"""
    def __init__(self, calls_per_second: int = 15):
        self.interval = 1.0 / calls_per_second
        self.lock = asyncio.Lock()
        self.last_called_at = 0.0

    async def execute(self, coro_func, *args, **kwargs):
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_called_at
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self.last_called_at = time.monotonic()
            return await coro_func(*args, **kwargs)


class WebSocketQuotaManager:
    """KIS 웹소켓 41종목 단일 세션 제한을 준수하는 37종목 쿼터 관리자"""
    MAX_TOTAL_QUOTA = 37

    def __init__(self):
        self.held_positions: Set[str] = set()       # 1순위: 보유 종목 (최대 12)
        self.momentum_watchlist: List[str] = []     # 2순위: 슬롯 C 단타 감시 (최대 25)

    def update_held_positions(self, tickers: List[str]):
        self.held_positions = set(tickers)

    def update_momentum_watchlist(self, tickers: List[str]):
        self.momentum_watchlist = tickers

    def get_subscribed_tickers(self) -> List[str]:
        # 1순위 보유종목 우선 배정 후 남은 슬롯을 2순위 감시종목에 할당
        selected = list(self.held_positions)[:12]
        remaining_slots = self.MAX_TOTAL_QUOTA - len(selected)

        for ticker in self.momentum_watchlist:
            if ticker not in selected and len(selected) < self.MAX_TOTAL_QUOTA:
                selected.append(ticker)

        return selected


class KisClient:
    def __init__(self):
        self.rate_limiter = StrictRateLimiter(calls_per_second=15)
        self.quota_manager = WebSocketQuotaManager()
        self.access_token: Optional[str] = None
        self.approval_key: Optional[str] = None
        self.token_expired_at: float = 0.0
        self.is_connected: bool = False
        self.last_heartbeat: float = time.time()
        self._ws_task: Optional[asyncio.Task] = None
        self._tick_callbacks: List[Callable[[TickData], asyncio.Future]] = []

    def register_tick_callback(self, callback):
        self._tick_callbacks.append(callback)

    async def get_access_token(self) -> str:
        """OAuth 2.0 접근 토큰 발급/갱신 (더미 키일 경우 시뮬레이션 토큰 반환)"""
        now = time.time()
        if self.access_token and now < self.token_expired_at:
            return self.access_token

        # 실제 키가 입력되지 않은 경우 시뮬레이션 모드로 동작
        if settings.KIS_APP_KEY in ("mock_key", "your_prod_app_key_here"):
            self.access_token = "mock_access_token_for_shadow_trading"
            self.token_expired_at = now + 86400
            self.is_connected = True
            return self.access_token

        url = f"{settings.kis_base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": settings.KIS_APP_KEY,
            "appsecret": settings.KIS_APP_SECRET,
        }

        async def _fetch():
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=body)
                res.raise_for_status()
                data = res.json()
                self.access_token = data["access_token"]
                self.token_expired_at = now + int(data.get("expires_in", 86400)) - 600
                self.is_connected = True
                return self.access_token

        return await self.rate_limiter.execute(_fetch)

    async def fetch_current_price(self, ticker: str) -> Optional[TickData]:
        """주식 현재가 및 1호가 조회"""
        token = await self.get_access_token()

        if token.startswith("mock_"):
            # 시뮬레이션용 더미 시세 생성
            base_p = 50000.0 if ticker != "069500" else 35000.0
            return TickData(
                ticker=ticker,
                price=base_p,
                volume=15000,
                bid_price=base_p - 50.0,
                ask_price=base_p + 50.0,
                exec_strength=115.0,
                bid_ask_ratio=1.2,
                timestamp=datetime.now()
            )

        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": settings.KIS_APP_KEY,
            "appsecret": settings.KIS_APP_SECRET,
            "tr_id": "FHKST01010100",
        }
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
        }

        async def _fetch():
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(url, headers=headers, params=params)
                if res.status_code == 200:
                    out = res.json().get("output", {})
                    price = float(out.get("stck_prpr", 0))
                    ask = float(out.get("askp1", price))
                    bid = float(out.get("bidp1", price))
                    cttr = float(out.get("cttr", 100.0))
                    vol = int(out.get("acml_vol", 0))
                    self.last_heartbeat = time.time()
                    return TickData(
                        ticker=ticker,
                        price=price,
                        volume=vol,
                        bid_price=bid,
                        ask_price=ask,
                        exec_strength=cttr,
                        bid_ask_ratio=1.0,
                        timestamp=datetime.now()
                    )
                return None

        return await self.rate_limiter.execute(_fetch)

    async def fetch_top_volume_tickers(self, count: int = 25) -> List[str]:
        """당일 거래대금 상위 종목 리스트 조회 (슬롯 C용)"""
        token = await self.get_access_token()
        if token.startswith("mock_"):
            # 기본 대형주/유동성 우량주 25종목
            return [
                "005930", "000660", "373220", "207940", "005380",
                "068270", "000270", "105560", "055550", "035420",
                "006400", "051910", "012330", "035720", "028260",
                "086520", "247540", "003670", "032830", "009150",
                "010130", "000100", "018260", "003490", "034730"
            ][:count]

        # 실제 KIS 순위 API 호출 (FHPST01710000)
        url = f"{settings.kis_base_url}/uapi/domestic-stock/v1/ranking/trade-vol-rank"
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": settings.KIS_APP_KEY,
            "appsecret": settings.KIS_APP_SECRET,
            "tr_id": "FHPST01710000",
        }
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20171",
            "fid_input_iscd": "0000",
            "fid_div_cls_code": "0",
            "fid_blng_cls_code": "0",
            "fid_trgt_cls_code": "111111111",
            "fid_trgt_exls_cls_code": "000000",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_input_date_1": "",
        }

        async def _fetch():
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.get(url, headers=headers, params=params)
                if res.status_code == 200:
                    outputs = res.json().get("output", [])
                    return [item["mksc_shrn_iscd"] for item in outputs[:count]]
                return []

        try:
            return await self.rate_limiter.execute(_fetch)
        except Exception as e:
            logger.warning(f"거래대금 순위 조회 실패, 기본 종목 사용: {e}")
            return ["005930", "000660", "005380", "035420", "068270"]

    async def stop(self):
        """웹소켓 세션 및 백그라운드 태스크 정상 종료"""
        self.is_connected = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        logger.info("KIS 클라이언트 연결 종료")
