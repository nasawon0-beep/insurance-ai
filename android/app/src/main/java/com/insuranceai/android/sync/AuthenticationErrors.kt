package com.insuranceai.android.sync

import com.insuranceai.android.security.isUserAuthenticationRequired

fun Throwable.requiresUserAuthentication(): Boolean = isUserAuthenticationRequired()
