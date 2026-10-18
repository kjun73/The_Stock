import logging

logger = logging.getLogger(__name__)


class MarketFilterManager:
    """
    코스피 20일 이동평균선 기반 대폭락장 방어 필터 (전략별 이원화):
    - 추세 추종 슬롯(A, C, D): 지수가 20일선 아래면 신규 매수 전면 차단
    - 역추세 평균회귀 슬롯(B): 20일선 아래여도 매수 허용 (단, 당일 -3.0% 이상 패닉셀 시에만 차단)
    """
    def __init__(self, base_index: float = 2600.0, ma20: float = 2580.0):
        self.base_index: float = base_index
        self.kospi_index: float = base_index
        self.kospi_20ma: float = ma20
        self.kospi_daily_change_pct: float = 0.5  # 당일 등락률 (%)
        self.kodex200_open: float = 0.0

    def refresh_from_history(self):
        """pykrx를 활용해 KODEX 200(069500) 최근 20영업일 종가 기준 20MA 및 기준 지수 동적 산출"""
        try:
            from pykrx import stock
            from datetime import datetime, timedelta
            today = datetime.now()
            start_date = (today - timedelta(days=40)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")
            df = stock.get_market_ohlcv_by_date(start_date, end_date, "069500")
            if not df.empty and len(df) >= 5:
                closes = df["종가"] if "종가" in df.columns else df.iloc[:, 3]
                ma20_val = float(closes.rolling(window=min(len(closes), 20)).mean().iloc[-1])
                latest_close = float(closes.iloc[-1])
                ratio = latest_close / ma20_val if ma20_val > 0 else 1.0
                self.kospi_20ma = 2600.0
                self.base_index = round(2600.0 * ratio, 1)
                self.kospi_index = self.base_index
                logger.info(
                    f"📊 [마켓필터] KODEX 200 실증 데이터 동적 동기화 완료: "
                    f"최신가 {latest_close:.0f}, 20MA {ma20_val:.0f} -> 기준지수 {self.base_index:.1f} / 20MA {self.kospi_20ma:.1f}"
                )
        except Exception as e:
            logger.info(f"[마켓필터] pykrx 동적 20MA 동기화 폴백 (기본값 유지): {e}")

    def update_from_kodex200(self, tick_price: float):
        """KODEX 200(069500) 실시간 틱 기반 지수 및 등락률 동적 갱신"""
        if self.kodex200_open <= 0:
            self.kodex200_open = tick_price
        open_p = self.kodex200_open
        daily_change = ((tick_price - open_p) / open_p) * 100.0 if open_p > 0 else 0.0
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
    def is_bear_market(self) -> bool:
        """코스피 지수가 20일 이평선 아래인지 여부"""
        return self.kospi_index < self.kospi_20ma

    @property
    def is_panic_sell_day(self) -> bool:
        """당일 -3.0% 이하 급락 패닉셀 상태인지 여부"""
        return self.kospi_daily_change_pct <= -3.0

    def can_enter(self, slot_id: str) -> bool:
        """슬롯별 신규 진입 가능 여부 판정"""
        # 슬롯 B (ETF 역추세 볼린저 그리드): 패닉셀 날만 아니면 하락장에서도 매수 허용
        if slot_id == "SLOT_B":
            if self.is_panic_sell_day:
                logger.warning("🛑 [마켓필터] 코스피 당일 -3% 이상 패닉셀 -> 슬롯 B 신규 매수 보류")
                return False
            return True

        # 슬롯 A, C, D (추세 추종 전략): 20일선 아래 하락장에서는 전면 차단
        if self.is_bear_market:
            logger.warning(
                f"🛑 [마켓필터] 코스피({self.kospi_index:.1f}) < 20일선({self.kospi_20ma:.1f}) "
                f"하락장 방어막 가동 -> {slot_id} 신규 매수 차단 (현금 보존)"
            )
            return False

        return True
