import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

US_SYMBOLS: Dict[str, str] = {
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "sox": "^SOX",
    "ewy": "EWY",
    "vix": "^VIX",
    "usdkrw": "KRW=X",
}


class USMarketSnapshot(BaseModel):
    """전일 미국 증시 마감 스냅샷 및 매크로 레짐 판정 결과"""
    sp500_close: float = 0.0
    sp500_chg_pct: float = 0.0
    nasdaq_close: float = 0.0
    nasdaq_chg_pct: float = 0.0
    sox_close: float = 0.0
    sox_chg_pct: float = 0.0
    ewy_close: float = 0.0
    ewy_chg_pct: float = 0.0
    vix_close: float = 18.0
    vix_chg_pct: float = 0.0
    usdkrw_close: float = 0.0
    usdkrw_chg_pct: float = 0.0
    regime: str = "US_NEUTRAL"  # US_PANIC | US_RISK_OFF | US_NEUTRAL | US_RISK_ON
    is_fresh: bool = False
    last_us_date: str = ""
    summary_line: str = "미국장 데이터 미수신 (중립 모드)"
    fetched_at: str = Field(default_factory=lambda: datetime.now(KST).isoformat())


def is_us_data_fresh(last_ts: Optional[int], now_kst: Optional[datetime] = None) -> bool:
    """
    Yahoo Finance 일봉 시작 타임스탬프 기준으로 전일 미국장 데이터 신선도 검증:
    - 한국 월요일/주말: 직전 금요일(23:30 KST)까지 허용 (최대 72시간)
    - 한국 화~금요일: 직전 영업일 밤(22:30~23:30 KST)까지 허용 (최대 30시간) -> 미국 휴장일 중복 적용 방지
    """
    if not last_ts or last_ts <= 0:
        return False
    now_dt = now_kst or datetime.now(KST)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=KST)
    last_dt = datetime.fromtimestamp(last_ts, tz=KST)
    age_hours = (now_dt - last_dt).total_seconds() / 3600.0
    if age_hours < 0:
        return True
    max_age_hours = 72.0 if now_dt.weekday() in (0, 5, 6) else 30.0
    return age_hours <= max_age_hours


def classify_us_regime(
    nasdaq_chg: float,
    sox_chg: float,
    ewy_chg: float,
    vix_close: float,
    is_fresh: bool = True,
) -> str:
    """
    미국장 등락률 기반 4단계 매크로 레짐 판정:
    - 미국 휴장/데이터 오래됨(is_fresh=False) 시 중복 차단을 막기 위해 US_NEUTRAL 반환
    - US_PANIC: EWY <= -3.0% or SOX <= -5.0% or (VIX >= 28.0 and EWY < 0)
    - US_RISK_OFF: EWY <= -1.5% or SOX <= -3.0% or (NASDAQ <= -1.5% and EWY < 0)
    - US_RISK_ON: EWY >= +1.5% and SOX >= +2.0%
    - US_NEUTRAL: 그 외
    """
    if not is_fresh:
        return "US_NEUTRAL"

    if ewy_chg <= -3.0 or sox_chg <= -5.0 or (vix_close >= 28.0 and ewy_chg < 0.0):
        return "US_PANIC"

    if ewy_chg <= -1.5 or sox_chg <= -3.0 or (nasdaq_chg <= -1.5 and ewy_chg < 0.0):
        return "US_RISK_OFF"

    if ewy_chg >= 1.5 and sox_chg >= 2.0:
        return "US_RISK_ON"

    return "US_NEUTRAL"


