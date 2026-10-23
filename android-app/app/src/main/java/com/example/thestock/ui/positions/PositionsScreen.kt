package com.example.thestock.ui.positions

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.thestock.data.SlotAccountDto
import com.example.thestock.data.SlotPositionDto
import com.example.thestock.data.TradeRecordDto
import com.example.thestock.theme.*
import com.example.thestock.util.StockUtils
import java.text.NumberFormat
import java.util.Locale

@Composable
fun PositionsScreen(
    slots: Map<String, SlotAccountDto>,
    recentTrades: List<TradeRecordDto> = emptyList(),
    initialSlotId: String = "SLOT_A",
    onToggleSlot: (String, Boolean) -> Unit,
    onClosePosition: (String, String) -> Unit,
    modifier: Modifier = Modifier
) {
    val wonFormat = NumberFormat.getNumberInstance(Locale.KOREA)
    val slotKeys = listOf("SLOT_A", "SLOT_B", "SLOT_C", "SLOT_D")
    val initialIdx = slotKeys.indexOf(initialSlotId).let { if (it >= 0) it else 0 }
    var selectedTab by remember(initialSlotId) { mutableIntStateOf(initialIdx) }

    LaunchedEffect(initialSlotId) {
        val idx = slotKeys.indexOf(initialSlotId)
        if (idx >= 0) {
            selectedTab = idx
        }
    }

    val currentSlotId = slotKeys[selectedTab]
    val currentSlot = slots[currentSlotId] ?: SlotAccountDto(slot_id = currentSlotId, name = "슬롯 $currentSlotId")

    // 0: 보유 포지션, 1: 청산(매도) 내역
    var viewSubTab by remember { mutableIntStateOf(0) }
    val slotTrades = recentTrades.filter { it.slot_id == currentSlotId }

    var positionToClose by remember { mutableStateOf<SlotPositionDto?>(null) }

    // 청산 확인 다이얼로그
    positionToClose?.let { pos ->
        AlertDialog(
            onDismissRequest = { positionToClose = null },
            containerColor = SurfaceDark,
            title = { Text("포지션 수동 청산", color = TextPrimary, fontWeight = FontWeight.Bold, fontSize = 16.sp) },
            text = {
                Text(
                    "${StockUtils.formatDisplay(pos.ticker, pos.name)} (${pos.quantity}주) 포지션을 현재 시장 호가로 즉시 청산하시겠습니까?",
                    color = TextSecondary,
                    fontSize = 13.sp
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        onClosePosition(pos.slot_id, pos.ticker)
                        positionToClose = null
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = NeonRed),
                    shape = RoundedCornerShape(6.dp)
                ) {
                    Text("청산 집행", color = Color.White, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                OutlinedButton(
                    onClick = { positionToClose = null },
                    shape = RoundedCornerShape(6.dp)
                ) {
                    Text("취소", color = TextPrimary, fontSize = 13.sp)
                }
            }
        )
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(BackgroundDark)
            .padding(horizontal = 12.dp, vertical = 8.dp)
    ) {
        // 1. 슬롯 선택 탭 (상단 슬롯 A/B/C/D)
        ScrollableTabRow(
            selectedTabIndex = selectedTab,
            containerColor = BackgroundDark,
            contentColor = CyberCyan,
            edgePadding = 0.dp,
            divider = { HorizontalDivider(color = SurfaceBorder, thickness = 0.5.dp) }
        ) {
            slotKeys.forEachIndexed { index, slotId ->
                Tab(
                    selected = selectedTab == index,
                    onClick = { selectedTab = index },
                    modifier = Modifier.height(38.dp),
                    text = {
                        Text(
                            text = slotId.replace("_", " "),
                            fontSize = 13.sp,
                            fontWeight = if (selectedTab == index) FontWeight.Bold else FontWeight.Normal,
                            color = if (selectedTab == index) CyberCyan else TextSecondary
                        )
                    }
                )
            }
        }

        Spacer(Modifier.height(6.dp))

        // 2. 슬롯 자산 요약 & On/Off 스위치 카드 (주식 평가액 + 예수금 명확한 분리)
        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = SurfaceDark),
            shape = RoundedCornerShape(8.dp),
            border = BorderStroke(1.dp, SurfaceBorder)
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 10.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = currentSlot.name,
                            color = TextPrimary,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = if (currentSlot.is_active) "ACTIVE" else "FROZEN",
                            color = if (currentSlot.is_active) NeonGreen else NeonRed,
                            fontSize = 11.sp,
                            fontWeight = FontWeight.ExtraBold,
                            fontFamily = FontFamily.Monospace
                        )
                    }
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = "총자산: ${wonFormat.format(currentSlot.total_asset.toLong())}원 (주식: ${wonFormat.format(currentSlot.stock_value.toLong())}원 · 예수금: ${wonFormat.format(currentSlot.cash_balance.toLong())}원)",
                        color = TextSecondary,
                        fontSize = 11.sp,
                        fontFamily = FontFamily.Monospace
                    )
                }

                Switch(
                    checked = currentSlot.is_active,
                    onCheckedChange = { onToggleSlot(currentSlotId, it) },
                    modifier = Modifier.height(28.dp),
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = NeonGreen,
                        checkedTrackColor = Color(0x3300E676),
                        uncheckedThumbColor = NeonRed,
                        uncheckedTrackColor = Color(0x33FF1744)
                    )
                )
            }
        }

        Spacer(Modifier.height(6.dp))

        // 3. 서브 탭: [보유 포지션 (N)] vs [체결·청산 내역 (M)]
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0x15FFFFFF), RoundedCornerShape(6.dp))
                .padding(2.dp)
        ) {
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(4.dp))
                    .background(if (viewSubTab == 0) CyberCyan.copy(alpha = 0.2f) else Color.Transparent)
                    .clickable { viewSubTab = 0 }
                    .padding(vertical = 6.dp),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = "보유 포지션 (${currentSlot.positions.size})",
                    fontSize = 12.sp,
                    fontWeight = if (viewSubTab == 0) FontWeight.Bold else FontWeight.Normal,
                    color = if (viewSubTab == 0) CyberCyan else TextSecondary
                )
            }
            Box(
                modifier = Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(4.dp))
                    .background(if (viewSubTab == 1) CyberCyan.copy(alpha = 0.2f) else Color.Transparent)
                    .clickable { viewSubTab = 1 }
                    .padding(vertical = 6.dp),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    text = "청산 내역 (${slotTrades.size})",
                    fontSize = 12.sp,
                    fontWeight = if (viewSubTab == 1) FontWeight.Bold else FontWeight.Normal,
                    color = if (viewSubTab == 1) CyberCyan else TextSecondary
                )
            }
        }

        Spacer(Modifier.height(6.dp))

        // 4. 리스트 표시 (보유 포지션 or 청산 내역)
        if (viewSubTab == 0) {
            // === 보유 포지션 목록 ===
            if (currentSlot.positions.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("보유 중인 포지션이 없습니다", color = TextSecondary, fontSize = 13.sp)
                        Spacer(Modifier.height(4.dp))
                        Text("실시간 매매 시그널을 감시 중입니다", color = TextMuted, fontSize = 11.sp)
                    }
                }
            } else {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    items(currentSlot.positions) { pos ->
                        val isProfit = pos.profit_rate >= 0
                        val evalAmount = (pos.current_price * pos.quantity).toLong()
                        val buyCost = (pos.entry_price * pos.quantity).toLong()
                        val evalPnl = ((pos.current_price - pos.entry_price) * pos.quantity).toLong()

                        Card(
                            modifier = Modifier.fillMaxWidth(),
                            colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                            shape = RoundedCornerShape(8.dp),
                            border = BorderStroke(1.dp, if (isProfit) Color(0x3300E676) else Color(0x33FF1744))
                        ) {
                            Column(modifier = Modifier.padding(8.dp)) {
                                // 1단: 종목명 + 종목코드 + 자산배지 + 손익률/손익금 + 청산버튼
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Row(
                                        verticalAlignment = Alignment.CenterVertically,
                                        modifier = Modifier.weight(1f)
                                    ) {
                                        Text(
                                            text = StockUtils.getStockName(pos.ticker, pos.name),
                                            color = TextPrimary,
                                            fontSize = 14.sp,
                                            fontWeight = FontWeight.Bold
                                        )
                                        Spacer(Modifier.width(4.dp))
                                        Text(
                                            text = pos.ticker,
                                            color = TextSecondary,
                                            fontSize = 11.sp,
                                            fontFamily = FontFamily.Monospace
                                        )
                                        Spacer(Modifier.width(6.dp))
                                        Surface(
                                            shape = RoundedCornerShape(3.dp),
                                            color = if (pos.asset_type == "ETF") Color(0x2600E676) else Color(0x2600E5FF)
                                        ) {
                                            Text(
                                                text = if (pos.asset_type == "ETF") "ETF 0%" else "주식 0.215%",
                                                color = if (pos.asset_type == "ETF") NeonGreen else CyberCyan,
                                                fontSize = 9.sp,
                                                fontWeight = FontWeight.Bold,
                                                modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                            )
                                        }

                                        val isBreakevenLocked = pos.stop_loss_price >= pos.entry_price
                                        val isTrailingActive = (pos.slot_id == "SLOT_C" && pos.profit_rate >= 1.5) || (pos.slot_id == "SLOT_D" && pos.profit_rate >= 3.5)
                                        if (isBreakevenLocked) {
                                            Spacer(Modifier.width(4.dp))
                                            Surface(
                                                shape = RoundedCornerShape(3.dp),
                                                color = Color(0x2600E676)
                                            ) {
                                                Text(
                                                    text = "🛡️ 본전잠금",
                                                    color = NeonGreen,
                                                    fontSize = 9.sp,
                                                    fontWeight = FontWeight.Bold,
                                                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                                )
                                            }
                                        } else if (isTrailingActive) {
                                            Spacer(Modifier.width(4.dp))
                                            Surface(
                                                shape = RoundedCornerShape(3.dp),
                                                color = Color(0x26FFD600)
                                            ) {
                                                Text(
                                                    text = "🔥 트레일링",
                                                    color = ElectricYellow,
                                                    fontSize = 9.sp,
                                                    fontWeight = FontWeight.Bold,
                                                    modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                                )
                                            }
                                        }
                                    }

                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Column(horizontalAlignment = Alignment.End) {
                                            Text(
                                                text = "${if (isProfit) "+" else ""}${pos.profit_rate}%",
                                                color = if (isProfit) NeonGreen else NeonRed,
                                                fontSize = 14.sp,
                                                fontWeight = FontWeight.ExtraBold,
                                                fontFamily = FontFamily.Monospace
                                            )
                                            Text(
                                                text = "${if (evalPnl >= 0) "+" else ""}${wonFormat.format(evalPnl)}원",
                                                color = if (isProfit) NeonGreen else NeonRed,
                                                fontSize = 10.sp,
                                                fontFamily = FontFamily.Monospace
                                            )
                                        }
                                        Spacer(Modifier.width(4.dp))
                                        IconButton(
                                            onClick = { positionToClose = pos },
                                            modifier = Modifier.size(24.dp)
                                        ) {
                                            Icon(
                                                Icons.Default.Close,
                                                contentDescription = "청산",
                                                tint = TextMuted,
                                                modifier = Modifier.size(16.dp)
                                            )
                                        }
                                    }
                                }

                                Spacer(Modifier.height(4.dp))

                                // 2단: 4열 컴팩트 데이터 (현재가 | 진입평단 | 수량(평가금) | 보유일수)
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .background(Color(0x18FFFFFF), RoundedCornerShape(4.dp))
                                        .padding(horizontal = 6.dp, vertical = 4.dp),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Column {
                                        Text("현재가", color = TextMuted, fontSize = 9.sp)
                                        Text(
                                            text = "${wonFormat.format(pos.current_price.toLong())}원",
                                            color = TextPrimary,
                                            fontSize = 12.sp,
                                            fontWeight = FontWeight.Bold,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }
                                    Column {
                                        Text("진입평단", color = TextMuted, fontSize = 9.sp)
                                        Text(
                                            text = "${wonFormat.format(pos.entry_price.toLong())}원",
                                            color = TextSecondary,
                                            fontSize = 11.sp,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }
                                    Column {
                                        Text("수량 (평가금)", color = TextMuted, fontSize = 9.sp)
                                        Text(
                                            text = "${pos.quantity}주 (${wonFormat.format(evalAmount)}원)",
                                            color = TextSecondary,
                                            fontSize = 11.sp,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }
                                    Column(horizontalAlignment = Alignment.End) {
                                        Text("보유", color = TextMuted, fontSize = 9.sp)
                                        Text(
                                            text = "${pos.holding_days}일차",
                                            color = CyberCyan,
                                            fontSize = 11.sp,
                                            fontWeight = FontWeight.Medium
                                        )
                                    }
                                }

                                Spacer(Modifier.height(4.dp))

                                // 3단 (신규): 매수일자/체결시각 및 매수총액 명확 표기
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        text = "🕒 매수체결: ${StockUtils.formatDateTimeShort(pos.entry_time)}",
                                        color = CyberCyan,
                                        fontSize = 10.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                    Text(
                                        text = "매수원금: ${wonFormat.format(buyCost)}원",
                                        color = TextSecondary,
                                        fontSize = 10.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }

                                Spacer(Modifier.height(3.dp))

                                // 4단: 손절선 및 익절목표
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        text = "손절: ${wonFormat.format(pos.stop_loss_price.toLong())}원",
                                        color = NeonRed,
                                        fontSize = 10.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                    pos.take_profit_price?.let { tp ->
                                        Text(
                                            text = "익절: ${wonFormat.format(tp.toLong())}원",
                                            color = NeonGreen,
                                            fontSize = 10.sp,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } else {
            // === 청산(매도) 완료 내역 목록 ===
            if (slotTrades.isEmpty()) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    contentAlignment = Alignment.Center
                ) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("청산 완료된 거래 내역이 없습니다", color = TextSecondary, fontSize = 13.sp)
                        Spacer(Modifier.height(4.dp))
                        Text("포지션이 청산되면 체결 내역이 이곳에 기록됩니다", color = TextMuted, fontSize = 11.sp)
                    }
                }
            } else {
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(6.dp)
                ) {
                    item {
                        val totalNetPnl = slotTrades.sumOf { it.net_pnl }
                        val winTrades = slotTrades.filter { it.net_pnl > 0 }
                        val lossTrades = slotTrades.filter { it.net_pnl < 0 }
                        val winRate = if (slotTrades.isNotEmpty()) (winTrades.size.toDouble() / slotTrades.size * 100.0) else 0.0
                        val avgGain = if (winTrades.isNotEmpty()) (winTrades.sumOf { it.net_pnl } / winTrades.size) else 0.0
                        val avgLoss = if (lossTrades.isNotEmpty()) (lossTrades.sumOf { Math.abs(it.net_pnl) } / lossTrades.size) else 0.0
                        val payoff = if (avgLoss > 0) String.format(Locale.US, "%.1f", avgGain / avgLoss) else if (avgGain > 0) "∞" else "0.0"
                        val isNetProfitable = totalNetPnl >= 0

                        Card(
                            modifier = Modifier.fillMaxWidth(),
                            colors = CardDefaults.cardColors(containerColor = SurfaceVariantDark),
                            shape = RoundedCornerShape(6.dp),
                            border = BorderStroke(1.dp, SurfaceBorder)
                        ) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(horizontal = 10.dp, vertical = 6.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = "실현손익: ${if (isNetProfitable) "+" else ""}${wonFormat.format(totalNetPnl.toLong())}원",
                                    color = if (isNetProfitable) NeonGreen else NeonRed,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace
                                )
                                Text(
                                    text = "승률 ${String.format(Locale.US, "%.0f", winRate)}% (${winTrades.size}승 ${lossTrades.size}패) · 손익비 ${payoff}",
                                    color = TextSecondary,
                                    fontSize = 11.sp,
                                    fontFamily = FontFamily.Monospace
                                )
                            }
                        }
                    }

                    items(slotTrades) { trade ->
                        val isProfit = trade.net_pnl >= 0
                        Card(
                            modifier = Modifier.fillMaxWidth(),
                            colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                            shape = RoundedCornerShape(8.dp),
                            border = BorderStroke(1.dp, if (isProfit) Color(0x3300E676) else Color(0x33FF1744))
                        ) {
                            Column(modifier = Modifier.padding(8.dp)) {
                                // 1단: 종목명 + 실현손익금 / 수익률
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Text(
                                            text = StockUtils.getStockName(trade.ticker, trade.name),
                                            color = TextPrimary,
                                            fontSize = 14.sp,
                                            fontWeight = FontWeight.Bold
                                        )
                                        Spacer(Modifier.width(4.dp))
                                        Text(
                                            text = trade.ticker,
                                            color = TextSecondary,
                                            fontSize = 11.sp,
                                            fontFamily = FontFamily.Monospace
                                        )
                                    }
                                    Text(
                                        text = "${if (isProfit) "+" else ""}${wonFormat.format(trade.net_pnl.toLong())}원 (${if (isProfit) "+" else ""}${trade.profit_rate}%)",
                                        color = if (isProfit) NeonGreen else NeonRed,
                                        fontSize = 13.sp,
                                        fontWeight = FontWeight.ExtraBold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }

                                Spacer(Modifier.height(4.dp))

                                // 2단: 가격 및 수량 요약
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .background(Color(0x18FFFFFF), RoundedCornerShape(4.dp))
                                        .padding(horizontal = 6.dp, vertical = 4.dp),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        text = "매수: ${wonFormat.format(trade.buy_price.toLong())}원 ➔ 매도: ${wonFormat.format(trade.sell_price.toLong())}원",
                                        color = TextSecondary,
                                        fontSize = 11.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                    Text(
                                        text = "${trade.quantity}주",
                                        color = CyberCyan,
                                        fontSize = 11.sp,
                                        fontWeight = FontWeight.Bold,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }

                                Spacer(Modifier.height(4.dp))

                                // 3단: 매수체결시각 & 매도체결시각 (핵심 요청)
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text(
                                        text = "📥 매수: ${StockUtils.formatDateTimeShort(trade.entry_time)}",
                                        color = TextMuted,
                                        fontSize = 10.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                    Text(
                                        text = "📤 매도: ${StockUtils.formatDateTimeShort(trade.exit_time)}",
                                        color = if (isProfit) NeonGreen else NeonRed,
                                        fontSize = 10.sp,
                                        fontFamily = FontFamily.Monospace
                                    )
                                }

                                Spacer(Modifier.height(3.dp))

                                // 4단: 청산 사유 배지
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.End
                                ) {
                                    Surface(
                                        shape = RoundedCornerShape(3.dp),
                                        color = Color(0x22FFFFFF)
                                    ) {
                                        Text(
                                            text = trade.exit_reason,
                                            color = TextSecondary,
                                            fontSize = 9.sp,
                                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp)
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
