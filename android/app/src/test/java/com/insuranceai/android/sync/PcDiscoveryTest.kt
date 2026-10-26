package com.insuranceai.android.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.coroutines.test.runTest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.MessageDigest
import java.security.cert.X509Certificate

class PcDiscoveryTest {
    private val instance = "0123456789abcdef0123456789abcdef"

    @Test
    fun knownInstanceMismatchIsRejectedBeforeCredentialUse() {
        val candidate = DiscoveredPc("https://192.168.50.12:18443", "fedcba9876543210fedcba9876543210")
        assertFalse(mayProbeDiscoveredCandidate(instance, candidate))
    }

    @Test
    fun legacyRegistrationMayProbeButMustBindAdvertisedAndAuthenticatedInstance() {
        val candidate = DiscoveredPc("https://192.168.50.12:18443", instance)
        assertTrue(mayProbeDiscoveredCandidate(null, candidate))
        validateDiscoveredContinuity(
            expectedPcInstanceId = null,
            advertisedPcInstanceId = instance,
            authenticatedPcInstanceId = instance,
        )
    }

    @Test
    fun spoofedOrMalformedInstanceFailsClosed() {
        val candidate = DiscoveredPc("https://192.168.50.12:18443", "not-an-instance")
        assertFalse(mayProbeDiscoveredCandidate(null, candidate))
        assertThrows(IllegalArgumentException::class.java) {
            validateDiscoveredContinuity(
                expectedPcInstanceId = instance,
                advertisedPcInstanceId = instance,
                authenticatedPcInstanceId = "fedcba9876543210fedcba9876543210",
            )
        }
    }

    @Test
    fun coordinatorSkipsWrongInstanceAndUsesAuthenticatedMatchingCandidate() = runTest {
        val wrong = DiscoveredPc("https://192.168.50.11:18443", "fedcba9876543210fedcba9876543210")
        val matching = DiscoveredPc("https://192.168.50.12:18443", instance)
        val actions = FakeActions(instance)
        val coordinator = PcAutoReconnectCoordinator(actions, FakeDiscovery(listOf(wrong, matching)))

        assertEquals(AutoReconnectResult.CONNECTED, coordinator.reconnectWithResult())
        assertTrue(actions.probed == listOf(matching))
    }

    @Test
    fun failedAuthenticatedProbeDoesNotUpdateAndMayTryAnotherSameIdentityAddress() = runTest {
        val first = DiscoveredPc("https://192.168.50.11:18443", instance)
        val second = DiscoveredPc("https://192.168.50.12:18443", instance)
        val actions = FakeActions(instance, failFirst = true)

        assertEquals(
            AutoReconnectResult.CONNECTED,
            PcAutoReconnectCoordinator(actions, FakeDiscovery(listOf(first, second))).reconnectWithResult(),
        )
        assertTrue(actions.probed == listOf(first, second))
    }

    @Test
    fun spoofedCertificateCandidateIsSkippedBeforeTryingValidSameIdentityAddress() = runTest {
        val first = DiscoveredPc("https://192.168.50.11:18443", instance)
        val second = DiscoveredPc("https://192.168.50.12:18443", instance)
        val actions = FakeActions(
            instance,
            firstFailure = CertificateException("Server certificate identity mismatch"),
        )

        assertEquals(
            AutoReconnectResult.CONNECTED,
            PcAutoReconnectCoordinator(actions, FakeDiscovery(listOf(first, second))).reconnectWithResult(),
        )
        assertEquals(listOf(first, second), actions.probed)
    }

    @Test
    fun leafOrPreviouslyBoundSpkiCanAuthenticateButUnknownIdentityCannot() {
        val oldLeaf = "a".repeat(64)
        val stableSpki = "b".repeat(64)
        assertTrue(matchesPinnedServerIdentity(oldLeaf, stableSpki, oldLeaf, null))
        assertTrue(matchesPinnedServerIdentity("c".repeat(64), stableSpki, oldLeaf, stableSpki))
        assertFalse(matchesPinnedServerIdentity("c".repeat(64), "d".repeat(64), oldLeaf, stableSpki))
        assertFalse(matchesPinnedServerIdentity("c".repeat(64), stableSpki, oldLeaf, null))
    }

