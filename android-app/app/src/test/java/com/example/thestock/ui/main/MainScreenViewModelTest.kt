package com.example.thestock.ui.main

import com.example.thestock.data.TradingRepository
import junit.framework.TestCase.assertEquals
import junit.framework.TestCase.assertNotNull
import kotlinx.coroutines.test.runTest
import org.junit.Test

class MainScreenViewModelTest {
    @Test
    fun viewModel_initialStatusNotNull() = runTest {
        val viewModel = MainScreenViewModel()
        assertNotNull(viewModel.status.value)
        assertEquals(40_000_000.0, viewModel.status.value.portfolio.initial_capital)
        assertEquals(4, viewModel.status.value.slots.size)
    }

    @Test
    fun viewModel_toggleSlotUpdatesState() = runTest {
        val viewModel = MainScreenViewModel()
        viewModel.toggleSlot("SLOT_A", false)
        // 오프라인 상태에서도 로컬 상태 갱신
        assertNotNull(viewModel.status.value.slots["SLOT_A"])
    }
}
