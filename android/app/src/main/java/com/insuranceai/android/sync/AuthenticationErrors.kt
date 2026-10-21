package com.insuranceai.android.sync

fun Throwable.requiresUserAuthentication(): Boolean =
    generateSequence(this) { it.cause }.any { cause ->
        cause is SecurityException ||
            cause.javaClass.simpleName == "UserNotAuthenticatedException" ||
            cause.message?.contains("User not authenticated", ignoreCase = true) == true
    }
