package com.insuranceai.android.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import com.insuranceai.android.sync.RecoveryUiState
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class RecoveryConnectionUiInstrumentedTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun recoveryCardExplainsPreservationMasksCodeAndEnablesExplicitAction() {
        var submitted = false
        composeRule.setContent {
            var code by remember { mutableStateOf("") }
            MaterialTheme {
                RecoveryConnectionCard(
                    code = code,
                    onCodeChange = { code = it },
                    state = RecoveryUiState(),
                    onRecover = { submitted = true },
                )
            }
        }

        composeRule.onNodeWithText("기존 미전송 자료는 삭제되지 않습니다.", substring = true).assertExists()
        composeRule.onNodeWithText("복구 코드").performTextInput("123456")
        composeRule.onAllNodesWithText("123456").assertCountEquals(0)
        composeRule.onAllNodes(hasText("기존 연결 복구") and hasClickAction())[0]
            .assertIsEnabled()
            .performClick()
        assertTrue(submitted)
    }

    @Test
    fun stagedRecoveryCanBeExplicitlyResumedWithoutDisplayingToken() {
        var submitted = false
        composeRule.setContent {
            MaterialTheme {
                RecoveryConnectionCard(
                    code = "",
                    onCodeChange = {},
                    state = RecoveryUiState(),
                    stagedRecoveryAvailable = true,
                    onRecover = { submitted = true },
                )
            }
        }

        composeRule.onNodeWithText("이전 복구 요청의 PC 승인을 확인할 수 있습니다.", substring = true).assertExists()
        composeRule.onNodeWithText("복구 확인 다시 시도")
            .assertIsEnabled()
            .performClick()
        assertTrue(submitted)
    }
}
