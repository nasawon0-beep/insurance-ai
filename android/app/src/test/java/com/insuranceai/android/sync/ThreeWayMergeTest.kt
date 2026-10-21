package com.insuranceai.android.sync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ThreeWayMergeTest {
    private val base = mapOf("phone" to "010-0000", "address" to "기준", "memo" to "기준 메모")

    @Test
    fun nonOverlappingChangesMerge() {
        val result = ThreeWayMerge.merge(base, mapOf("address" to "PC", "memo" to "기준 메모", "phone" to "010-0000"), mapOf("memo" to "모바일"))
        assertEquals("PC", result.merged["address"])
        assertEquals("모바일", result.merged["memo"])
        assertTrue(result.conflicts.isEmpty())
    }

    @Test
    fun sameFieldConflictPreservesBothValues() {
        val result = ThreeWayMerge.merge(base, base + ("memo" to "PC"), mapOf("memo" to "모바일"))
        assertEquals("PC", result.merged["memo"])
        assertEquals(FieldConflict("memo", "기준 메모", "모바일", "PC"), result.conflicts.single())
    }

    @Test
    fun clockValuesNeverChooseAWinner() {
        val result = ThreeWayMerge.merge(base, base + ("memo" to "PC"), mapOf("memo" to "모바일"), clientTime = "2099-01-01", serverTime = "2000-01-01")
        assertEquals(1, result.conflicts.size)
    }
}
