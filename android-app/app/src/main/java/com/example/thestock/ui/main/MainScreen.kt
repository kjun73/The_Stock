package com.example.thestock.ui.main

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.ListAlt
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.example.thestock.theme.*
import com.example.thestock.ui.dashboard.DashboardScreen
import com.example.thestock.ui.intelligence.IntelligenceScreen
import com.example.thestock.ui.positions.PositionsScreen
import com.example.thestock.ui.settings.SettingsScreen

import androidx.compose.ui.platform.LocalContext
import com.example.thestock.ui.dialog.UpdateDialog
import com.example.thestock.util.AppUpdateInfo
import com.example.thestock.util.UpdateManager
import kotlinx.coroutines.launch

enum class MainTab(val title: String, val icon: ImageVector) {
    DASHBOARD("대시보드", Icons.Default.Dashboard),
    POSITIONS("포지션", Icons.Default.ListAlt),
    INTELLIGENCE("AI·JEV", Icons.Default.AutoAwesome),
    SETTINGS("설정", Icons.Default.Settings)
}

@Composable
fun MainScreen(
    modifier: Modifier = Modifier,
    viewModel: MainScreenViewModel = viewModel { MainScreenViewModel() }
) {
    val status by viewModel.status.collectAsStateWithLifecycle()
    val isConnected by viewModel.isConnected.collectAsStateWithLifecycle()
    val serverUrl by viewModel.serverUrl.collectAsStateWithLifecycle()
    val serverControlState by viewModel.serverControlState.collectAsStateWithLifecycle()

    var selectedTab by remember { mutableStateOf(MainTab.DASHBOARD) }
    var targetSlotId by remember { mutableStateOf("SLOT_A") }

    // 자동 업데이트 상태 관리
    var appUpdateInfo by remember { mutableStateOf<AppUpdateInfo?>(null) }
    var isCheckingUpdate by remember { mutableStateOf(false) }
    var updateStatusMessage by remember { mutableStateOf<String?>(null) }
    val coroutineScope = rememberCoroutineScope()

    // 앱 시작 시 자동 업데이트 확인 (1회)
    LaunchedEffect(Unit) {
        val update = UpdateManager.checkUpdate(serverUrl)
        if (update != null) {
            appUpdateInfo = update
        }
    }

    fun checkUpdateManually() {
        coroutineScope.launch {
            isCheckingUpdate = true
            updateStatusMessage = null
            val update = UpdateManager.checkUpdate(serverUrl)
            isCheckingUpdate = false
            if (update != null) {
                appUpdateInfo = update
                updateStatusMessage = "새로운 버전 v${update.versionName}이 준비되었습니다."
            } else {
                updateStatusMessage = "현재 최신 버전을 사용 중입니다."
            }
        }
    }

    Scaffold(
        modifier = modifier.fillMaxSize(),
        containerColor = BackgroundDark,
        bottomBar = {
            NavigationBar(
                containerColor = SurfaceDark,
                contentColor = TextPrimary
            ) {
                MainTab.values().forEach { tab ->
                    NavigationBarItem(
                        selected = selectedTab == tab,
                        onClick = { selectedTab = tab },
                        icon = { Icon(tab.icon, contentDescription = tab.title) },
                        label = { Text(tab.title) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = CyberCyan,
                            selectedTextColor = CyberCyan,
                            unselectedIconColor = TextMuted,
                            unselectedTextColor = TextMuted,
                            indicatorColor = SurfaceVariantDark
                        )
                    )
                }
            }
        }
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .background(BackgroundDark)
        ) {
            when (selectedTab) {
                MainTab.DASHBOARD -> DashboardScreen(
                    isConnected = isConnected,
                    portfolio = status.portfolio,
                    marketFilter = status.market_filter,
                    slots = status.slots,
                    onKillConfirmed = { viewModel.triggerKillSwitch() },
                    onSlotClick = { slotId ->
                        targetSlotId = slotId
                        selectedTab = MainTab.POSITIONS
                    }
                )
                MainTab.POSITIONS -> PositionsScreen(
                    slots = status.slots,
                    recentTrades = status.recent_trades,
                    initialSlotId = targetSlotId,
                    onToggleSlot = { slotId, active -> viewModel.toggleSlot(slotId, active) },
                    onClosePosition = { slotId, ticker -> viewModel.closePosition(slotId, ticker) }
                )
                MainTab.INTELLIGENCE -> IntelligenceScreen(
                    recentTrades = status.recent_trades,
                    intelligence = status.intelligence
                )
                MainTab.SETTINGS -> SettingsScreen(
                    currentUrl = serverUrl,
                    onUrlChange = { viewModel.updateServerUrl(it) },
                    onTestConnection = { viewModel.refresh() },
                    isConnected = isConnected,
                    serverControlState = serverControlState,
                    onStartServer = { viewModel.startRemoteServer() },
                    onStopServer = { viewModel.stopRemoteServer() },
                    onCheckUpdate = { checkUpdateManually() },
                    isCheckingUpdate = isCheckingUpdate,
                    updateStatusMessage = updateStatusMessage
                )
            }

            // 업데이트 다이얼로그 팝업
            appUpdateInfo?.let { updateInfo ->
                UpdateDialog(
                    updateInfo = updateInfo,
                    onDismissRequest = { appUpdateInfo = null }
                )
            }
        }
    }
}
