package com.example.thestock.ui.main

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.thestock.data.SystemStatusDto
import com.example.thestock.data.TradingRepository
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class MainScreenViewModel(
    val repository: TradingRepository = TradingRepository()
) : ViewModel() {
    val status: StateFlow<SystemStatusDto> = repository.status
    val isConnected: StateFlow<Boolean> = repository.isConnected
    val serverUrl: StateFlow<String> = repository.serverUrl
    val serverControlState: StateFlow<String> = repository.serverControlState

    fun refresh() {
        viewModelScope.launch {
            repository.fetchStatus()
        }
    }

    fun startRemoteServer() {
        repository.requestServerStart()
    }

    fun stopRemoteServer() {
        repository.requestServerStop()
    }

    fun triggerKillSwitch() {
        viewModelScope.launch {
            repository.triggerKillSwitch()
        }
    }

    fun toggleSlot(slotId: String, isActive: Boolean) {
        viewModelScope.launch {
            repository.toggleSlot(slotId, isActive)
        }
    }

    fun closePosition(slotId: String, ticker: String) {
        viewModelScope.launch {
            repository.closePosition(slotId, ticker)
        }
    }

    fun updateServerUrl(url: String) {
        repository.serverUrl.value = url
    }
}
