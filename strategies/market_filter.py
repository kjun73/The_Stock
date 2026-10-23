import logging
import urllib.request
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def fetch_real_kospi_index() -> tuple[float | None, float | None, float | None]:
    """
    네이버 금융 실시간 국내 증시 API를 통해 실제 코스피 지수, 대비, 등락률을 조회합니다.
    실패 시 (None, None, None) 반환.
    """
    url = "https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            item = data["datas"][0]
            close_p = float(item["closePrice"].replace(",", ""))
            change_p = float(item["compareToPreviousClosePrice"].replace(",", ""))
            ratio = float(item["fluctuationsRatio"].replace(",", ""))
            return close_p, change_p, ratio
    except Exception as e:
        logger.debug(f"[마켓필터] 네이버 실시간 코스피 조회 예외: {e}")
        return None, None, None


class MarketFilterManager:
    """
    코스피 20일 이동평균선 기반 대폭락장 방어 필터 (전략별 이원화 및 강반등 예외 적용):
    - 추세 추종 슬롯(A, C, D): 지수가 20일선 아래인 하락장에서는 원칙적으로 신규 매수 차단.
      * [핫픽스 예외 1] 슬롯 C (단타): 당일 지수 강반등(+0.8% 이상) 시 15:15 당일 청산 전제로 진입 전격 승인!
      * [핫픽스 예외 2] 슬롯 A, D (스윙): 당일 지수 폭등(+1.2% 이상) 시 하락장 방어막 일시 해제 및 진입 승인!
    - 역추세 평균회귀 슬롯(B): 20일선 아래여도 매수 허용 (단, 당일 -3.0% 이상 패닉셀 시에만 차단)
    """
    def __init__(self, base_index: float = 2600.0, ma20: float = 2580.0):
        self.base_index: float = base_index
        self.kospi_index: float = base_index
        self.kospi_20ma: float = ma20
        self.kospi_daily_change_pct: float = 0.5  # 당일 등락률 (%)
        self.kodex200_open: float = 0.0
        self.kodex200_prev_close: float = 0.0
        self.kodex200_price: float = 0.0
        self.kodex200_ma20: float = 0.0

    def refresh_from_history(self):
        """pykrx 및 네이버 금융을 활용해 실제 코스피 지수와 KODEX 200 20MA, 전일 종가를 동적 동기화"""
        real_p, real_change, real_ratio = fetch_real_kospi_index()

        try:
            from pykrx import stock
            today = datetime.now()
            start_date = (today - timedelta(days=40)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")
            df = stock.get_market_ohlcv_by_date(start_date, end_date, "069500")
            today_str = today.strftime("%Y-%m-%d")

            if not df.empty and len(df) >= 2:
                closes = df["종가"] if "종가" in df.columns else df.iloc[:, 3]
                ma20_val = float(closes.rolling(window=min(len(closes), 20)).mean().iloc[-1])
                latest_close = float(closes.iloc[-1])
                ratio = latest_close / ma20_val if ma20_val > 0 else 1.0

                # 전일 종가 정밀 판별 (당일 행이 포함되어 있으면 iloc[-2], 아니면 iloc[-1])
                last_row_date = str(df.index[-1])[:10]
                if today_str in last_row_date and len(df) >= 2:
                    self.kodex200_prev_close = float(closes.iloc[-2])
                else:
                    self.kodex200_prev_close = float(closes.iloc[-1])

                self.kodex200_price = latest_close
                self.kodex200_ma20 = ma20_val

                if real_p is not None and real_p > 1000.0:
                    self.base_index = round(real_p, 1)
                    self.kospi_index = round(real_p, 1)
                    self.kospi_20ma = round(real_p / ratio, 1) if ratio > 0 else round(real_p, 1)
                    if real_ratio is not None:
                        self.kospi_daily_change_pct = real_ratio
                    logger.info(
                        f"📊 [마켓필터] 실시간 거래소 코스피 지수 동기화 성공: "
                        f"현재지수 {self.kospi_index:.1f}pt ({self.kospi_daily_change_pct:+.2f}%), "
                        f"추정 20MA {self.kospi_20ma:.1f}pt (KODEX 200 전일종가 {self.kodex200_prev_close:.0f}원 / 20MA {ma20_val:.0f}원)"
                    )
                else:
                    self.kospi_20ma = 2600.0
                    self.base_index = round(2600.0 * ratio, 1)
                    self.kospi_index = self.base_index
                    logger.info(
                        f"📊 [마켓필터] KODEX 200 폴백 동기화: 기준지수 {self.base_index:.1f} / 20MA {self.kospi_20ma:.1f}"
                    )
        except Exception as e:
            logger.info(f"[마켓필터] 동적 이평선 동기화 폴백 (기본값 유지): {e}")
            if real_p is not None:
                self.base_index = real_p
                self.kospi_index = real_p
                if real_ratio is not None:
                    self.kospi_daily_change_pct = real_ratio

    def update_from_kodex200(self, tick_price: float):
        """KODEX 200(069500) 실시간 틱 기반 지수 및 전일 대비 등락률 동적 갱신"""
        self.kodex200_price = tick_price
        
        # 전일 종가 기준 당일 등락률 산출 (전일 종가가 없으면 첫 틱을 기준으로 폴백)
        base_p = self.kodex200_prev_close if self.kodex200_prev_close > 0 else self.kodex200_open
        if base_p <= 0:
            self.kodex200_open = tick_price
            base_p = tick_price

        daily_change = ((tick_price - base_p) / base_p) * 100.0 if base_p > 0 else 0.0
        current_index = round(self.base_index * (1.0 + daily_change / 100.0), 1)
        self.update_kospi_status(
            current_index=current_index,
            ma20=self.kospi_20ma,
            daily_change_pct=round(daily_change, 2)
        )

    def update_kospi_status(self, current_index: float, ma20: float, daily_change_pct: float):
        self.kospi_index = current_index
        self.kospi_20ma = ma20
        self.kospi_daily_change_pct = daily_change_pct

    @property
    def is_under_20ma(self) -> bool:
        """코스피 지수가 기술적으로 20일 이평선 아래인지 여부"""
        return self.kospi_index < self.kospi_20ma

    @property
    def is_bear_market(self) -> bool:
        """
        모바일 앱 및 외부 시스템에 노출되는 실질적 하락장 방어막 가동 여부:
        - 20일선 아래더라도 당일 +0.8% 이상 강반등 중이면 단타/모멘텀이 가동되므로 방어막 해제(False)
        """
        if not self.is_under_20ma:
            return False
        if self.is_strong_rebound_day:
            return False
        return True

    @property
    def is_panic_sell_day(self) -> bool:
        """당일 -3.0% 이하 급락 패닉셀 상태인지 여부"""
        return self.kospi_daily_change_pct <= -3.0

    @property
    def is_strong_rebound_day(self) -> bool:
        """당일 +0.8% 이상 강한 상승/반등 국면인지 여부 (단타 진입 승인)"""
        return self.kospi_daily_change_pct >= 0.8

    @property
    def is_super_rally_day(self) -> bool:
        """당일 +1.2% 이상 대폭등 국면인지 여부 (우량주 스윙 진입 승인)"""
        return self.kospi_daily_change_pct >= 1.2

    def can_enter(self, slot_id: str) -> bool:
        """슬롯별 신규 진입 가능 여부 판정"""
        # 1. 슬롯 B (ETF 역추세 볼린저 그리드): 당일 -3% 이상 패닉셀만 아니면 하락장에서도 분할 매수 허용
        if slot_id == "SLOT_B":
            if self.is_panic_sell_day:
                logger.warning(f"🛑 [마켓필터] 코스피 당일 {self.kospi_daily_change_pct:+.2f}% 급락 패닉셀 -> SLOT_B 매수 보류")
                return False
            return True

        # 2. 슬롯 C (변동성 돌파 단타): 당일 15:15 전량 청산하므로 오버나잇 위험 0%
        #    당일 지수가 +0.8% 이상 강반등 중이면 20일선 아래라도 돌파 매수 적극 허용!
        if slot_id == "SLOT_C":
            if self.is_panic_sell_day:
                logger.warning(f"🛑 [마켓필터] 당일 지수 {self.kospi_daily_change_pct:+.2f}% 패닉셀 -> SLOT_C 진입 차단")
                return False
            if self.is_strong_rebound_day:
                logger.info(
                    f"🚀 [마켓필터] 당일 코스피 강반등({self.kospi_daily_change_pct:+.2f}%) 감지 -> "
                    f"SLOT_C(돌파 단타) 진입 전격 승인"
                )
                return True
            if self.is_under_20ma:
                logger.warning(
                    f"🛑 [마켓필터] 코스피({self.kospi_index:.1f}pt) < 20일선({self.kospi_20ma:.1f}pt, 등락 {self.kospi_daily_change_pct:+.2f}%) "
                    f"하락장 방어막 가동 -> {slot_id} 신규 매수 차단 (현금 보존)"
                )
                return False
            return True

        # 3. 슬롯 A, D (수급/눌림목 스윙):
        #    스윙 전략은 오버나잇을 고려하여 20일선 아래일 때는 당일 +1.2% 이상 대폭등 시에만 진입 허용!
        if slot_id in ("SLOT_A", "SLOT_D"):
            if self.is_panic_sell_day:
                logger.warning(f"🛑 [마켓필터] 당일 지수 {self.kospi_daily_change_pct:+.2f}% 패닉셀 -> {slot_id} 매수 차단")
                return False
            if self.is_under_20ma and not self.is_super_rally_day:
                logger.warning(
                    f"🛑 [마켓필터] 코스피({self.kospi_index:.1f}pt) < 20일선({self.kospi_20ma:.1f}pt, 등락 {self.kospi_daily_change_pct:+.2f}%) "
                    f"하락장 방어막 가동 (스윙은 +1.2% 이상 폭등 시에만 진입) -> {slot_id} 신규 매수 차단"
                )
                return False
            if self.is_super_rally_day:
                logger.info(
                    f"🚀 [마켓필터] 당일 코스피 대폭등({self.kospi_daily_change_pct:+.2f}%) 감지 -> "
                    f"하락장 방어막 일시 해제, {slot_id} 우량주 스윙 진입 전격 승인"
                )
            return True

        # 기본 폴백
        if self.is_under_20ma:
            logger.warning(
                f"🛑 [마켓필터] 코스피({self.kospi_index:.1f}pt) < 20일선({self.kospi_20ma:.1f}pt) "
                f"하락장 방어막 가동 -> {slot_id} 신규 매수 차단 (현금 보존)"
            )
            return False

        return True
