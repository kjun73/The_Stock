import logging

logger = logging.getLogger(__name__)


class MarketFilterManager:
    """
    코스피 20일 이동평균선 기반 대폭락장 방어 필터 (전략별 이원화):
    - 추세 추종 슬롯(A, C, D): 지수가 20일선 아래면 신규 매수 전면 차단
    - 역추세 평균회귀 슬롯(B): 20일선 아래여도 매수 허용 (단, 당일 -3.0% 이상 패닉셀 시에만 차단)
    """
    def __init__(self):
        self.kospi_index: float = 2600.0
        self.kospi_20ma: float = 2580.0
        self.kospi_daily_change_pct: float = 0.5  # 당일 등락률 (%)

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
