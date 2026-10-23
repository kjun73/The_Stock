package com.example.thestock.ui.dashboard

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.thestock.data.MarketFilterStatus
import com.example.thestock.data.PortfolioSummary
import com.example.thestock.data.SlotAccountDto
import com.example.thestock.theme.*
import com.example.thestock.ui.components.SlideToKillBar
import java.text.NumberFormat
import java.util.Locale

@Composable
fun DashboardScreen(
    isConnected: Boolean,
    portfolio: PortfolioSummary,
    marketFilter: MarketFilterStatus,
    slots: Map<String, SlotAccountDto>,
    onKillConfirmed: () -> Unit,
    onSlotClick: (String) -> Unit = {},
    serverControlState: String = "STOPPED",
    settlementHistory: List<com.example.thestock.data.DailySettlementDto> = emptyList(),
    modifier: Modifier = Modifier
) {
    val wonFormat = NumberFormat.getNumberInstance(Locale.KOREA)

    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .background(BackgroundDark)
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(top = 16.dp, bottom = 24.dp)
    ) {
        // 1. 상태 헤더 (3단계 상태: ONLINE / CLOSED / OFFLINE)
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        text = "THE STOCK C2",
                        color = TextPrimary,
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Black,
                        letterSpacing = 1.sp
                    )
                    Text(
                        text = "AI & JEV 주식 자동매매 관제",
                        color = TextSecondary,
                        fontSize = 12.sp
                    )
                }

                val (statusText, statusColor, statusBg) = when {
                    isConnected -> Triple("ONLINE", NeonGreen, Color(0x2600E676))
                    serverControlState == "STOPPED" -> Triple("CLOSED · 대기", CyberCyan, Color(0x2600E5FF))
                    else -> Triple("OFFLINE", NeonRed, Color(0x33FF1744))
                }

                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = statusBg,
                    border = BorderStroke(1.dp, statusColor)
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .size(8.dp)
                                .clip(RoundedCornerShape(4.dp))
                                .background(statusColor)
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = statusText,
                            color = statusColor,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
            }
        }

        // 2. 코스피 20일선 마켓필터 배너
        item {
            val changeSign = if (marketFilter.kospi_daily_change_pct >= 0) "+" else ""
            val changeStr = "${changeSign}${marketFilter.kospi_daily_change_pct}%"
            val (bannerColor, bannerBg, bannerText, bannerIcon) = when {
                marketFilter.is_panic_sell_day -> Quadruple(
                    NeonRed, PanicBg,
                    "🛑 당일 급락 패닉셀 발생 (${changeStr}) -> 전 슬롯 신규매수 동결",
                    Icons.Default.Warning
                )
                marketFilter.is_super_rally_day -> Quadruple(
                    NeonGreen, Color(0x2600E676),
                    "🚀 코스피 대폭등 (${changeStr}) -> 하락장 방어막 해제 및 전 슬롯(스윙 포함) 진입 승인",
                    Icons.Default.CheckCircle
                )
                marketFilter.is_strong_rebound_day -> Quadruple(
                    CyberCyan, Color(0x2600E5FF),
                    "⚡ 코스피 강반등 (${changeStr}) -> 슬롯 C(단타) 진입 승인 (스윙은 보수적 유지)",
                    Icons.Default.CheckCircle
                )
                marketFilter.is_bear_market -> Quadruple(
                    ElectricYellow, Color(0x26FFD600),
                    "⚠️ 하락장 방어막 (코스피 ${marketFilter.kospi_index.toInt()}pt [${changeStr}] < 20MA ${marketFilter.kospi_20ma.toInt()}pt) -> 슬롯 A/C/D 차단, 슬롯 B(ETF)만 허용",
                    Icons.Default.Info
                )
                else -> Quadruple(
                    CyberCyan, BearBg,
                    "🛡️ 상승장 (코스피 ${marketFilter.kospi_index.toInt()}pt [${changeStr}] > 20MA ${marketFilter.kospi_20ma.toInt()}pt) -> 4개 슬롯 정상 가동",
                    Icons.Default.CheckCircle
                )
            }

            Surface(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(10.dp),
                color = bannerBg,
                border = BorderStroke(1.dp, bannerColor.copy(alpha = 0.5f))
            ) {
                Row(
                    modifier = Modifier.padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(bannerIcon, contentDescription = null, tint = bannerColor, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = bannerText,
                        color = bannerColor,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium
                    )
                }
            }
        }

        // 3. 총 포트폴리오 요약 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(16.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(18.dp)) {
                    Text(
                        text = "전체 가상 운용자산 (4개 슬롯 합산)",
                        color = TextSecondary,
                        fontSize = 12.sp
                    )
                    Spacer(Modifier.height(4.dp))
                    Row(
                        verticalAlignment = Alignment.Bottom,
                        horizontalArrangement = Arrangement.SpaceBetween,
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text(
                            text = "${wonFormat.format(portfolio.total_asset.toLong())}원",
                            color = TextPrimary,
                            fontSize = 28.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        val isProfit = portfolio.total_profit >= 0
                        Text(
                            text = "${if (isProfit) "+" else ""}${portfolio.total_profit_rate}% (${if (isProfit) "+" else ""}${wonFormat.format(portfolio.total_profit.toLong())}원)",
                            color = if (isProfit) NeonGreen else NeonRed,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }

                    Spacer(Modifier.height(14.dp))
                    HorizontalDivider(color = SurfaceBorder)
                    Spacer(Modifier.height(14.dp))

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Column {
                            Text("주식 평가금", color = TextMuted, fontSize = 11.sp)
                            Text("${wonFormat.format(portfolio.total_stock.toLong())}원", color = CyberCyan, fontSize = 13.sp, fontWeight = FontWeight.Bold, fontFamily = FontFamily.Monospace)
                        }
                        Column {
                            Text("예수금 잔고", color = TextMuted, fontSize = 11.sp)
                            Text("${wonFormat.format(portfolio.total_cash.toLong())}원", color = TextSecondary, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = FontFamily.Monospace)
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text("보유 종목수", color = TextMuted, fontSize = 11.sp)
                            Text("${portfolio.open_positions_count}개 종목", color = if (portfolio.open_positions_count > 0) NeonGreen else TextMuted, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }

        // 3-1. 최근 일일 결산 자산 추이 (Equity Curve) 카드
        if (settlementHistory.isNotEmpty()) {
            item {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                    shape = RoundedCornerShape(12.dp),
                    border = BorderStroke(1.dp, SurfaceBorder)
                ) {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "📈 최근 결산 자산 추이 (Equity Curve)",
                                color = TextPrimary,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Bold
                            )
                            Text(
                                text = "최근 ${settlementHistory.size}일",
                                color = TextMuted,
                                fontSize = 11.sp
                            )
                        }

                        Spacer(Modifier.height(8.dp))

                        settlementHistory.take(5).forEach { settlement ->
                            val isPos = settlement.profit_rate >= 0
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 2.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = settlement.date.takeLast(5),
                                    color = TextSecondary,
                                    fontSize = 11.sp,
                                    fontFamily = FontFamily.Monospace
                                )
                                Text(
                                    text = "${wonFormat.format(settlement.total_asset.toLong())}원",
                                    color = TextPrimary,
                                    fontSize = 12.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    fontFamily = FontFamily.Monospace
                                )
                                Text(
                                    text = "${if (isPos) "+" else ""}${settlement.profit_rate}%",
                                    color = if (isPos) NeonGreen else NeonRed,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                        }
                    }
                }
            }
        }

        // 4. 슬롯 요약 섹션 헤더
        item {
            Text(
                text = "4대 전략 슬롯 (각 1,000만 원)",
                color = TextPrimary,
                fontSize = 15.sp,
                fontWeight = FontWeight.Bold
            )
        }

        // 5. 슬롯별 요약 카드 목록
        val slotKeys = listOf("SLOT_A", "SLOT_B", "SLOT_C", "SLOT_D")
        items(slotKeys.size) { index ->
            val slotId = slotKeys[index]
            val slot = slots[slotId] ?: SlotAccountDto(slot_id = slotId, name = "슬롯 $slotId")
            val slotColor = when (slotId) {
                "SLOT_A" -> SlotAColor
                "SLOT_B" -> SlotBColor
                "SLOT_C" -> SlotCColor
                else -> SlotDColor
            }

            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onSlotClick(slotId) },
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(14.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(slotColor.copy(alpha = 0.15f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = slotId.takeLast(1),
                                color = slotColor,
                                fontWeight = FontWeight.Black,
                                fontSize = 16.sp
                            )
                        }
                        Spacer(Modifier.width(12.dp))
                        Column {
                            Text(slot.name, color = TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Text(
                                text = "보유 ${slot.positions.size}종목 · 주식 ${wonFormat.format(slot.stock_value.toLong())}원 · 예수금 ${wonFormat.format(slot.cash_balance.toLong())}원",
                                color = TextSecondary,
                                fontSize = 11.sp
                            )
                        }
                    }

                    Column(horizontalAlignment = Alignment.End) {
                        Text(
                            text = "${wonFormat.format(slot.total_asset.toLong())}원",
                            color = TextPrimary,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace
                        )
                        val isProfit = slot.profit_rate >= 0
                        Text(
                            text = "${if (isProfit) "+" else ""}${slot.profit_rate}%",
                            color = if (isProfit) NeonGreen else NeonRed,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Bold
                        )
                    }
                }
            }
        }

        // 6. 마스터 킬스위치 제스처 슬라이더
        item {
            Spacer(Modifier.height(8.dp))
            SlideToKillBar(onKillConfirmed = onKillConfirmed)
        }
    }
}

// 4개 요소를 담는 헬퍼 데이터 클래스
private data class Quadruple<A, B, C, D>(val first: A, val second: B, val third: C, val fourth: D)
