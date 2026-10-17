package com.example.thestock.data

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit

class TradingRepository(
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.IO)
) {
    companion object {
        private const val TAG = "TradingRepository"
        // 싱가포르 Firebase Realtime Database 클라우드 중계 엔드포인트
        private const val FIREBASE_RTDB_STATUS_URL =
            "https://thestock-513c0-default-rtdb.asia-southeast1.firebasedatabase.app/system_status.json"
        private const val FIREBASE_RTDB_COMMANDS_URL =
            "https://thestock-513c0-default-rtdb.asia-southeast1.firebasedatabase.app/commands.json"
        private const val FIREBASE_RTDB_SERVER_CONTROL_URL =
            "https://thestock-513c0-default-rtdb.asia-southeast1.firebasedatabase.app/server_control.json"
    }

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    // 백엔드 주소 (기본값은 빈 문자열로 두어 기본적으로 클라우드 Firebase RTDB 우선 통신)
    val serverUrl = MutableStateFlow("")

    private val _status = MutableStateFlow(createInitialDummyStatus())
    val status: StateFlow<SystemStatusDto> = _status.asStateFlow()

    private val _isConnected = MutableStateFlow(false)
    val isConnected: StateFlow<Boolean> = _isConnected.asStateFlow()

    // 원격 서버 실행 상태 ("STOPPED", "RUNNING", "STARTING")
    private val _serverControlState = MutableStateFlow("STOPPED")
    val serverControlState: StateFlow<String> = _serverControlState.asStateFlow()

    private val _isRefreshing = MutableStateFlow(false)
    val isRefreshing: StateFlow<Boolean> = _isRefreshing.asStateFlow()

    init {
        // 백그라운드 2초 주기 자동 폴링 동기화
        scope.launch {
            while (isActive) {
                fetchStatus()
                fetchServerControlState()
                delay(2000)
            }
        }
    }

    suspend fun fetchStatus() {
        // 1차: 만약 사용자가 로컬 IP 주소를 직접 설정했다면 로컬 백엔드 확인 시도
        val localUrl = serverUrl.value.trim().trimEnd('/')
        if (localUrl.isNotBlank() && !localUrl.contains("10.0.2.2")) {
            val success = tryFetchFromLocal("${localUrl}/api/status")
            if (success) {
                _isConnected.value = true
                return
            }
        }

        // 2차 (기본 & LTE/외부망): Firebase Realtime Database 클라우드 조회
        tryFetchFromFirebase()
    }

    private fun tryFetchFromLocal(url: String): Boolean {
        return try {
            val request = Request.Builder().url(url).get().build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val bodyString = response.body?.string()
                    if (!bodyString.isNullOrBlank()) {
                        val parsed = json.decodeFromString<SystemStatusDto>(bodyString)
                        _status.value = parsed.copy(status = "ONLINE")
                        true
                    } else false
                } else false
            }
        } catch (e: Exception) {
            false
        }
    }

    private fun tryFetchFromFirebase(): Boolean {
        return try {
            val request = Request.Builder()
                .url(FIREBASE_RTDB_STATUS_URL)
                .get()
                .build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val bodyString = response.body?.string()
                    if (!bodyString.isNullOrBlank() && bodyString != "null") {
                        val parsed = json.decodeFromString<SystemStatusDto>(bodyString)

                        // 심장박동 Liveness 검증 (통신사 시각 오차 및 네트워크 지연 방어 위해 절대값 25초 이내 판정)
                        val nowMs = System.currentTimeMillis()
                        val isRecentHeartbeat = Math.abs(nowMs - parsed.last_heartbeat) < 25000L
                        val isOnline = parsed.server_status == "ONLINE" && isRecentHeartbeat

                        _isConnected.value = isOnline
                        _status.value = parsed.copy(
                            status = if (isOnline) "ONLINE" else "OFFLINE"
                        )
                        Log.d(TAG, "Firebase RTDB 동기화: isOnline=$isOnline (Heartbeat diff=${Math.abs(nowMs - parsed.last_heartbeat)/1000}s)")
                        true
                    } else {
                        _isConnected.value = false
                        false
                    }
                } else {
                    _isConnected.value = false
                    false
                }
            }
        } catch (e: Exception) {
            _isConnected.value = false
            false
        }
    }

    private fun fetchServerControlState() {
        try {
            val request = Request.Builder()
                .url(FIREBASE_RTDB_SERVER_CONTROL_URL)
                .get()
                .build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val body = response.body?.string()
                    if (!body.isNullOrBlank() && body != "null") {
                        val control = json.decodeFromString<ServerControlDto>(body)
                        _serverControlState.value = control.state
                    }
                }
            }
        } catch (_: Exception) {}
    }

    /**
     * 스마트폰 앱에서 홈 PC의 트레이딩 서버를 원격 기동합니다.
     */
    fun requestServerStart() {
        scope.launch {
            _serverControlState.value = "STARTING"
            val payload = """
                {
                    "command": "START",
                    "command_id": "${UUID.randomUUID()}",
                    "requested_at": ${System.currentTimeMillis()}
                }
            """.trimIndent()
            patchServerControl(payload)
        }
    }

    /**
     * 스마트폰 앱에서 홈 PC의 트레이딩 서버를 원격 종료합니다.
     */
    fun requestServerStop() {
        scope.launch {
            _serverControlState.value = "STOPPED"
            val payload = """
                {
                    "command": "STOP",
                    "command_id": "${UUID.randomUUID()}",
                    "requested_at": ${System.currentTimeMillis()}
                }
            """.trimIndent()
            patchServerControl(payload)
        }
    }

    private fun patchServerControl(jsonPayload: String) {
        try {
            val request = Request.Builder()
                .url(FIREBASE_RTDB_SERVER_CONTROL_URL)
                .patch(jsonPayload.toRequestBody("application/json".toMediaType()))
                .build()
            client.newCall(request).execute().close()
            Log.d(TAG, "Server control patched: $jsonPayload")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to patch server control: ${e.message}")
        }
    }

    suspend fun triggerKillSwitch(): Boolean {
        // 로컬 상태 즉시 반영 (낙관적 업데이트)
        val current = _status.value
        _status.value = current.copy(
            slots = current.slots.mapValues { (_, acc) ->
                acc.copy(is_active = false, positions = emptyList(), stock_value = 0.0)
            }
        )

        // 1. Firebase RTDB에 원격 명령 발행 (LTE/외부망 어디서든 홈 PC로 즉시 전달)
        val cmdPayload = """
            {
                "type": "KILL_SWITCH",
                "timestamp": "${System.currentTimeMillis()}",
                "processed": false
            }
        """.trimIndent()
        postCommandToFirebase(cmdPayload)

        // 2. 만약 로컬 서버 주소가 설정되어 있다면 직접 호출도 병행
        val localUrl = serverUrl.value.trim().trimEnd('/')
        if (localUrl.isNotBlank() && !localUrl.contains("10.0.2.2")) {
            try {
                val request = Request.Builder()
                    .url("$localUrl/api/kill-switch")
                    .post("{}".toRequestBody("application/json".toMediaType()))
                    .build()
                client.newCall(request).execute().close()
            } catch (_: Exception) {}
        }
        return true
    }

    suspend fun toggleSlot(slotId: String, isActive: Boolean): Boolean {
        // 로컬 상태 즉시 반영
        val current = _status.value
        val target = current.slots[slotId]
        if (target != null) {
            _status.value = current.copy(
                slots = current.slots + (slotId to target.copy(is_active = isActive))
            )
        }

        // Firebase RTDB 원격 명령 발행
        val cmdPayload = """
            {
                "type": "TOGGLE_SLOT",
                "slot_id": "$slotId",
                "is_active": $isActive,
                "timestamp": "${System.currentTimeMillis()}",
                "processed": false
            }
        """.trimIndent()
        postCommandToFirebase(cmdPayload)

        // 로컬 백엔드 직접 호출 병행
        val localUrl = serverUrl.value.trim().trimEnd('/')
        if (localUrl.isNotBlank() && !localUrl.contains("10.0.2.2")) {
            try {
                val payload = """{"is_active": $isActive}"""
                val request = Request.Builder()
                    .url("$localUrl/api/slots/$slotId/toggle")
                    .post(payload.toRequestBody("application/json".toMediaType()))
                    .build()
                client.newCall(request).execute().close()
            } catch (_: Exception) {}
        }
        return true
    }

    suspend fun closePosition(slotId: String, ticker: String): Boolean {
        // 로컬 상태 즉시 반영
        val current = _status.value
        val target = current.slots[slotId]
        if (target != null) {
            _status.value = current.copy(
                slots = current.slots + (slotId to target.copy(
                    positions = target.positions.filter { it.ticker != ticker }
                ))
            )
        }

        // Firebase RTDB 원격 명령 발행
        val cmdPayload = """
            {
                "type": "CLOSE_POSITION",
                "slot_id": "$slotId",
                "ticker": "$ticker",
                "timestamp": "${System.currentTimeMillis()}",
                "processed": false
            }
        """.trimIndent()
        postCommandToFirebase(cmdPayload)

        // 로컬 백엔드 직접 호출 병행
        val localUrl = serverUrl.value.trim().trimEnd('/')
        if (localUrl.isNotBlank() && !localUrl.contains("10.0.2.2")) {
            try {
                val payload = """{"slot_id": "$slotId", "ticker": "$ticker"}"""
                val request = Request.Builder()
                    .url("$localUrl/api/positions/close")
                    .post(payload.toRequestBody("application/json".toMediaType()))
                    .build()
                client.newCall(request).execute().close()
            } catch (_: Exception) {}
        }
        return true
    }

    private fun postCommandToFirebase(commandJson: String) {
        scope.launch {
            try {
                val request = Request.Builder()
                    .url(FIREBASE_RTDB_COMMANDS_URL)
                    .post(commandJson.toRequestBody("application/json".toMediaType()))
                    .build()
                client.newCall(request).execute().use { response ->
                    Log.d(TAG, "Firebase command post result: code=${response.code}")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to post command to Firebase RTDB: ${e.message}")
            }
        }
    }

    private fun createInitialDummyStatus(): SystemStatusDto {
        return SystemStatusDto(
            status = "OFFLINE",
            server_status = "OFFLINE",
            last_heartbeat = 0L,
            portfolio = PortfolioSummary(
                initial_capital = 40_000_000.0,
                total_asset = 40_000_000.0,
                total_cash = 40_000_000.0,
                total_stock = 0.0,
                total_stock_value = 0.0,
                total_profit = 0.0,
                total_profit_rate = 0.0,
                open_positions_count = 0,
                updated_at = ""
            ),
            market_filter = MarketFilterStatus(
                kospi_index = 2600.0,
                kospi_20ma = 2580.0,
                is_bear_market = false,
                is_panic_sell_day = false
            ),
            slots = mapOf(
                "SLOT_A" to SlotAccountDto(
                    slot_id = "SLOT_A",
                    name = "외인·기관 수급 스윙 (T+1)",
                    initial_capital = 10_000_000.0,
                    cash_balance = 10_000_000.0,
                    stock_value = 0.0,
                    total_asset = 10_000_000.0,
                    total_profit = 0.0,
                    profit_rate = 0.0,
                    is_active = true,
                    positions = emptyList()
                ),
                "SLOT_B" to SlotAccountDto(
                    slot_id = "SLOT_B",
                    name = "KODEX 현물 ETF 볼린저 그리드",
                    initial_capital = 10_000_000.0,
                    cash_balance = 10_000_000.0,
                    stock_value = 0.0,
                    total_asset = 10_000_000.0,
                    total_profit = 0.0,
                    profit_rate = 0.0,
                    is_active = true,
                    positions = emptyList()
                ),
                "SLOT_C" to SlotAccountDto(
                    slot_id = "SLOT_C",
                    name = "변동성 돌파 + JEV 단타",
                    initial_capital = 10_000_000.0,
                    cash_balance = 10_000_000.0,
                    stock_value = 0.0,
                    total_asset = 10_000_000.0,
                    total_profit = 0.0,
                    profit_rate = 0.0,
                    is_active = true,
                    positions = emptyList()
                ),
                "SLOT_D" to SlotAccountDto(
                    slot_id = "SLOT_D",
                    name = "전고점 돌파 첫 눌림목 스윙",
                    initial_capital = 10_000_000.0,
                    cash_balance = 10_000_000.0,
                    stock_value = 0.0,
                    total_asset = 10_000_000.0,
                    total_profit = 0.0,
                    profit_rate = 0.0,
                    is_active = true,
                    positions = emptyList()
                )
            ),
            recent_trades = emptyList()
        )
    }
}
