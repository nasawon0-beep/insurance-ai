package com.insuranceai.android.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** No real phone, dialer, SMS app or outgoing transmission is used. */
@RunWith(AndroidJUnit4::class)
class CustomerContactIntentInstrumentedTest {
    @Test fun onlyOpensDialerOrSmsComposerUsingSyntheticNumber() {
        for (action in ContactAction.entries) {
            val captured = mutableListOf<Intent>()
            assertNull(openCustomerContact("010-0000-0101", action) { captured.add(it) })
            assertEquals(1, captured.size)
            assertEquals(action.androidAction, captured.single().action)
            assertEquals(action.scheme, captured.single().data!!.scheme)
            assertEquals("01000000101", captured.single().data!!.schemeSpecificPart)
            assertNull(captured.single().extras)
            assertNotEquals(Intent.ACTION_CALL, captured.single().action)
        }
    }
    @Test fun malformedOrMissingHandlerNeverCrashesOrDispatches() {
        var calls = 0
        assertNotNull(openCustomerContact("010#00000101", ContactAction.DIAL) { calls++ })
        assertEquals(0, calls)
        assertNotNull(openCustomerContact("010-0000-0101", ContactAction.DIAL) { throw ActivityNotFoundException() })
        assertNotNull(openCustomerContact("010-0000-0101", ContactAction.MESSAGE) { throw SecurityException() })
    }
}
