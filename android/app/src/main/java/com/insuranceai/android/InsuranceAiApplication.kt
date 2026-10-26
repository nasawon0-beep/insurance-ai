package com.insuranceai.android

import android.app.Application
import com.insuranceai.android.data.ConsultationRepository
import com.insuranceai.android.data.CoverageAnalysisRepository
import com.insuranceai.android.data.CustomerRepository
import com.insuranceai.android.data.PolicyRepository
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.security.EncryptionService
import com.insuranceai.android.security.AuthenticationGate
import com.insuranceai.android.security.KeyStoreManager
import com.insuranceai.android.sync.SyncRepository
import com.insuranceai.android.sync.SyncSettings
import com.insuranceai.android.sync.SyncWorker
import com.insuranceai.android.sync.AndroidSyncEnqueuer
import com.insuranceai.android.sync.PostCommitSyncDispatcher
import com.insuranceai.android.sync.SyncResumeCoordinator
import com.insuranceai.android.sync.AndroidNsdPcDiscovery
import com.insuranceai.android.sync.PcAutoReconnectCoordinator
import com.insuranceai.android.sync.ForegroundSyncLifecycleCoordinator

class InsuranceAiApplication : Application() {
    val container: AppContainer by lazy { AppContainer(this) }

    override fun onCreate() {
        super.onCreate()
        SyncWorker.schedule(this)
    }
}

class AppContainer(application: Application) {
    private val keyStoreManager = KeyStoreManager(application)
    private val syncEnqueuer = AndroidSyncEnqueuer(application)
    private val postCommitSync = PostCommitSyncDispatcher(syncEnqueuer)
    val syncResumeCoordinator = SyncResumeCoordinator(syncEnqueuer)
    val authenticationGate = AuthenticationGate()
    val encryptionService = EncryptionService(keyStoreManager, authenticationGate)
    val database: AppDatabase by lazy {
        AppDatabase.create(application, keyStoreManager.getOrCreateDbPassphrase())
    }
    val customerRepository: CustomerRepository by lazy {
        CustomerRepository(database, encryptionService, postCommitSync)
    }
    val policyRepository: PolicyRepository by lazy {
        PolicyRepository(database, encryptionService)
    }
    val consultationRepository: ConsultationRepository by lazy {
        ConsultationRepository(database, encryptionService, postCommitSync)
    }
    val coverageAnalysisRepository: CoverageAnalysisRepository by lazy {
        CoverageAnalysisRepository(database, encryptionService)
    }
    val syncSettings: SyncSettings by lazy {
        SyncSettings(application, encryptionService)
    }
    val syncRepository: SyncRepository by lazy {
        SyncRepository(database, encryptionService, syncSettings)
    }
    val pcAutoReconnectCoordinator: PcAutoReconnectCoordinator by lazy {
        PcAutoReconnectCoordinator(syncRepository, AndroidNsdPcDiscovery(application))
    }
    val foregroundSyncLifecycleCoordinator: ForegroundSyncLifecycleCoordinator by lazy {
        ForegroundSyncLifecycleCoordinator(
            reconnect = { pcAutoReconnectCoordinator.reconnectWithResult() },
            resumeSync = syncResumeCoordinator::resumeAfterAuthentication,
        )
    }
}
