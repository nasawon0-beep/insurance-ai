package com.insuranceai.android.sync

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import java.util.concurrent.atomic.AtomicBoolean
import java.security.cert.CertificateException
import kotlin.coroutines.resume

private const val SERVICE_TYPE = "_moadoom-sync._tcp."
private val PC_INSTANCE_PATTERN = Regex("[0-9a-f]{32}")

data class DiscoveredPc(
    val endpoint: String,
    val instanceId: String,
)

enum class AutoReconnectResult {
    CONNECTED,
    VERIFIED_EXISTING_ENDPOINT,
    DISCOVERY_FAILED,
    AUTHENTICATION_FAILED,
    COALESCED,
}

fun mayProbeDiscoveredCandidate(expectedPcInstanceId: String?, candidate: DiscoveredPc): Boolean {
    if (!candidate.endpoint.startsWith("https://")) return false
    if (!PC_INSTANCE_PATTERN.matches(candidate.instanceId)) return false
    return expectedPcInstanceId == null || expectedPcInstanceId == candidate.instanceId
}

fun validateDiscoveredContinuity(
    expectedPcInstanceId: String?,
    advertisedPcInstanceId: String,
    authenticatedPcInstanceId: String,
) {
    require(PC_INSTANCE_PATTERN.matches(advertisedPcInstanceId)) { "Invalid advertised PC identity" }
    require(PC_INSTANCE_PATTERN.matches(authenticatedPcInstanceId)) { "Invalid authenticated PC identity" }
    require(advertisedPcInstanceId == authenticatedPcInstanceId) { "Advertised PC identity changed after authentication" }
    if (expectedPcInstanceId != null) {
        require(expectedPcInstanceId == authenticatedPcInstanceId) { "Registered PC identity changed" }
    }
}

interface PcDiscovery {
    suspend fun discover(): List<DiscoveredPc>
}

interface DiscoveredAddressActions {
    fun registeredPcInstanceId(): String?
    suspend fun verifyAndUpdateDiscoveredAddress(candidate: DiscoveredPc)
    suspend fun verifyCurrentAddress(): Boolean = false
}

class PcAutoReconnectCoordinator(
    private val actions: DiscoveredAddressActions,
    private val discovery: PcDiscovery,
) {
    private val mutex = Mutex()

    suspend fun reconnectWithResult(): AutoReconnectResult {
        if (!mutex.tryLock()) return AutoReconnectResult.COALESCED
        try {
            val expected = actions.registeredPcInstanceId()
            val candidates = runCatching { discovery.discover().distinct() }.getOrDefault(emptyList())
            for (candidate in candidates.take(8)) {
                if (!mayProbeDiscoveredCandidate(expected, candidate)) continue
                val verified = runCatching { actions.verifyAndUpdateDiscoveredAddress(candidate) }
                if (verified.isSuccess) return AutoReconnectResult.CONNECTED
                if (verified.exceptionOrNull()?.isCertificateReconnectRejection() == true) continue
                if (verified.exceptionOrNull()?.isFailClosedReconnectFailure() == true) {
                    return AutoReconnectResult.AUTHENTICATION_FAILED
                }
            }
            val fallback = runCatching { actions.verifyCurrentAddress() }
            if (fallback.getOrDefault(false)) return AutoReconnectResult.VERIFIED_EXISTING_ENDPOINT
            return if (fallback.exceptionOrNull()?.isFailClosedReconnectFailure() == true) {
                AutoReconnectResult.AUTHENTICATION_FAILED
            } else {
                AutoReconnectResult.DISCOVERY_FAILED
            }
        } finally {
            mutex.unlock()
        }
    }
}

private fun Throwable.isCertificateReconnectRejection(): Boolean {
    var current: Throwable? = this
    while (current != null) {
        if (current is CertificateException) return true
        current = current.cause
    }
    return false
}

private fun Throwable.isFailClosedReconnectFailure(): Boolean {
    if (requiresUserAuthentication()) return true
    if (this is LanSyncHttpException && statusCode == 401) return true
    if (this is IllegalArgumentException || this is SecurityException) return true
    var current: Throwable? = this
    while (current != null) {
        if (current is CertificateException) return true
        current = current.cause
    }
    return false
}

@Suppress("DEPRECATION")
class AndroidNsdPcDiscovery(context: Context) : PcDiscovery {
    private val manager = context.applicationContext.getSystemService(Context.NSD_SERVICE) as NsdManager

    override suspend fun discover(): List<DiscoveredPc> = suspendCancellableCoroutine { continuation ->
        val completed = AtomicBoolean(false)
        val candidates = mutableListOf<DiscoveredPc>()
        val handler = Handler(Looper.getMainLooper())
        lateinit var listener: NsdManager.DiscoveryListener

        fun finish() {
            if (!completed.compareAndSet(false, true)) return
            runCatching { manager.stopServiceDiscovery(listener) }
            val snapshot = synchronized(candidates) { candidates.toList() }
            if (continuation.isActive) continuation.resume(snapshot)
        }

        listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) = Unit
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) = finish()
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) = finish()
            override fun onDiscoveryStopped(serviceType: String) = Unit
            override fun onServiceLost(serviceInfo: NsdServiceInfo) = Unit

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (serviceInfo.serviceType != SERVICE_TYPE) return
                manager.resolveService(serviceInfo, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) = Unit
                    override fun onServiceResolved(resolved: NsdServiceInfo) {
                        val address = resolved.host?.hostAddress ?: return
                        val instance = resolved.attributes["instance"]?.toString(Charsets.UTF_8) ?: return
                        if (resolved.port !in 1..65535) return
                        synchronized(candidates) {
                            candidates += DiscoveredPc("https://$address:${resolved.port}", instance)
                        }
                    }
                })
            }
        }
        continuation.invokeOnCancellation { finish() }
        handler.postDelayed(::finish, 2_500)
        runCatching { manager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener) }
            .onFailure { finish() }
    }
}
