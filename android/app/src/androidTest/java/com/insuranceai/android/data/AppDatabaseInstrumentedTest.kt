package com.insuranceai.android.data

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.insuranceai.android.data.local.AppDatabase
import com.insuranceai.android.data.local.CustomerEntity
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AppDatabaseInstrumentedTest {
    private lateinit var db: AppDatabase

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(),
            AppDatabase::class.java
        ).build()
    }

    @After
    fun tearDown() { db.close() }

    @Test
    fun customerDaoCrudRoundTrip() = runBlocking {
        val customer = CustomerEntity(name = "김테스트", phone = "010-0000-0000")
        db.customerDao().insert(customer)
        assertEquals("김테스트", db.customerDao().getById(customer.id)?.name)

        db.customerDao().update(customer.copy(name = "김수정"))
        assertEquals("김수정", db.customerDao().getById(customer.id)?.name)

        db.customerDao().delete(customer)
        assertEquals(null, db.customerDao().getById(customer.id))
    }
}
