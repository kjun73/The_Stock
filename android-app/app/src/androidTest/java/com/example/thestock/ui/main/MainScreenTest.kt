package com.example.thestock.ui.main

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** UI tests for [com.example.thestock.ui.main.MainScreen]. */
class MainScreenTest {

    @get:Rule
    val composeTestRule = createAndroidComposeRule<ComponentActivity>()

    @Before
    fun setup() {
        composeTestRule.setContent {
            MainScreen()
        }
    }

    @Test
    fun dashboard_title_exists() {
        composeTestRule.onNodeWithText("THE STOCK C2").assertExists()
    }
}
