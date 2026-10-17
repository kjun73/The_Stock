package com.example.thestock.ui.dialog

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.thestock.BuildConfig
import com.example.thestock.theme.*
import com.example.thestock.util.AppUpdateInfo
import com.example.thestock.util.UpdateManager
import kotlinx.coroutines.launch
import java.io.File

private enum class DownloadState {
    IDLE,
    DOWNLOADING,
    DOWNLOADED,
    ERROR
}

@Composable
fun UpdateDialog(
    updateInfo: AppUpdateInfo,
    onDismissRequest: () -> Unit
) {
    val context = LocalContext.current
    val coroutineScope = rememberCoroutineScope()

    var downloadState by remember { mutableStateOf(DownloadState.IDLE) }
    var progress by remember { mutableFloatStateOf(0f) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    var downloadedFile by remember { mutableStateOf<File?>(null) }

    fun startDownload() {
        downloadState = DownloadState.DOWNLOADING
        progress = 0f
        errorMessage = null

        coroutineScope.launch {
            val result = UpdateManager.downloadApk(
                context = context,
                apkUrl = updateInfo.apkUrl,
                onProgress = { p -> progress = p }
            )

            result.onSuccess { file ->
                downloadedFile = file
                downloadState = DownloadState.DOWNLOADED
                // 권한 확인 후 바로 설치 또는 설정 유도
                if (UpdateManager.canInstallPackage(context)) {
                    UpdateManager.installApk(context, file)
                }
            }.onFailure { e ->
                downloadState = DownloadState.ERROR
                errorMessage = e.message ?: "다운로드에 실패했습니다."
            }
        }
    }

    AlertDialog(
        onDismissRequest = {
            if (!updateInfo.isForce && downloadState != DownloadState.DOWNLOADING) {
                onDismissRequest()
            }
        },
        containerColor = SurfaceDark,
        icon = {
            Text(
                text = "🚀",
                fontSize = 32.sp
            )
        },
        title = {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    text = "새로운 업데이트 발견",
                    style = MaterialTheme.typography.titleLarge.copy(
                        fontWeight = FontWeight.Bold,
                        fontSize = 18.sp,
                        color = TextPrimary
                    )
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = "v${BuildConfig.VERSION_NAME} ➔ v${updateInfo.versionName}",
                    style = MaterialTheme.typography.bodyMedium.copy(
                        color = CyberCyan,
                        fontWeight = FontWeight.SemiBold
                    )
                )
            }
        },
        text = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 4.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                if (updateInfo.notes.isNotBlank()) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .background(SurfaceVariantDark)
                            .padding(12.dp)
                    ) {
                        Text(
                            text = updateInfo.notes,
                            style = MaterialTheme.typography.bodyMedium,
                            color = TextSecondary,
                            lineHeight = 18.sp
                        )
                    }
                }

                // 다운로드 상태 표시
                when (downloadState) {
                    DownloadState.IDLE -> {
                        Text(
                            text = "원활한 실시간 시세 관제 및 최신 AI 전략 모니터링을 위해 업데이트를 권장합니다.",
                            style = MaterialTheme.typography.bodySmall,
                            color = TextMuted
                        )
                    }
                    DownloadState.DOWNLOADING -> {
                        Column(
                            modifier = Modifier.fillMaxWidth(),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            if (progress >= 0f) {
                                LinearProgressIndicator(
                                    progress = { progress },
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(8.dp)
                                        .clip(RoundedCornerShape(4.dp)),
                                    color = CyberCyan,
                                    trackColor = SurfaceVariantDark
                                )
                                Text(
                                    text = "다운로드 중... ${(progress * 100).toInt()}%",
                                    style = MaterialTheme.typography.bodySmall,
                                    modifier = Modifier.align(Alignment.End),
                                    color = CyberCyan
                                )
                            } else {
                                LinearProgressIndicator(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(8.dp)
                                        .clip(RoundedCornerShape(4.dp)),
                                    color = CyberCyan,
                                    trackColor = SurfaceVariantDark
                                )
                                Text(
                                    text = "다운로드 중...",
                                    style = MaterialTheme.typography.bodySmall,
                                    modifier = Modifier.align(Alignment.End),
                                    color = CyberCyan
                                )
                            }
                        }
                    }
                    DownloadState.DOWNLOADED -> {
                        val canInstall = UpdateManager.canInstallPackage(context)
                        if (!canInstall) {
                            Text(
                                text = "⚠️ 설치를 계속하려면 '이 출처의 앱 설치 허용' 권한이 필요합니다.",
                                style = MaterialTheme.typography.bodySmall,
                                color = NeonRed,
                                fontWeight = FontWeight.SemiBold
                            )
                        } else {
                            Text(
                                text = "다운로드가 완료되었습니다. 설치를 진행합니다.",
                                style = MaterialTheme.typography.bodySmall,
                                color = NeonGreen
                            )
                        }
                    }
                    DownloadState.ERROR -> {
                        Text(
                            text = "오류: ${errorMessage ?: "다운로드 실패"}",
                            style = MaterialTheme.typography.bodySmall,
                            color = NeonRed
                        )
                    }
                }
            }
        },
        confirmButton = {
            when (downloadState) {
                DownloadState.IDLE -> {
                    Button(
                        onClick = { startDownload() },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = CyberCyan,
                            contentColor = BackgroundDark
                        )
                    ) {
                        Text("업데이트", fontWeight = FontWeight.Bold)
                    }
                }
                DownloadState.DOWNLOADING -> {
                    Button(
                        onClick = {},
                        enabled = false,
                        colors = ButtonDefaults.buttonColors(
                            disabledContainerColor = SurfaceVariantDark,
                            disabledContentColor = TextMuted
                        )
                    ) {
                        Text("다운로드 중...")
                    }
                }
                DownloadState.DOWNLOADED -> {
                    val canInstall = UpdateManager.canInstallPackage(context)
                    if (!canInstall) {
                        Button(
                            onClick = { UpdateManager.openInstallPermissionSetting(context) },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = NeonRed,
                                contentColor = Color.White
                            )
                        ) {
                            Text("권한 설정", fontWeight = FontWeight.Bold)
                        }
                    } else {
                        Button(
                            onClick = {
                                downloadedFile?.let { UpdateManager.installApk(context, it) }
                            },
                            colors = ButtonDefaults.buttonColors(
                                containerColor = NeonGreen,
                                contentColor = BackgroundDark
                            )
                        ) {
                            Text("설치하기", fontWeight = FontWeight.Bold)
                        }
                    }
                }
                DownloadState.ERROR -> {
                    Button(
                        onClick = { startDownload() },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = CyberCyan,
                            contentColor = BackgroundDark
                        )
                    ) {
                        Text("다시 시도", fontWeight = FontWeight.Bold)
                    }
                }
            }
        },
        dismissButton = {
            if (!updateInfo.isForce && downloadState != DownloadState.DOWNLOADING) {
                TextButton(onClick = onDismissRequest) {
                    Text("나중에", color = TextSecondary)
                }
            }
        }
    )
}
