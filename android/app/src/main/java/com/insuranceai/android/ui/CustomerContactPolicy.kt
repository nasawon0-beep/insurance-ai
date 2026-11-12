package com.insuranceai.android.ui

/** A destination only; never replaces the stored/displayed customer phone. */
data class CustomerContactTarget(val original: String, val destination: String)
enum class ContactAction(val androidAction: String, val scheme: String) {
    DIAL("android.intent.action.DIAL", "tel"),
    MESSAGE("android.intent.action.SENDTO", "smsto"),
}
fun customerContactTarget(raw: String?): CustomerContactTarget? {
    if (raw == null || !Regex("^[+0-9() -]+$").matches(raw)) return null
    val destination = raw.replace(Regex("[() -]"), "")
    if (!Regex("^\\+?[0-9]{7,15}$").matches(destination)) return null
    return CustomerContactTarget(raw, destination)
}
