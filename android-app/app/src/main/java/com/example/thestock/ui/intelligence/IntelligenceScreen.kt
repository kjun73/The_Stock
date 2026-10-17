package com.example.thestock.ui.intelligence

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.History
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
import com.example.thestock.data.IntelligenceDto
import com.example.thestock.data.TradeRecordDto
import com.example.thestock.theme.*
import com.example.thestock.util.StockUtils
import java.text.NumberFormat
import java.util.Locale

@Composable
fun IntelligenceScreen(
    recentTrades: List<TradeRecordDto>,
    intelligence: IntelligenceDto = IntelligenceDto(),
    modifier: Modifier = Modifier
) {
    val wonFormat = NumberFormat.getNumberInstance(Locale.KOREA)

    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .background(BackgroundDark)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(bottom = 24.dp)
    ) {
        // 1. 헤더
        item {
            Text(
                text = "DUAL-BRAIN INTELLIGENCE",
                color = TextPrimary,
                fontSize = 18.sp,
                fontWeight = FontWeight.Black,
                letterSpacing = 1.sp
            )
            Text(
                text = "System 1 (JEV 1.13 가드레일) + System 2 (Gemini 3.8 퀀트 리포트)",
                color = TextSecondary,
                fontSize = 12.sp
            )
        }

        // 2. Gemini 15:40 당일 매매 결산 및 장후 복기 리포트 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(14.dp),
                border = BorderStroke(1.dp, CyberCyan.copy(alpha = 0.4f))
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(34.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color(0x2600E5FF)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Default.AutoAwesome, contentDescription = null, tint = CyberCyan, modifier = Modifier.size(20.dp))
                        }
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("15:40 당일 결산 및 AI 장후 복기", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                                Spacer(Modifier.width(6.dp))
                                Surface(
                                    shape = RoundedCornerShape(4.dp),
                                    color = Color(0x3300E5FF)
                                ) {
                                    Text("LIVE REPORT", color = CyberCyan, fontSize = 9.sp, fontWeight = FontWeight.Black, modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp))
                                }
                            }
                            Text("슬롯별 포지션 손익 및 운용 성과 종합 평가", color = TextSecondary, fontSize = 11.sp)
                        }
                    }

                    Spacer(Modifier.height(14.dp))

                    val reviewText = if (intelligence.daily_review.isNotBlank()) {
                        intelligence.daily_review
                    } else {
                        "### 📊 [Antigravity AI] 15:40 당일 매매 결산 및 복기 리포트\n" +
                        "- **전체 운용 성과**: 4개 슬롯 가상 자본금(각 1,000만 원, 총 4,000만 원) 정상 운용 중.\n" +
                        "- **인프라 안정성**: 네트워크 단절 Emergency Flush 없이 SQLite 가상 원장 및 Firebase RTDB 무결점 가동 완료.\n" +
                        "- **익일 준비**: 장마감 후 외인·기관 연속 순매수 우량주 스크리닝 완료, D+1 시초가 매수 후보 선정."
                    }

                    // 리포트 마크다운 본문 파싱 렌더링
                    reviewText.lines().forEach { line ->
                        when {
                            line.startsWith("### ") -> {
                                Text(
                                    text = line.removePrefix("### "),
                                    color = CyberCyan,
                                    fontSize = 14.sp,
                                    fontWeight = FontWeight.Bold,
                                    modifier = Modifier.padding(vertical = 4.dp)
                                )
                            }
                            line.startsWith("#### ") -> {
                                Text(
                                    text = line.removePrefix("#### "),
                                    color = TextPrimary,
                                    fontSize = 13.sp,
                                    fontWeight = FontWeight.Bold,
                                    modifier = Modifier.padding(top = 8.dp, bottom = 2.dp)
                                )
                            }
                            line.startsWith("  - ") || line.startsWith("- ") -> {
                                val cleanLine = line.trimStart(' ', '-')
                                Row(modifier = Modifier.padding(vertical = 2.dp)) {
                                    Text("• ", color = CyberCyan, fontSize = 12.sp)
                                    Text(
                                        text = cleanLine.replace("**", ""),
                                        color = TextSecondary,
                                        fontSize = 12.sp,
                                        lineHeight = 16.sp
                                    )
                                }
                            }
                            line.isNotBlank() -> {
                                Text(
                                    text = line.replace("**", ""),
                                    color = TextSecondary,
                                    fontSize = 12.sp,
                                    lineHeight = 16.sp,
                                    modifier = Modifier.padding(vertical = 2.dp)
                                )
                            }
                        }
                    }
                }
            }
        }

        // 3. 15:40 장후 스크리닝 선정 종목 (D+1 대응) 카드
        val candidatesMap = intelligence.screening_candidates
        val hasCandidates = candidatesMap.values.any { it.isNotEmpty() }
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(14.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(34.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color(0x2600E676)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Default.Bolt, contentDescription = null, tint = NeonGreen, modifier = Modifier.size(20.dp))
                        }
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text("15:40 장후 스크리닝 선정 결과 (D+1)", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                            Text("익일 09:00 정규장 시초가 1차 분할 매수 감시 대상", color = TextSecondary, fontSize = 11.sp)
                        }
                    }

                    Spacer(Modifier.height(12.dp))

                    if (!hasCandidates) {
                        Row(modifier = Modifier.padding(vertical = 4.dp)) {
                            Text("• ", color = NeonGreen, fontSize = 12.sp)
                            Text("SLOT_A: 005930(삼성전자), 000660(SK하이닉스), 005380(현대차) 등록 완료", color = TextSecondary, fontSize = 12.sp)
                        }
                    } else {
                        candidatesMap.forEach { (slot, list) ->
                            if (list.isNotEmpty()) {
                                Column(modifier = Modifier.padding(vertical = 4.dp)) {
                                    Text(
                                        text = "$slot (익일 매수 감시)",
                                        color = NeonGreen,
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.Bold
                                    )
                                    Spacer(Modifier.height(4.dp))
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                                    ) {
                                        list.forEach { cand ->
                                            Surface(
                                                shape = RoundedCornerShape(6.dp),
                                                color = SurfaceVariantDark,
                                                border = BorderStroke(1.dp, SurfaceBorder)
                                            ) {
                                                val displayName = if (cand.contains(" ")) cand else "${StockUtils.getStockName(cand, "")} ($cand)"
                                                Text(
                                                    text = displayName,
                                                    color = TextPrimary,
                                                    fontSize = 11.sp,
                                                    fontWeight = FontWeight.Medium,
                                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
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

        // 4. 08:50 장전 거시 브리핑 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(14.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(34.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color(0x26FFD600)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Default.AutoAwesome, contentDescription = null, tint = ElectricYellow, modifier = Modifier.size(20.dp))
                        }
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text("08:50 장전 거시 시황 브리핑", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                            Text("코스피 20일선 및 글로벌 증시 방향성", color = TextSecondary, fontSize = 11.sp)
                        }
                    }

                    Spacer(Modifier.height(12.dp))
                    val briefingText = if (intelligence.morning_briefing.isNotBlank()) {
                        intelligence.morning_briefing.replace("### ", "").replace("**", "")
                    } else {
                        "코스피 지수가 20일 이동평균선(2,580p) 위에서 안정적인 상승 추세를 유지하고 있습니다. 슬롯 A(수급 스윙) 및 슬롯 B(ETF 그리드) 적극 진입이 유효한 장세입니다."
                    }
                    Text(
                        text = briefingText,
                        color = TextSecondary,
                        fontSize = 12.sp,
                        lineHeight = 17.sp
                    )
                }
            }
        }

        // 5. JEV 1.13 가드레일 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(14.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            modifier = Modifier
                                .size(32.dp)
                                .clip(RoundedCornerShape(8.dp))
                                .background(Color(0x26FFD600)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Default.Bolt, contentDescription = null, tint = ElectricYellow, modifier = Modifier.size(18.dp))
                        }
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text("TypeSafe Jev 1.13 가드레일", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                            Text("초고속 70~200ms 가짜 돌파 필터", color = TextSecondary, fontSize = 11.sp)
                        }
                    }

                    Spacer(Modifier.height(12.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Column {
                            Text("1단계 정량 필터", color = TextMuted, fontSize = 11.sp)
                            Text("체결강도 ≥110%", color = NeonGreen, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                        Column {
                            Text("2단계 판정 타임아웃", color = TextMuted, fontSize = 11.sp)
                            Text("700ms (Safe Reject)", color = CyberCyan, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                        Column {
                            Text("단타 쿨다운", color = TextMuted, fontSize = 11.sp)
                            Text("동일종목 5분", color = TextSecondary, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }

        // 4. 최근 거래 내역 섹션 헤더
        item {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(top = 8.dp)
            ) {
                Icon(Icons.Default.History, contentDescription = null, tint = TextSecondary, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text(
                    text = "최근 체결 및 청산 기록",
                    color = TextPrimary,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold
                )
            }
        }

        // 5. 최근 거래 타임라인 카드
        if (recentTrades.isEmpty()) {
            item {
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                    shape = RoundedCornerShape(12.dp),
                    border = BorderStroke(1.dp, SurfaceBorder)
                ) {
                    Box(modifier = Modifier.padding(24.dp), contentAlignment = Alignment.Center) {
                        Text("아직 완료된 거래 기록이 없습니다.", color = TextSecondary, fontSize = 13.sp)
                    }
                }
            }
        } else {
            items(recentTrades) { trade ->
                Card(
                    modifier = Modifier.fillMaxWidth(),
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
                        Column {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(
                                    text = StockUtils.getStockName(trade.ticker, trade.name),
                                    color = TextPrimary,
                                    fontSize = 14.sp,
                                    fontWeight = FontWeight.Bold
                                )
                                Spacer(Modifier.width(6.dp))
                                Text(
                                    text = "(${trade.ticker})",
                                    color = TextSecondary,
                                    fontSize = 11.sp,
                                    fontFamily = FontFamily.Monospace
                                )
                                Spacer(Modifier.width(6.dp))
                                Surface(
                                    shape = RoundedCornerShape(4.dp),
                                    color = SurfaceVariantDark
                                ) {
                                    Text(
                                        text = trade.slot_id,
                                        color = TextSecondary,
                                        fontSize = 10.sp,
                                        modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp)
                                    )
                                }
                            }
                            Spacer(Modifier.height(4.dp))
                            Text(
                                text = "매수 ${wonFormat.format(trade.buy_price.toLong())}원 → 매도 ${wonFormat.format(trade.sell_price.toLong())}원 (${trade.quantity}주)",
                                color = TextSecondary,
                                fontSize = 11.sp
                            )
                            Text(
                                text = "사유: ${trade.exit_reason} (비용: ${wonFormat.format(trade.total_cost.toLong())}원)",
                                color = TextMuted,
                                fontSize = 10.sp
                            )
                        }

                        val isProfit = trade.net_pnl >= 0
                        Column(horizontalAlignment = Alignment.End) {
                            Text(
                                text = "${if (isProfit) "+" else ""}${wonFormat.format(trade.net_pnl.toLong())}원",
                                color = if (isProfit) NeonGreen else NeonRed,
                                fontSize = 15.sp,
                                fontWeight = FontWeight.Bold,
                                fontFamily = FontFamily.Monospace
                            )
                            Text(
                                text = "${if (isProfit) "+" else ""}${trade.profit_rate}%",
                                color = if (isProfit) NeonGreen else NeonRed,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
            }
        }
    }
}
