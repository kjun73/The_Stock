package com.example.thestock.data

import kotlinx.serialization.Serializable

@Serializable
data class PortfolioSummary(
    val initial_capital: Double = 40_000_000.0,
    val total_asset: Double = 40_000_000.0,
    val total_cash: Double = 40_000_000.0,
    val total_stock: Double = 0.0,
    val total_stock_value: Double = 0.0,
    val total_profit: Double = 0.0,
    val total_profit_rate: Double = 0.0,
    val open_positions_count: Int = 0,
    val updated_at: String = ""
)

@Serializable
data class MarketFilterStatus(
    val kospi_index: Double = 2600.0,
    val kospi_20ma: Double = 2580.0,
    val is_bear_market: Boolean = false,
    val is_panic_sell_day: Boolean = false
)

@Serializable
data class SlotPositionDto(
    val slot_id: String,
    val ticker: String,
    val name: String = "",
    val asset_type: String = "STOCK",
    val quantity: Int,
    val entry_price: Double,
    val current_price: Double,
    val stop_loss_price: Double,
    val take_profit_price: Double? = null,
    val profit_rate: Double = 0.0,
    val holding_days: Int = 0,
    val entry_time: String = ""
)

@Serializable
data class SlotAccountDto(
    val slot_id: String,
    val name: String,
    val initial_capital: Double = 10_000_000.0,
    val cash_balance: Double = 10_000_000.0,
    val stock_value: Double = 0.0,
    val total_asset: Double = 10_000_000.0,
    val total_profit: Double = 0.0,
    val profit_rate: Double = 0.0,
    val is_active: Boolean = true,
    val positions: List<SlotPositionDto> = emptyList(),
    val active_positions_count: Int = 0
)

@Serializable
data class TradeRecordDto(
    val id: Long? = null,
    val slot_id: String,
    val ticker: String,
    val name: String = "",
    val asset_type: String,
    val quantity: Int,
    val buy_price: Double,
    val sell_price: Double,
    val gross_pnl: Double = 0.0,
    val total_cost: Double = 0.0,
    val net_pnl: Double,
    val profit_rate: Double,
    val entry_time: String = "",
    val exit_time: String = "",
    val exit_reason: String
)

@Serializable
data class IntelligenceDto(
    val daily_review: String = "",
    val morning_briefing: String = "",
    val screening_candidates: Map<String, List<String>> = emptyMap(),
    val updated_at: String = ""
)

@Serializable
data class SystemStatusDto(
    val status: String = "OFFLINE",
    val server_status: String = "OFFLINE",
    val last_heartbeat: Long = 0L,
    val portfolio: PortfolioSummary = PortfolioSummary(),
    val market_filter: MarketFilterStatus = MarketFilterStatus(),
    val slots: Map<String, SlotAccountDto> = emptyMap(),
    val recent_trades: List<TradeRecordDto> = emptyList(),
    val intelligence: IntelligenceDto = IntelligenceDto()
)

@Serializable
data class ServerControlDto(
    val state: String = "STOPPED",
    val command: String = "NONE",
    val last_updated: Long = 0L,
    val pid: Long = 0L
)