    @Test
    fun selfSignedServerCertificateRequiresLeafOrStableSpkiAndValidSignature() {
        val certificate = javaClass.getResourceAsStream("/tls/synthetic-self-signed.txt").use {
            CertificateFactory.getInstance("X.509").generateCertificate(it) as X509Certificate
        }
        fun digest(bytes: ByteArray) = MessageDigest.getInstance("SHA-256")
            .digest(bytes).joinToString("") { "%02x".format(it) }
        val leaf = digest(certificate.encoded)
        val spki = digest(certificate.publicKey.encoded)

        validatePinnedServerCertificate(certificate, leaf, null)
        validatePinnedServerCertificate(certificate, "c".repeat(64), spki)
        assertThrows(CertificateException::class.java) {
            validatePinnedServerCertificate(certificate, "c".repeat(64), "d".repeat(64))
        }
    }

    @Test
    fun authenticatedServerIdentityMustMatchPeerAndAnyStoredBinding() {
        val identity = "b".repeat(64)
        assertEquals(identity, validatePeerTlsIdentity(identity, identity, null))
        assertEquals(identity, validatePeerTlsIdentity(identity, identity, identity))
        assertThrows(SecurityException::class.java) {
            validatePeerTlsIdentity("c".repeat(64), identity, identity)
        }
        assertThrows(SecurityException::class.java) {
            validatePeerTlsIdentity(identity, identity, "d".repeat(64))
        }
    }

    @Test
    fun rediscoveryCandidateProbesAreBounded() = runTest {
        val candidates = (1..20).map {
            DiscoveredPc("https://192.168.50.$it:18443", instance)
        }
        val actions = FakeActions(instance, alwaysFail = true)

        assertEquals(
            AutoReconnectResult.DISCOVERY_FAILED,
            PcAutoReconnectCoordinator(actions, FakeDiscovery(candidates)).reconnectWithResult(),
        )
        assertEquals(8, actions.probed.size)
        assertEquals(1, actions.existingEndpointProbes)
    }

    @Test
    fun emptyDiscoveryUsesOnlyAuthenticatedPinnedExistingEndpointFallback() = runTest {
        val actions = FakeActions(instance, existingEndpointVerified = true)

        val result = PcAutoReconnectCoordinator(actions, FakeDiscovery(emptyList())).reconnectWithResult()

        assertEquals(AutoReconnectResult.VERIFIED_EXISTING_ENDPOINT, result)
        assertEquals(1, actions.existingEndpointProbes)
    }

    @Test
    fun emptyDiscoveryWithoutVerifiedFallbackIsExplicitFailure() = runTest {
        val actions = FakeActions(instance)

        val result = PcAutoReconnectCoordinator(actions, FakeDiscovery(emptyList())).reconnectWithResult()

        assertEquals(AutoReconnectResult.DISCOVERY_FAILED, result)
        assertEquals(1, actions.existingEndpointProbes)
    }

    @Test
    fun revokedExistingEndpointFailsClosedWithoutSyncEligibility() = runTest {
        val actions = FakeActions(instance, existingEndpointFailure = LanSyncHttpException(401))

        val result = PcAutoReconnectCoordinator(actions, FakeDiscovery(emptyList())).reconnectWithResult()

        assertEquals(AutoReconnectResult.AUTHENTICATION_FAILED, result)
    }

    @Test
    fun pinAndInstanceMismatchFallbacksFailClosed() = runTest {
        for (failure in listOf(
            CertificateException("Server certificate pin mismatch"),
            IllegalArgumentException("Registered PC identity changed"),
        )) {
            val actions = FakeActions(instance, existingEndpointFailure = failure)

            val result = PcAutoReconnectCoordinator(actions, FakeDiscovery(emptyList())).reconnectWithResult()

            assertEquals(AutoReconnectResult.AUTHENTICATION_FAILED, result)
        }
    }

    private class FakeDiscovery(private val values: List<DiscoveredPc>) : PcDiscovery {
        override suspend fun discover(): List<DiscoveredPc> = values
    }

    private class FakeActions(
        private val registered: String?,
        private val failFirst: Boolean = false,
        private val firstFailure: Throwable? = null,
        private val alwaysFail: Boolean = false,
        private val existingEndpointVerified: Boolean = false,
        private val existingEndpointFailure: Throwable? = null,
    ) : DiscoveredAddressActions {
        val probed = mutableListOf<DiscoveredPc>()
        var existingEndpointProbes = 0
        override fun registeredPcInstanceId(): String? = registered
        override suspend fun verifyAndUpdateDiscoveredAddress(candidate: DiscoveredPc) {
            probed += candidate
            if (probed.size == 1) firstFailure?.let { throw it }
            if (failFirst && probed.size == 1) error("pin or authentication failure")
            if (alwaysFail) error("synthetic unavailable candidate")
        }
        override suspend fun verifyCurrentAddress(): Boolean {
            existingEndpointProbes++
            existingEndpointFailure?.let { throw it }
            return existingEndpointVerified
        }
    }
}
