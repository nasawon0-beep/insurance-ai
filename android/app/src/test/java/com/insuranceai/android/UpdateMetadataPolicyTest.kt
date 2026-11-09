package com.insuranceai.android

import org.junit.Assert.*
import org.junit.Test

class UpdateMetadataPolicyTest {
    private fun metadata(code: String = "13", url: String = "https://github.com/nasawon0-beep/insurance-ai/releases/download/test/phase13.apk") =
        """{"versionCode":$code,"versionName":"1.0.0-phase13","downloadUrl":"$url","checksum":{"sha256":"${"a".repeat(64)}"}}"""
    @Test fun usesActualSiteAndVersionCode() {
        assertEquals("https://nasawon0-beep.github.io/insurance-ai-updates/api/android.json", UpdateMetadataPolicy.ENDPOINT)
        assertNotNull(UpdateMetadataPolicy.parse(metadata(), 12))
        assertNull(UpdateMetadataPolicy.parse(metadata(), 13))
        assertNull(UpdateMetadataPolicy.parse(metadata(), 14))
    }
    @Test fun rejectsUnsafeUrlsAndMalformedMetadata() {
        for (url in listOf("http://github.com/nasawon0-beep/insurance-ai/releases/download/x/a.apk", "https://evil.test/a.apk", "https://github.com/other/repo/releases/download/x/a.apk", "https://github.com@evil.test/a.apk", "https://github.com/nasawon0-beep/insurance-ai/releases/download/untagged-x/a.apk")) {
            assertNull(UpdateMetadataPolicy.parse(metadata(url=url), 12))
        }
        for (code in listOf("13.5", "\"13\"", "-1", "2147483648")) assertNull(UpdateMetadataPolicy.parse(metadata(code=code), 12))
        assertNull(UpdateMetadataPolicy.parse("{}",12))
        assertNull(UpdateMetadataPolicy.parse("x".repeat(65537),12))
        assertNull(UpdateMetadataPolicy.parse(metadata().replace("a".repeat(64), "bad"),12))
    }
}
