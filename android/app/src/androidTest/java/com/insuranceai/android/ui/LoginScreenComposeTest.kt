package com.insuranceai.android.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import org.junit.Rule
import org.junit.Test

class LoginScreenComposeTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun loginScreenShowsBiometricAction() {
        composeRule.setContent {
            LoginScreen(biometricAvailable = true, onAuthenticate = {})
        }

        composeRule.onNodeWithText("Insurance AI").assertIsDisplayed()
        composeRule.onNodeWithText("생체 인증으로 시작").assertIsDisplayed()
    }
}
