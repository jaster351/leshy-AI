package ru.leshy.satellite

import android.content.Context

/**
 * Temporary developer-only remote connectivity selection.
 * Does not relax TLS checks, grant ownership or expose the server through NAT.
 */
internal object DeveloperConnection {
    private const val PREFS = "leshy_developer_connection"
    private const val KEY_EXTERNAL = "external_https_address"
    private const val KEY_ENABLED_UNTIL = "enabled_until_ms"
    private const val SESSION_MS = 60L * 60L * 1000L

    fun externalAddress(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_EXTERNAL, "")?.trim().orEmpty()

    fun saveExternalAddress(context: Context, address: String) {
        require(ServerReachability.normalize(address) == address) {
            "External address must be a normalized HTTPS origin"
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_EXTERNAL, address).apply()
    }

    fun enabledUntil(context: Context): Long {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val expiry = prefs.getLong(KEY_ENABLED_UNTIL, 0L)
        if (expiry <= System.currentTimeMillis()) {
            if (expiry != 0L) prefs.edit().remove(KEY_ENABLED_UNTIL).apply()
            return 0L
        }
        return expiry
    }

    fun enableForOneHour(context: Context): Boolean {
        if (ServerReachability.normalize(externalAddress(context)) == null) return false
        val until = System.currentTimeMillis() + SESSION_MS
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putLong(KEY_ENABLED_UNTIL, until).apply()
        return true
    }

    fun disable(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().remove(KEY_ENABLED_UNTIL).apply()
    }

    /** Selected address for both the manual probe and the listening monitor. */
    fun activeAddress(context: Context): String =
        if (enabledUntil(context) > 0L) externalAddress(context)
        else ServerReachability.address(context)
}
