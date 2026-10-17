package com.example.thestock.ui.settings

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Security
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material.icons.filled.PowerSettingsNew
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import com.example.thestock.BuildConfig
import com.example.thestock.theme.*

@Composable
fun SettingsScreen(
    currentUrl: String,
    onUrlChange: (String) -> Unit,
    onTestConnection: () -> Unit,
    isConnected: Boolean,
    serverControlState: String = "STOPPED",
    onStartServer: () -> Unit = {},
    onStopServer: () -> Unit = {},
    onCheckUpdate: () -> Unit = {},
    isCheckingUpdate: Boolean = false,
    updateStatusMessage: String? = null,
    modifier: Modifier = Modifier
) {
    var urlInput by remember(currentUrl) { mutableStateOf(currentUrl) }

    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .background(BackgroundDark)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        item {
            Text(
                text = "인프라 & 관제 설정",
                color = TextPrimary,
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold
            )
            Text(
                text = "홈 PC 원격 전원 제어 및 클라우드(Firebase) 관제",
                color = TextSecondary,
                fontSize = 12.sp
            )
        }

        // 🖥️ 원격 홈 PC 서버 전원 제어 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                Icons.Default.PowerSettingsNew,
                                contentDescription = null,
                                tint = if (serverControlState == "RUNNING") NeonGreen else CyberCyan,
                                modifier = Modifier.size(20.dp)
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("원격 홈 PC 서버 전원", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        }

                        // 서버 상태 뱃지
                        Surface(
                            color = when (serverControlState) {
                                "RUNNING" -> NeonGreen.copy(alpha = 0.15f)
                                "STARTING" -> CyberCyan.copy(alpha = 0.15f)
                                else -> TextMuted.copy(alpha = 0.15f)
                            },
                            shape = RoundedCornerShape(6.dp),
                            border = BorderStroke(
                                1.dp,
                                when (serverControlState) {
                                    "RUNNING" -> NeonGreen
                                    "STARTING" -> CyberCyan
                                    else -> SurfaceBorder
                                }
                            )
                        ) {
                            Text(
                                text = when (serverControlState) {
                                    "RUNNING" -> "🟢 실행 중"
                                    "STARTING" -> "🟡 기동 중..."
                                    else -> "⚪ 정지됨"
                                },
                                color = when (serverControlState) {
                                    "RUNNING" -> NeonGreen
                                    "STARTING" -> CyberCyan
                                    else -> TextSecondary
                                },
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                            )
                        }
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = "스마트폰에서 클라우드를 통해 홈 PC의 주식 자동매매 백엔드 서버를 원클릭으로 켜고 끕니다.",
                        color = TextSecondary,
                        fontSize = 12.sp,
                        lineHeight = 16.sp
                    )

                    Spacer(Modifier.height(14.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Button(
                            onClick = onStartServer,
                            modifier = Modifier.weight(1f),
                            enabled = serverControlState != "RUNNING" && serverControlState != "STARTING",
                            colors = ButtonDefaults.buttonColors(
                                containerColor = NeonGreen,
                                contentColor = BackgroundDark,
                                disabledContainerColor = SurfaceVariantDark,
                                disabledContentColor = TextMuted
                            )
                        ) {
                            Icon(Icons.Default.PlayArrow, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("서버 기동", fontWeight = FontWeight.Bold)
                        }

                        Button(
                            onClick = onStopServer,
                            modifier = Modifier.weight(1f),
                            enabled = serverControlState == "RUNNING" || serverControlState == "STARTING",
                            colors = ButtonDefaults.buttonColors(
                                containerColor = NeonRed,
                                contentColor = Color.White,
                                disabledContainerColor = SurfaceVariantDark,
                                disabledContentColor = TextMuted
                            )
                        ) {
                            Icon(Icons.Default.Stop, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("서버 종료", fontWeight = FontWeight.Bold)
                        }
                    }
                }
            }
        }

        // 백엔드 URL 설정 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Cloud, contentDescription = null, tint = CyberCyan, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("홈 PC 백엔드 서버 주소", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                    }

                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(
                        value = urlInput,
                        onValueChange = {
                            urlInput = it
                            onUrlChange(it)
                        },
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = CyberCyan,
                            unfocusedBorderColor = SurfaceBorder,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary,
                            focusedContainerColor = SurfaceVariantDark,
                            unfocusedContainerColor = SurfaceVariantDark
                        ),
                        placeholder = { Text("http://192.168.0.x:8000", color = TextMuted) },
                        singleLine = true
                    )

                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = onTestConnection,
                        modifier = Modifier.fillMaxWidth(),
                        colors = ButtonDefaults.buttonColors(containerColor = CyberCyan)
                    ) {
                        Icon(Icons.Default.Refresh, contentDescription = null, tint = BackgroundDark, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("서버 상태 즉시 동기화", color = BackgroundDark, fontWeight = FontWeight.Bold)
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = if (isConnected) "🟢 클라우드(Firebase) 실시간 관제 연결됨 (LTE/Wi-Fi 무관)" else "🔴 연결 대기 중",
                        color = if (isConnected) NeonGreen else NeonRed,
                        fontSize = 12.sp
                    )
                }
            }
        }

        // 비상 손절 프로토콜(Emergency Flush) 안내 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Default.Security, contentDescription = null, tint = NeonRed, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("통신 장애 비상 탈출 (Emergency Flush)", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                    }

                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = "홈 PC 인터넷이 60초 이상 단절되었다가 복구되면, 워치독(Watchdog)이 즉시 보유 종목 시세를 전수 검사하여 손절가(-3.5%)를 이탈한 포지션을 시장가로 전량 강제 청산합니다.",
                        color = TextSecondary,
                        fontSize = 12.sp,
                        lineHeight = 18.sp
                    )
                }
            }
        }

        // 인앱 자동 업데이트 & 버전 정보 카드
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = SurfaceDark),
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, SurfaceBorder)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Default.SystemUpdate, contentDescription = null, tint = CyberCyan, modifier = Modifier.size(20.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("앱 버전 & 무선 업데이트", color = TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                        }
                        Text(
                            text = "v${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})",
                            color = CyberCyan,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }

                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = "서버 및 Firebase RTDB에 등록된 최신 버전과 대조하여 인앱 원클릭으로 무선 업데이트합니다.",
                        color = TextSecondary,
                        fontSize = 12.sp
                    )

                    Spacer(Modifier.height(12.dp))
                    Button(
                        onClick = onCheckUpdate,
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !isCheckingUpdate,
                        colors = ButtonDefaults.buttonColors(
                            containerColor = SurfaceVariantDark,
                            contentColor = CyberCyan
                        )
                    ) {
                        if (isCheckingUpdate) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                color = CyberCyan,
                                strokeWidth = 2.dp
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("업데이트 확인 중...")
                        } else {
                            Icon(Icons.Default.Refresh, contentDescription = null, tint = CyberCyan, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("최신 업데이트 확인", fontWeight = FontWeight.SemiBold)
                        }
                    }

                    updateStatusMessage?.let { msg ->
                        Spacer(Modifier.height(8.dp))
                        Text(
                            text = msg,
                            color = if (msg.contains("최신")) NeonGreen else TextSecondary,
                            fontSize = 12.sp
                        )
                    }
                }
            }
        }
    }
}
