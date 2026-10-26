package com.insuranceai.android.ui

data class AuthenticationUiPolicy(
    val showLockScreen: Boolean,
    val keepNavigationMounted: Boolean,
)

fun authenticationUiPolicy(authenticationRequired: Boolean): AuthenticationUiPolicy =
    AuthenticationUiPolicy(
        showLockScreen = authenticationRequired,
        keepNavigationMounted = true,
    )