class USMarketMonitor:
    """
    국내장 개시 전(08:45) 미국 증시 마감 지표를 수집하여
    마켓필터·JEV·장전 브리핑에 매크로 레짐을 제공하는 경량 비동기 모니터
    """
    def __init__(self, timeout_sec: float = 3.5):
        self.timeout_sec = timeout_sec
        self.latest_snapshot: Optional[USMarketSnapshot] = None

    async def _fetch_single_symbol(
        self, client: httpx.AsyncClient, key: str, symbol: str
    ) -> Tuple[str, float, float, int]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d"
        try:
            res = await client.get(url)
            if res.status_code != 200:
                return key, 0.0, 0.0, 0
            data = res.json()["chart"]["result"][0]
            ts_raw = data.get("timestamp") or []
            closes_raw = data.get("indicators", {}).get("quote", [{}])[0].get("close") or []
            valid_pairs = [(t, float(c)) for t, c in zip(ts_raw, closes_raw) if c is not None]
            if not valid_pairs:
                return key, 0.0, 0.0, 0
            last_ts, last_c = valid_pairs[-1]
            if len(valid_pairs) >= 2:
                prev_c = valid_pairs[-2][1]
            else:
                prev_c = float(data.get("meta", {}).get("chartPreviousClose", last_c) or last_c)
            chg_pct = ((last_c - prev_c) / prev_c) * 100.0 if prev_c > 0 else 0.0
            return key, round(last_c, 2), round(chg_pct, 2), int(last_ts)
        except Exception as e:
            logger.debug(f"[USMarketMonitor] {symbol} 조회 예외: {e}")
            return key, 0.0, 0.0, 0

    async def fetch_snapshot(self, now_kst: Optional[datetime] = None) -> USMarketSnapshot:
        """미국장 핵심 지표 6종을 병렬 수집하고 4단계 레짐 스냅샷을 반환 (장애 시 중립 폴백)"""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        results: Dict[str, Tuple[float, float, int]] = {}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec, headers=headers) as client:
                tasks = [
                    self._fetch_single_symbol(client, key, sym)
                    for key, sym in US_SYMBOLS.items()
                ]
                fetched = await asyncio.gather(*tasks, return_exceptions=True)
                for item in fetched:
                    if isinstance(item, tuple):
                        k, close_v, chg_v, ts_v = item
                        results[k] = (close_v, chg_v, ts_v)
        except Exception as e:
            logger.warning(f"⚠️ [USMarketMonitor] 미국장 데이터 수집 실패 (중립 폴백): {e}")

        # 대표 지수 타임스탬프로 신선도 판정 (SOX/EWY/NASDAQ 중 유효한 최신 타임스탬프 사용)
        ref_ts = max(
            results.get("ewy", (0.0, 0.0, 0))[2],
            results.get("sox", (0.0, 0.0, 0))[2],
            results.get("nasdaq", (0.0, 0.0, 0))[2],
        )
        fresh = is_us_data_fresh(ref_ts, now_kst=now_kst)
        last_us_date = datetime.fromtimestamp(ref_ts, tz=KST).strftime("%Y-%m-%d") if ref_ts > 0 else ""

        sp_c, sp_chg, _ = results.get("sp500", (0.0, 0.0, 0))
        nq_c, nq_chg, _ = results.get("nasdaq", (0.0, 0.0, 0))
        sox_c, sox_chg, _ = results.get("sox", (0.0, 0.0, 0))
        ewy_c, ewy_chg, _ = results.get("ewy", (0.0, 0.0, 0))
        vix_c, vix_chg, _ = results.get("vix", (18.0, 0.0, 0))
        fx_c, fx_chg, _ = results.get("usdkrw", (0.0, 0.0, 0))

        regime = classify_us_regime(
            nasdaq_chg=nq_chg,
            sox_chg=sox_chg,
            ewy_chg=ewy_chg,
            vix_close=vix_c if vix_c > 0 else 18.0,
            is_fresh=fresh,
        )

        if ref_ts == 0:
            summary_line = "미국장 데이터 수집 실패 -> US_NEUTRAL 안전 폴백"
        else:
            stale_tag = "" if fresh else " [휴장/이전마감]"
            summary_line = (
                f"NASDAQ {nq_chg:+.2f}%, SOX {sox_chg:+.2f}%, EWY {ewy_chg:+.2f}%, "
                f"S&P500 {sp_chg:+.2f}%, VIX {vix_c:.1f}, USD/KRW {fx_c:,.0f}원 ({regime}{stale_tag})"
            )

        snapshot = USMarketSnapshot(
            sp500_close=sp_c,
            sp500_chg_pct=sp_chg,
            nasdaq_close=nq_c,
            nasdaq_chg_pct=nq_chg,
            sox_close=sox_c,
            sox_chg_pct=sox_chg,
            ewy_close=ewy_c,
            ewy_chg_pct=ewy_chg,
            vix_close=vix_c,
            vix_chg_pct=vix_chg,
            usdkrw_close=fx_c,
            usdkrw_chg_pct=fx_chg,
            regime=regime,
            is_fresh=fresh,
            last_us_date=last_us_date,
            summary_line=summary_line,
        )
        self.latest_snapshot = snapshot
        logger.info(f"🌎 [USMarketMonitor] {summary_line}")
        return snapshot
