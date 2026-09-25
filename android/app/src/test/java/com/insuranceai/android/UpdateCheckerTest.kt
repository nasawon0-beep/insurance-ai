package com.insuranceai.android

import org.junit.Assert.assertEquals
import org.junit.Test

class UpdateCheckerTest {
    @Test
    fun compareVersionsTreatsMissingPartsAsZero() {
        assertEquals(0, UpdateChecker.compareVersions("1.2", "1.2.0"))
        assertEquals(1, UpdateChecker.compareVersions("1.2.1", "1.2.0"))
        assertEquals(-1, UpdateChecker.compareVersions("1.1.9", "1.2.0"))
    }

    @Test
    fun compareVersionsIgnoresNonNumericSuffixes() {
        assertEquals(0, UpdateChecker.compareVersions("1.0.0", "1.0.0-phase1"))
        assertEquals(1, UpdateChecker.compareVersions("1.0.1", "1.0.0-phase1"))
    }
}
