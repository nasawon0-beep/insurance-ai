package com.insuranceai.android.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Assessment
import androidx.compose.material.icons.outlined.Description
import androidx.compose.material.icons.outlined.Groups
import androidx.compose.material.icons.outlined.SupportAgent
import androidx.compose.material.icons.outlined.Sync
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.insuranceai.android.auth.SessionManager
import com.insuranceai.android.security.AuthenticationGate

private sealed class Route(val value: String, val label: String, val icon: ImageVector? = null) {
    data object Login : Route("login", "로그인")
    data object Customers : Route("customers", "고객", Icons.Outlined.Groups)
    data object Policies : Route("policies", "계약", Icons.Outlined.Description)
    data object Coverage : Route("coverage", "분석", Icons.Outlined.Assessment)
    data object Consultations : Route("consultations", "상담", Icons.Outlined.SupportAgent)
    data object Sync : Route("sync", "동기화", Icons.Outlined.Sync)
}

val LocalAuthenticationResumeId = staticCompositionLocalOf { 0L }

@Composable
fun InsuranceAiApp(
    sessionManager: SessionManager,
    authenticationGate: AuthenticationGate,
    biometricAvailable: Boolean,
    onAuthenticate: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit
) {
    val navController = rememberNavController()
    val authenticationState by authenticationGate.state.collectAsState()
    var selectedCustomerId by rememberSaveable { mutableStateOf<String?>(null) }
    var showAllPolicies by rememberSaveable { mutableStateOf(false) }
    val darkTheme = isSystemInDarkTheme()
    val colors = if (darkTheme) darkColorScheme(
        primary = Color(0xFFAEC7E8),
        onPrimary = Color(0xFF0D2D4D),
        primaryContainer = Color(0xFF304F76),
        onPrimaryContainer = Color.White,
        background = Color(0xFF12171D),
        surface = Color(0xFF1A2027),
        surfaceVariant = Color(0xFF27313B),
        onSurface = Color(0xFFF1F4F7),
    ) else lightColorScheme(
        primary = Color(0xFF304F76),
        onPrimary = Color.White,
        primaryContainer = Color(0xFFE7EDF4),
        onPrimaryContainer = Color(0xFF243B57),
        background = Color(0xFFF4F5F6),
        surface = Color.White,
        surfaceVariant = Color(0xFFE7EDF4),
        onSurface = Color(0xFF242B33),
    )
    MaterialTheme(colorScheme = colors) {
        val authenticationPolicy = authenticationUiPolicy(authenticationState.required)
        if (authenticationPolicy.showLockScreen) {
            LaunchedEffect(authenticationState.requestId) { sessionManager.endSession() }
        }
        Box(Modifier.fillMaxSize()) {
            CompositionLocalProvider(LocalAuthenticationResumeId provides authenticationState.resumeId) {
                val bottomRoutes = listOf(Route.Customers, Route.Policies, Route.Coverage, Route.Consultations, Route.Sync)
                val navBackStackEntry by navController.currentBackStackEntryAsState()
                val currentRoute = navBackStackEntry?.destination?.route
                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    bottomBar = {
                        if (currentRoute in bottomRoutes.map { it.value }) {
                            NavigationBar {
                                bottomRoutes.forEach { route ->
                                    NavigationBarItem(
                                        selected = currentRoute == route.value,
                                        onClick = { navController.navigate(route.value) { launchSingleTop = true } },
                                        label = { Text(route.label) },
                                        icon = { Icon(route.icon!!, contentDescription = route.label) }
                                    )
                                }
                            }
                        }
                    }
                ) { padding ->
                    NavHost(
                        navController = navController,
                        startDestination = if (sessionManager.isSessionValid()) Route.Customers.value else Route.Login.value,
                        modifier = Modifier.padding(padding)
                    ) {
                        composable(Route.Login.value) {
                            LoginScreen(
                                biometricAvailable = biometricAvailable,
                                onAuthenticate = { errorSink ->
                                    onAuthenticate(
                                        { navController.navigate(Route.Customers.value) { popUpTo(Route.Login.value) { inclusive = true } } },
                                        errorSink
                                    )
                                }
                            )
                        }
                        composable(Route.Customers.value) {
                            CustomersScreen(
                                selectedCustomerId = selectedCustomerId,
                                onSelectedCustomerChange = { selectedCustomerId = it },
                                onOpenPoliciesForCustomer = { customerId ->
                                    selectedCustomerId = customerId
                                    showAllPolicies = false
                                    navController.navigate(Route.Policies.value) { launchSingleTop = true }
                                },
                                onOpenCoverageForCustomer = { customerId ->
                                    selectedCustomerId = customerId
                                    navController.navigate(Route.Coverage.value) { launchSingleTop = true }
                                },
                                onOpenConsultationsForCustomer = { customerId ->
                                    selectedCustomerId = customerId
                                    navController.navigate(Route.Consultations.value) { launchSingleTop = true }
                                },
                                onOpenSync = { navController.navigate(Route.Sync.value) { launchSingleTop = true } },
                            )
                        }
                        composable(Route.Policies.value) {
                            PoliciesScreen(
                                selectedCustomerId = selectedCustomerId,
                                showAllPolicies = showAllPolicies,
                                onSelectCustomer = {
                                    selectedCustomerId = it
                                    showAllPolicies = false
                                },
                                onChooseCustomer = {
                                    selectedCustomerId = null
                                    showAllPolicies = false
                                },
                                onShowAll = {
                                    selectedCustomerId = null
                                    showAllPolicies = true
                                },
                            )
                        }
                        composable(Route.Coverage.value) { CoverageAnalysisScreen(selectedCustomerId = selectedCustomerId) }
                        composable(Route.Consultations.value) { ConsultationsScreen(selectedCustomerId = selectedCustomerId) }
                        composable(Route.Sync.value) { SyncScreen(onAuthenticate = onAuthenticate) }
                    }
                }
            }
            if (authenticationPolicy.showLockScreen) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    LoginScreen(
                        biometricAvailable = biometricAvailable,
                        onAuthenticate = { errorSink -> onAuthenticate({}, errorSink) },
                    )
                }
            }
        }
    }
}
