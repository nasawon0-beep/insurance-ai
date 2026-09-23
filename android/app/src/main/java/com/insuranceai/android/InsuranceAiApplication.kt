package com.insuranceai.android

import android.app.Application
import com.insuranceai.android.data.ConsultationRepository
import com.insuranceai.android.data.CustomerRepository
import com.insuranceai.android.data.PolicyRepository
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.security.EncryptionService
import com.insuranceai.android.security.KeyStoreManager

class InsuranceAiApplication : Application() {
    val container: AppContainer by lazy { AppContainer(this) }
}

class AppContainer(application: Application) {
    private val keyStoreManager = KeyStoreManager(application)
    val encryptionService = EncryptionService(keyStoreManager)
    val database: AppDatabase by lazy {
        AppDatabase.create(application, keyStoreManager.getOrCreateDbPassphrase())
    }
    val customerRepository: CustomerRepository by lazy {
        CustomerRepository(database.customerDao(), encryptionService)
    }
    val policyRepository: PolicyRepository by lazy {
        PolicyRepository(database.policyDao())
    }
    val consultationRepository: ConsultationRepository by lazy {
        ConsultationRepository(database.consultationDao(), encryptionService)
    }
}
