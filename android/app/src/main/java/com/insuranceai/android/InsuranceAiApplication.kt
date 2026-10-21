package com.insuranceai.android

import android.app.Application
import com.insuranceai.android.data.ConsultationRepository
import com.insuranceai.android.data.CoverageAnalysisRepository
import com.insuranceai.android.data.CustomerRepository
import com.insuranceai.android.data.PolicyRepository
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.security.EncryptionService
import com.insuranceai.android.security.KeyStoreManager
import com.insuranceai.android.sync.SyncRepository
import com.insuranceai.android.sync.SyncSettings
import com.insuranceai.android.sync.SyncWorker

class InsuranceAiApplication : Application() {
    val container: AppContainer by lazy { AppContainer(this) }

    override fun onCreate() {
        super.onCreate()
        SyncWorker.schedule(this)
    }
}

class AppContainer(application: Application) {
    private val keyStoreManager = KeyStoreManager(application)
    val encryptionService = EncryptionService(keyStoreManager)
    val database: AppDatabase by lazy {
        AppDatabase.create(application, keyStoreManager.getOrCreateDbPassphrase())
    }
    val customerRepository: CustomerRepository by lazy {
        CustomerRepository(database, encryptionService)
    }
    val policyRepository: PolicyRepository by lazy {
        PolicyRepository(database)
    }
    val consultationRepository: ConsultationRepository by lazy {
        ConsultationRepository(database, encryptionService)
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
}
