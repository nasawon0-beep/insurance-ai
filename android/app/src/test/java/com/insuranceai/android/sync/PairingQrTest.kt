package com.insuranceai.android.sync

import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.MultiFormatReader
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class PairingQrTest {
    private val fp = "7502f13db142ce04e5caf9ebf36feb299ff1a25fc3796240979c48cef991b432"
    private val instance = "0123456789abcdef0123456789abcdef"
    private val expires = 1_794_200_000L
    private val now = expires - 200

    private fun payload(mutate: JSONObject.() -> Unit = {}): String = JSONObject()
        .put("t", "moadoom-pair").put("v", 1).put("u", "https://192.168.0.73:18443")
        .put("f", fp).put("c", "042917").put("e", expires).put("i", instance)
        .apply(mutate).toString()

    private fun rejected(raw: String?, at: Long = now): String {
        val result = PairingQr.parse(raw, at)
        if (result !is PairingQrParseResult.Rejected) fail("expected rejection for $raw")
        return (result as PairingQrParseResult.Rejected).reason
    }

    @Test
    fun validPayloadBecomesInviteWithoutExposingCode() {
        val result = PairingQr.parse(payload(), now) as PairingQrParseResult.Valid
        assertEquals("https://192.168.0.73:18443", result.invite.endpoint)
        assertEquals(fp, result.invite.certificatePin)
        assertEquals("042917", result.invite.code)
        assertFalse(result.invite.toString().contains("042917"))
        assertEquals("7502 f13d b142 ce04", shortFingerprint(fp))
    }

    /** Rust(qrcode crate)가 만든 실제 QR 모듈 이미지를 앱이 쓰는 ZXing으로 디코딩 → 파서까지 왕복. */
    @Test
    fun rustGeneratedQrImageDecodesWithZxingAndParses() {
        val pgm = javaClass.classLoader!!.getResourceAsStream("qr/pairing-qr.pgm")!!.readBytes()
        val expected = javaClass.classLoader!!.getResourceAsStream("qr/pairing-qr.payload.json")!!.readBytes().toString(Charsets.UTF_8)
        var offset = 0
        val fields = mutableListOf<String>()
        while (fields.size < 4) {
            val start = offset
            while (!pgm[offset].toInt().toChar().isWhitespace()) offset++
            fields += String(pgm, start, offset - start, Charsets.US_ASCII)
            offset++
        }
        assertEquals("P5", fields[0])
        val width = fields[1].toInt()
        val height = fields[2].toInt()
        val pixels = IntArray(width * height) { index ->
            val gray = pgm[offset + index].toInt() and 0xff
            (0xff shl 24) or (gray shl 16) or (gray shl 8) or gray
        }
        val bitmap = BinaryBitmap(HybridBinarizer(RGBLuminanceSource(width, height, pixels)))
        val decoded = MultiFormatReader().decode(
            bitmap,
            mapOf(DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE)),
        ).text
        assertEquals(expected, decoded)
        val parsed = PairingQr.parse(decoded, expires - 100) as PairingQrParseResult.Valid
        assertEquals("https://192.168.0.73:18443", parsed.invite.endpoint)
        assertEquals("042917", parsed.invite.code)
    }

    @Test
    fun expiredAndFarFutureQrAreRejected() {
        assertTrue(rejected(payload(), expires + PairingQr.CLOCK_SKEW_SECONDS + 1).contains("만료"))
        assertTrue(PairingQr.parse(payload(), expires + PairingQr.CLOCK_SKEW_SECONDS) is PairingQrParseResult.Valid)
        assertTrue(rejected(payload(), expires - PairingQr.MAX_FUTURE_SECONDS - 1).contains("시간"))
        val invite = (PairingQr.parse(payload(), now) as PairingQrParseResult.Valid).invite
        assertTrue(PairingQr.isExpired(invite, expires + 61))
        assertFalse(PairingQr.isExpired(invite, expires))
    }

    @Test
    fun tamperedOrForeignPayloadsAreRejected() {
        rejected(payload { put("u", "http://192.168.0.73:18443") })
        rejected(payload { put("u", "https://8.8.8.8:18443") })
        rejected(payload { put("u", "https://127.0.0.1:18443") })
        rejected(payload { put("u", "https://evil.example.com:18443") })
        rejected(payload { put("u", "https://user@192.168.0.73:18443") })
        rejected(payload { put("u", "https://192.168.0.73:18443/v1/pair") })
        rejected(payload { put("u", "https://192.168.0.73") })
        rejected(payload { put("u", "https://192.168.000.73:18443") })
        rejected(payload { put("u", "https://192.168.0.256:18443") })
        rejected(payload { put("u", "https://192.168.0.73:70000") })
        rejected(payload { put("u", "https://172.32.0.1:18443") })
        rejected(payload { put("f", fp.uppercase()) })
        rejected(payload { put("f", fp.dropLast(1)) })
        rejected(payload { put("c", "12345") })
        rejected(payload { put("c", "12345a") })
        rejected(payload { put("c", 42917) })
        rejected(payload { put("e", "1794200000") })
        rejected(payload { put("e", 1.7942e9) })
        rejected(payload { put("i", "short") })
        rejected(payload { put("t", "other-app") })
        rejected(payload { put("v", "1") })
        rejected(payload { put("v", 0) })
        assertTrue(rejected(payload { put("v", 2) }).contains("업데이트"))
        rejected(payload { put("token", "long-lived") })
        rejected(payload { remove("i") })
        rejected("{\"t\":\"moadoom-pair\",\"t\":\"moadoom-pair\"}")
        rejected("https://192.168.0.73:18443")
        rejected("not json")
        rejected("")
        rejected(null)
        assertTrue(rejected("{" + " ".repeat(PairingQr.MAX_LENGTH) + "}").contains("너무 길어"))
        assertTrue(PairingQr.parse(payload { put("u", "https://172.20.10.2:18443") }, now) is PairingQrParseResult.Valid)
        assertTrue(PairingQr.parse(payload { put("u", "https://10.0.0.5:18443") }, now) is PairingQrParseResult.Valid)
    }

    @Test
    fun scanCancelAndPermissionDeniedGiveGuidanceWithoutPairing() {
        val cancelled = qrScanOutcome(null, now)
        assertEquals(QrScanOutcome.Message(QrScanMessages.CANCELLED), cancelled)
        assertTrue(QrScanMessages.CAMERA_DENIED.contains("직접 입력"))
        assertTrue(QrScanMessages.CAMERA_DENIED.contains("카메라"))
        val foreign = qrScanOutcome("https://example.com/promo", now)
        assertEquals(QrScanOutcome.Message(PairingQr.NOT_MOADOOM), foreign)
        assertTrue(qrScanOutcome(payload(), now) is QrScanOutcome.Confirm)
    }
}
