package com.insuranceai.android.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.insuranceai.android.auth.SessionManager

private sealed class Route(val value: String, val label: String) {
    data object Login : Route("login", "로그인")
    data object Customers : Route("customers", "고객")
    data object Policies : Route("policies", "계약")
    data object Consultations : Route("consultations", "상담")
}

@Composable
fun InsuranceAiApp(
    sessionManager: SessionManager,
    biometricAvailable: Boolean,
    onAuthenticate: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit
) {
    val navController = rememberNavController()
    MaterialTheme(colorScheme = lightColorScheme()) {
        val bottomRoutes = listOf(Route.Customers, Route.Policies, Route.Consultations)
        val navBackStackEntry by navController.currentBackStackEntryAsState()
        val currentRoute = navBackStackEntry?.destination?.route
        Scaffold(
            bottomBar = {
                if (currentRoute in bottomRoutes.map { it.value }) {
                    NavigationBar {
                        bottomRoutes.forEach { route ->
                            NavigationBarItem(
                                selected = currentRoute == route.value,
                                onClick = { navController.navigate(route.value) { launchSingleTop = true } },
                                label = { Text(route.label) },
                                icon = { Text(route.label.take(1)) }
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
                composable(Route.Customers.value) { CustomersScreen() }
                composable(Route.Policies.value) { PoliciesScreen() }
                composable(Route.Consultations.value) { ConsultationsScreen() }
            }
        }
    }
}
