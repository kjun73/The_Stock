from core.models import AssetType, OrderType, TickData


class SqlPaperExecutor:
    """
    KIS 실전 호가를 기반으로 슬리피지와 2026년 차등 세율을 적용하는 로컬 체결 엔진
    """
    SLIPPAGE_PENALTY_RATE = 0.001  # 시장가 돌파 및 비상 청산 시 0.10% 페널티
    FEE_RATE = 0.00015             # 매수 및 매도 시 유관기관/위탁 수수료 0.015%
    STOCK_TAX_RATE = 0.0020        # 2026년 일반 주식 증권거래세 0.20% (ETF는 0% 면제)

    @classmethod
    def compute_execution_price(cls, action: str, order_type: OrderType, tick: TickData) -> float:
        """
        체결 호가 산정:
        - BUY: 매도1호가(ask_price) 기준. 돌파/비상청산 시 +0.10% 슬리피지 가산
        - SELL: 매수1호가(bid_price) 기준. 돌파/비상청산 시 -0.10% 슬리피지 차감
        """
        if action == "BUY":
            base_price = tick.ask_price if tick.ask_price > 0 else tick.price
            if order_type in ("MARKET_BREAKOUT", "EMERGENCY_FLUSH", "KILL_SWITCH"):
                return round(base_price * (1.0 + cls.SLIPPAGE_PENALTY_RATE), 2)
            return base_price
        else:  # SELL
            base_price = tick.bid_price if tick.bid_price > 0 else tick.price
            if order_type in ("MARKET_BREAKOUT", "EMERGENCY_FLUSH", "KILL_SWITCH"):
                return round(base_price * (1.0 - cls.SLIPPAGE_PENALTY_RATE), 2)
            return base_price

    @classmethod
    def calculate_costs_and_net_pnl(
        cls,
        buy_price: float,
        sell_price: float,
        quantity: int,
        asset_type: AssetType
    ) -> tuple[float, float, float, float]:
        """
        자산군별 차등 세율 및 수수료 정밀 차감 후 순손익 산출:
        - 반환: (gross_pnl, total_cost, net_pnl, profit_rate)
        """
        buy_amount = buy_price * quantity
        sell_amount = sell_price * quantity
        gross_pnl = sell_amount - buy_amount

        buy_fee = buy_amount * cls.FEE_RATE
        sell_fee = sell_amount * cls.FEE_RATE
        # ETF는 증권거래세 면제, 주식은 0.20% 부과
        sell_tax = 0.0 if asset_type == "ETF" else (sell_amount * cls.STOCK_TAX_RATE)

        total_cost = buy_fee + sell_fee + sell_tax
        net_pnl = gross_pnl - total_cost
        profit_rate = (net_pnl / buy_amount) * 100.0 if buy_amount > 0 else 0.0

        return (
            round(gross_pnl, 2),
            round(total_cost, 2),
            round(net_pnl, 2),
            round(profit_rate, 2)
        )
