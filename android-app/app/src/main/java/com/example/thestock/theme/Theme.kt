package com.example.thestock.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val FinancialDarkColorScheme = darkColorScheme(
    primary = CyberCyan,
    onPrimary = BackgroundDark,
    secondary = NeonGreen,
    onSecondary = BackgroundDark,
    tertiary = SlotCColor,
    background = BackgroundDark,
    onBackground = TextPrimary,
    surface = SurfaceDark,
    onSurface = TextPrimary,
    surfaceVariant = SurfaceVariantDark,
    onSurfaceVariant = TextSecondary,
    error = NeonRed,
    onError = TextPrimary,
    outline = SurfaceBorder
)

@Composable
fun TheStockTheme(
    content: @Composable () -> Unit
) {
    MaterialTheme(
        colorScheme = FinancialDarkColorScheme,
        typography = Typography,
        content = content
    )
}
