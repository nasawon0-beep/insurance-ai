package com.insuranceai.android.sync

import org.junit.Assert.assertFalse
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.coroutines.test.runTest
import java.security.cert.CertificateException

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
        private val existingEndpointVerified: Boolean = false,
        private val existingEndpointFailure: Throwable? = null,
    ) : DiscoveredAddressActions {
        val probed = mutableListOf<DiscoveredPc>()
        var existingEndpointProbes = 0
        override fun registeredPcInstanceId(): String? = registered
        override suspend fun verifyAndUpdateDiscoveredAddress(candidate: DiscoveredPc) {
            probed += candidate
            if (failFirst && probed.size == 1) error("pin or authentication failure")
        }
        override suspend fun verifyCurrentAddress(): Boolean {
            existingEndpointProbes++
            existingEndpointFailure?.let { throw it }
            return existingEndpointVerified
        }
    }
}
