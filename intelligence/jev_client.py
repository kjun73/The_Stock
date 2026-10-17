import asyncio
import logging
import time
from typing import Dict, Optional, Tuple
import httpx

from core.config import settings
from core.models import TickData

logger = logging.getLogger(__name__)


class JevCascadeClient:
    """
    JEV 1.13 2단계 캐스케이드(Cascade) 가짜신호 판별 클라이언트:
    - 1단계: 로컬 파이썬 정량 필터 (0.1ms, $0)
    - 2단계: OpenRouter typesafe/jev-1.13 API (수치 JSON, 700ms 타임아웃, Safe Reject)
    """
    def __init__(self):
        self.cooldown_tracker: Dict[str, float] = {}  # 종목별 5분 쿨다운 타임스탬프
        self.consecutive_failures: int = 0
        self.circuit_broken: bool = False

    def check_stage1_filter(
        self,
        tick: TickData,
        target_price: float,
        avg_vol_20m: float,
        vol_1m: float
    ) -> Tuple[bool, str]:
        """
        1단계 로컬 정량 필터 (0.1ms 실행, 95% 노이즈 사전 차단):
        - 목표가 돌파 여부
        - 체결강도 >= 110.0%
        - 1분 거래량 >= 20분 평균의 250%
        - 5분 쿨다운 미적용
        """
        now = time.time()
        # 1. 쿨다운 검사
        if tick.ticker in self.cooldown_tracker:
            if now - self.cooldown_tracker[tick.ticker] < 300.0:  # 5분(300초)
                return False, "COOLDOWN_ACTIVE"

        # 2. 목표가 돌파 검사
        if tick.price < target_price:
            return False, "BELOW_TARGET_PRICE"

        # 3. 체결강도 검사
        if tick.exec_strength < 110.0:
            return False, f"EXEC_STRENGTH_LOW_{tick.exec_strength:.1f}%"

        # 4. 거래량 폭발 검사
        if avg_vol_20m > 0 and vol_1m < (avg_vol_20m * 2.5):
            return False, "VOLUME_SURGE_INSUFFICIENT"

        return True, "STAGE1_PASSED"

    async def evaluate_stage2_jev(
        self,
        ticker: str,
        tick: TickData,
        target_price: float
    ) -> Tuple[bool, float, str]:
        """
        2단계 OpenRouter typesafe/jev-1.13 API 호출:
        - 반환: (is_approved, fakeout_prob, reason)
        - 700ms 타임아웃 초과 시 Safe Reject (안전 진입 거절)
        """
        # API 키가 없거나 서킷 브레이커 작동 시 모의 승인 또는 기본 거절
        api_key = settings.OPENROUTER_API_KEY
        if not api_key or api_key.startswith("your_"):
            # 개발/테스트용 시뮬레이션: 체결강도가 120% 이상이면 승인
            sim_prob = 0.15 if tick.exec_strength >= 120.0 else 0.45
            is_approved = sim_prob < 0.25
            return is_approved, sim_prob, "MOCK_JEV_EVALUATION"

        if self.circuit_broken:
            return False, 1.0, "CIRCUIT_BREAKER_ACTIVE_SAFE_REJECT"

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/the-stock-trading",
            "X-Title": "The Stock JEV Fast Decision",
        }

        # 정량 데이터만 담은 경량 JSON 프롬프트 구성
        payload = {
            "model": "typesafe/jev-1.13",
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Evaluate fakeout probability for stock breakout:\n"
                        f"- Ticker: {ticker}\n"
                        f"- Current Price: {tick.price}\n"
                        f"- Target Price: {target_price}\n"
                        f"- Execution Strength: {tick.exec_strength}%\n"
                        f"- Bid/Ask Ratio: {tick.bid_ask_ratio}\n"
                        f"Output JSON strictly with field 'fakeout_probability' (0.0 to 1.0)."
                    )
                }
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }

        start_t = time.monotonic()
        try:
            timeout_sec = settings.JEV_TIMEOUT_MS / 1000.0  # 700ms = 0.7s
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                res = await client.post(url, headers=headers, json=payload)
                elapsed_ms = (time.monotonic() - start_t) * 1000.0

                if res.status_code == 200:
                    self.consecutive_failures = 0
                    result_json = res.json()
                    content = result_json["choices"][0]["message"]["content"]
                    import json
                    parsed = json.loads(content)
                    fakeout_prob = float(parsed.get("fakeout_probability", 0.5))

                    is_approved = fakeout_prob < 0.25
                    reason = f"JEV_OK_{elapsed_ms:.0f}ms_PROB_{fakeout_prob:.2f}"

                    # 진입 승인 또는 검사 후 5분간 동일 종목 쿨다운 적용
                    self.cooldown_tracker[ticker] = time.time()
                    return is_approved, fakeout_prob, reason
                else:
                    raise RuntimeError(f"HTTP_{res.status_code}")

        except (httpx.TimeoutException, asyncio.TimeoutError):
            logger.warning(f"⚡ [JEV 타임아웃 {settings.JEV_TIMEOUT_MS}ms 초과] {ticker} -> Safe Reject 적용")
            self._handle_failure()
            self.cooldown_tracker[ticker] = time.time()
            return False, 1.0, "JEV_TIMEOUT_SAFE_REJECT"
        except Exception as e:
            logger.warning(f"JEV API 호출 오류: {e} -> Safe Reject 적용")
            self._handle_failure()
            self.cooldown_tracker[ticker] = time.time()
            return False, 1.0, f"JEV_ERROR_{e}"

    def _handle_failure(self):
        self.consecutive_failures += 1
        if self.consecutive_failures >= 5:
            self.circuit_broken = True
            logger.critical("🚨 [JEV 서킷 브레이커 트립] 5회 연속 실패로 슬롯 C 신규 진입 일시 동결!")
