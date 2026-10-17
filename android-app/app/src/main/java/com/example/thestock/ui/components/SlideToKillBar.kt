package com.example.thestock.ui.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.Orientation
import androidx.compose.foundation.gestures.draggable
import androidx.compose.foundation.gestures.rememberDraggableState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.thestock.theme.NeonRed
import com.example.thestock.theme.SurfaceDark
import com.example.thestock.theme.TextPrimary
import kotlin.math.roundToInt

@Composable
fun SlideToKillBar(
    onKillConfirmed: () -> Unit,
    modifier: Modifier = Modifier
) {
    var offsetX by remember { mutableFloatStateOf(0f) }
    val maxDragDistance = 600f // 픽셀 기준 최대 드래그 거리
    var showDialog by remember { mutableStateOf(false) }

    val animatedOffset by animateFloatAsState(
        targetValue = offsetX,
        label = "dragOffset"
    )

    if (showDialog) {
        AlertDialog(
            onDismissRequest = {
                showDialog = false
                offsetX = 0f
            },
            containerColor = SurfaceDark,
            title = {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Default.Warning, contentDescription = null, tint = NeonRed)
                    Spacer(Modifier.width(8.dp))
                    Text("마스터 킬스위치 발동", color = NeonRed, fontWeight = FontWeight.Bold)
                }
            },
            text = {
                Text(
                    "전체 4개 슬롯의 모든 보유 포지션을 시장가(-0.1% 슬리피지 페널티)로 즉시 전량 매도하고 모든 자동매매를 동결하시겠습니까?",
                    color = TextPrimary
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        showDialog = false
                        offsetX = 0f
                        onKillConfirmed()
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = NeonRed)
                ) {
                    Text("긴급 전량 매도 집행", color = Color.White, fontWeight = FontWeight.Bold)
                }
            },
            dismissButton = {
                OutlinedButton(
                    onClick = {
                        showDialog = false
                        offsetX = 0f
                    }
                ) {
                    Text("취소", color = TextPrimary)
                }
            }
        )
    }

    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(58.dp)
            .clip(RoundedCornerShape(29.dp))
            .background(Color(0x33FF1744)),
        contentAlignment = Alignment.CenterStart
    ) {
        // 안내 텍스트
        Box(
            modifier = Modifier.fillMaxSize(),
            contentAlignment = Alignment.Center
        ) {
            Text(
                text = "SLIDE TO KILL ALL POSITIONS ▶▶",
                color = NeonRed,
                fontSize = 13.sp,
                fontWeight = FontWeight.ExtraBold,
                letterSpacing = 1.2.sp
            )
        }

        // 드래그 가능한 빨간색 원형 슬라이더 버튼
        Box(
            modifier = Modifier
                .offset { IntOffset(animatedOffset.roundToInt(), 0) }
                .padding(4.dp)
                .size(50.dp)
                .clip(CircleShape)
                .background(NeonRed)
                .draggable(
                    orientation = Orientation.Horizontal,
                    state = rememberDraggableState { delta ->
                        val newOffset = (offsetX + delta).coerceIn(0f, maxDragDistance)
                        offsetX = newOffset
                        if (newOffset >= maxDragDistance * 0.95f) {
                            showDialog = true
                        }
                    },
                    onDragStopped = {
                        if (offsetX < maxDragDistance * 0.95f) {
                            offsetX = 0f
                        }
                    }
                ),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Default.Warning,
                contentDescription = "Kill Switch Handle",
                tint = Color.White,
                modifier = Modifier.size(24.dp)
            )
        }
    }
}
