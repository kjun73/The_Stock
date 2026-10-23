package com.example.thestock.ui.main

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.thestock.data.SystemStatusDto
import com.example.thestock.data.TradingRepository
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch

class MainScreenViewModel(
    val repository: TradingRepository = TradingRepository()
) : ViewModel() {
    val status: StateFlow<SystemStatusDto> = repository.status
    val isConnected: StateFlow<Boolean> = repository.isConnected
    val serverUrl: StateFlow<String> = repository.serverUrl
    val serverControlState: StateFlow<String> = repository.serverControlState

    private val _uiMessage = MutableSharedFlow<String>()
    val uiMessage: SharedFlow<String> = _uiMessage.asSharedFlow()

    fun refresh() {
        viewModelScope.launch {
            try {
                repository.fetchStatus()
            } catch (e: Exception) {
                _uiMessage.emit("상태 갱신 실패: ${e.message}")
            }
        }
    }

    fun startRemoteServer() {
        try {
            repository.requestServerStart()
        } catch (e: Exception) {
            viewModelScope.launch {
                _uiMessage.emit("서버 기동 요청 실패: ${e.message}")
            }
        }
    }

    fun stopRemoteServer() {
        try {
            repository.requestServerStop()
        } catch (e: Exception) {
            viewModelScope.launch {
                _uiMessage.emit("서버 종료 요청 실패: ${e.message}")
            }
        }
    }

    fun triggerKillSwitch() {
        viewModelScope.launch {
            try {
                val ok = repository.triggerKillSwitch()
                if (ok) {
                    _uiMessage.emit("🚨 비상 킬스위치 발동: 전 슬롯 즉시 정지")
                } else {
                    _uiMessage.emit("킬스위치 명령 전송 실패")
                }
            } catch (e: Exception) {
                _uiMessage.emit("킬스위치 실행 오류: ${e.message}")
            }
        }
    }

    fun toggleSlot(slotId: String, isActive: Boolean) {
        viewModelScope.launch {
            try {
                val ok = repository.toggleSlot(slotId, isActive)
                if (ok) {
                    val stateText = if (isActive) "가동" else "정지"
                    _uiMessage.emit("[$slotId] 슬롯을 ${stateText} 상태로 변경했습니다.")
                }
            } catch (e: Exception) {
                _uiMessage.emit("슬롯 토글 오류: ${e.message}")
            }
        }
    }

    fun closePosition(slotId: String, ticker: String) {
        viewModelScope.launch {
            try {
                val ok = repository.closePosition(slotId, ticker)
                if (ok) {
                    _uiMessage.emit("[$slotId] $ticker 수동 청산 요청을 전송했습니다.")
                }
            } catch (e: Exception) {
                _uiMessage.emit("포지션 청산 오류: ${e.message}")
            }
        }
    }

    fun updateServerUrl(url: String) {
        repository.serverUrl.value = url
    }

    override fun onCleared() {
        super.onCleared()
        repository.cleanup()
    }
}
