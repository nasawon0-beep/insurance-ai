package com.insuranceai.android.ui

import org.junit.Assert.*
import org.junit.Test

class CustomerContactPolicyTest {
    @Test fun normalizesOnlyDestinationAndPreservesDisplay() {
        val raw = "010-0000-0101"
        val target = customerContactTarget(raw)!!
        assertEquals(raw, target.original)
        assertEquals("01000000101", target.destination)
        assertEquals("+821000000101", customerContactTarget("+82 (10) 0000-0101")!!.destination)
    }
    @Test fun rejectsEmptyMalformedAndInjectedDestinations() {
        for (raw in listOf(null, "", " ", "123", "010#12345678", "*1234567", "01000000101?body=x", "tel:01000000101", "010\n00000101", "++821000000101", "1".repeat(16))) {
            assertNull(raw, customerContactTarget(raw))
        }
    }
    @Test fun onlyUserMediatedDialAndComposeActionsAreAllowed() {
        assertEquals("android.intent.action.DIAL", ContactAction.DIAL.androidAction)
        assertEquals("tel", ContactAction.DIAL.scheme)
        assertEquals("android.intent.action.SENDTO", ContactAction.MESSAGE.androidAction)
        assertEquals("smsto", ContactAction.MESSAGE.scheme)
        assertEquals(2, ContactAction.entries.size)
    }
}
