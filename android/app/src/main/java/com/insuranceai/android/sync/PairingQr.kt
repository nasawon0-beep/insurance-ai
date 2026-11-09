package com.insuranceai.android.sync

import org.json.JSONObject

/** PC 화면 QR에서 읽은 최초 등록 정보. 코드는 메모리에만 두고 상태 문구·로그에 넣지 않는다. */
data class PairingQrInvite(
    val endpoint: String,
    val certificatePin: String,
    val code: String,
    val expiresAtEpochSeconds: Long,
    val pcInstanceId: String,
) {
    override fun toString(): String = "PairingQrInvite(endpoint=$endpoint, code=******)"
}

sealed interface PairingQrParseResult {
    data class Valid(val invite: PairingQrInvite) : PairingQrParseResult
    data class Rejected(val reason: String) : PairingQrParseResult
}

/**
 * PC(desktop/src-tauri/src/mobile_sync.rs pairing_qr_payload)와 같은 v1 형식만 받는다.
 * {"t":"moadoom-pair","v":1,"u":"https://<private IPv4>:<port>","f":"<sha256 hex>","c":"<6자리>","e":<만료 epoch초>,"i":"<32hex>"}
 * URL·다른 앱 QR·외부 주소·HTTP·과대 입력은 거부하고, 내용을 열거나 실행하지 않는다.
 */
object PairingQr {
    const val KIND = "moadoom-pair"
    const val VERSION = 1
    const val MAX_LENGTH = 512
    /** PC와 폰 시계 차이 허용(서버가 실제 만료를 다시 검사한다). */
    const val CLOCK_SKEW_SECONDS = 60L
    /** PC 코드 수명(5분)보다 훨씬 먼 만료는 위조/시계 오류로 보고 거부한다. */
    const val MAX_FUTURE_SECONDS = 15 * 60L

    private val keys = setOf("t", "v", "u", "f", "c", "e", "i")
    private val endpointPattern = Regex("^https://(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3}):(\\d{1,5})$")
    private val pinPattern = Regex("^[0-9a-f]{64}$")
    private val codePattern = Regex("^\\d{6}$")
    private val instancePattern = Regex("^[0-9a-f]{32}$")

    const val NOT_MOADOOM = "모아둠 PC 연결 QR이 아닙니다. PC 설정 화면의 ‘QR로 폰 연결’ QR을 찍어 주세요."

    fun parse(raw: String?, nowEpochSeconds: Long): PairingQrParseResult {
        if (raw.isNullOrBlank()) return rejected("QR 내용이 비어 있습니다.")
        if (raw.length > MAX_LENGTH) return rejected("QR 내용이 너무 길어 읽지 않았습니다.")
        val text = raw.trim()
        if (!text.startsWith("{") || !text.endsWith("}")) return rejected(NOT_MOADOOM)
        val json = try {
            JSONObject(text)
        } catch (_: Exception) {
            return rejected(NOT_MOADOOM)
        }
        val names = json.keys().asSequence().toSet()
        if (json.opt("t") != KIND) return rejected(NOT_MOADOOM)
        val version = json.opt("v")
        if (version !is Int) return rejected(NOT_MOADOOM)
        if (version > VERSION) return rejected("새 버전 PC의 QR입니다. 모아둠 앱을 업데이트해 주세요.")
        if (version != VERSION || names != keys) return rejected(NOT_MOADOOM)

        val endpoint = json.opt("u") as? String ?: return rejected(NOT_MOADOOM)
        if (!isPrivateHttpsEndpoint(endpoint)) {
            return rejected("같은 Wi-Fi/핫스팟의 PC HTTPS 주소가 아니어서 연결하지 않았습니다.")
        }
        val pin = json.opt("f") as? String
        if (pin == null || !pinPattern.matches(pin)) return rejected("QR의 인증서 지문 형식이 올바르지 않습니다.")
        val code = json.opt("c") as? String
        if (code == null || !codePattern.matches(code)) return rejected("QR의 일회용 코드 형식이 올바르지 않습니다.")
        val instance = json.opt("i") as? String
        if (instance == null || !instancePattern.matches(instance)) return rejected(NOT_MOADOOM)
        val expiresRaw = json.opt("e")
        val expires = when (expiresRaw) {
            is Int -> expiresRaw.toLong()
            is Long -> expiresRaw
            else -> return rejected(NOT_MOADOOM)
        }
        if (nowEpochSeconds > expires + CLOCK_SKEW_SECONDS) {
            return rejected("QR이 만료되었습니다. PC에서 ‘QR로 폰 연결’을 다시 눌러 새 QR을 찍어 주세요.")
        }
        if (expires - nowEpochSeconds > MAX_FUTURE_SECONDS) {
            return rejected("QR 시간 정보가 올바르지 않습니다. PC와 폰의 날짜·시간을 확인해 주세요.")
        }
        return PairingQrParseResult.Valid(PairingQrInvite(endpoint, pin, code, expires, instance))
    }

    fun isExpired(invite: PairingQrInvite, nowEpochSeconds: Long): Boolean =
        nowEpochSeconds > invite.expiresAtEpochSeconds + CLOCK_SKEW_SECONDS

    /** 10/8, 172.16/12, 192.168/16 IPv4 + HTTPS + 명시 포트만. 호스트명·userinfo·경로·쿼리 불가. */
    fun isPrivateHttpsEndpoint(endpoint: String): Boolean {
        val match = endpointPattern.matchEntire(endpoint) ?: return false
        val parts = match.groupValues.drop(1)
        val octetText = parts.take(4)
        if (octetText.any { it.length > 1 && it.startsWith("0") }) return false
        val octets = octetText.map { it.toInt() }
        if (octets.any { it > 255 }) return false
        val portText = parts[4]
        if (portText.startsWith("0")) return false
        val port = portText.toInt()
        if (port !in 1..65535) return false
        val (a, b) = octets[0] to octets[1]
        return a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168)
    }

    private fun rejected(reason: String) = PairingQrParseResult.Rejected(reason)
}

/** 스캔 화면에서 돌아온 결과를 화면 동작으로 바꾼다(카메라·Activity 없이 단위 테스트). */
sealed interface QrScanOutcome {
    data class Confirm(val invite: PairingQrInvite) : QrScanOutcome
    data class Message(val text: String) : QrScanOutcome
}

object QrScanMessages {
    const val CANCELLED = "QR 스캔을 취소했습니다. 다시 스캔하거나 ‘직접 입력’을 사용하세요."
    const val CAMERA_DENIED =
        "카메라 권한이 없어 QR을 읽을 수 없습니다. 휴대폰 설정 > 애플리케이션 > 모아둠 > 권한에서 카메라를 허용하거나, 아래 ‘직접 입력’으로 주소·지문·코드를 입력하세요."
}

fun qrScanOutcome(contents: String?, nowEpochSeconds: Long): QrScanOutcome {
    if (contents == null) return QrScanOutcome.Message(QrScanMessages.CANCELLED)
    return when (val parsed = PairingQr.parse(contents, nowEpochSeconds)) {
        is PairingQrParseResult.Valid -> QrScanOutcome.Confirm(parsed.invite)
        is PairingQrParseResult.Rejected -> QrScanOutcome.Message(parsed.reason)
    }
}

/** 확인 화면에 보여줄 지문 일부(코드는 보여주지 않는다). */
fun shortFingerprint(pin: String): String = pin.take(16).chunked(4).joinToString(" ")
