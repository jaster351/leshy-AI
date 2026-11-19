package ru.leshy.satellite

import android.content.Context
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLException

/**
 * Connectivity probe, not pairing/authentication. Do not send a token,
 * credentials, microphone audio, images or profile information.
 */
internal object ServerReachability {
    private const val PREFS = "leshy_server_settings"
    private const val KEY_ADDRESS = "server_https_address"

    enum class State {
        NOT_CONFIGURED, REACHABLE_UNVERIFIED, AUTH_REQUIRED,
        UNREACHABLE, TLS_FAILURE, SERVER_ERROR
    }

    data class Result(val state: State, val message: String)

    fun address(context: Context): String =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_ADDRESS, "")?.trim().orEmpty()

    fun saveAddress(context: Context, address: String) {
        require(normalize(address) == address)
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_ADDRESS, address).apply()
    }

    fun normalize(raw: String): String? {
        val input = raw.trim()
        if (input.length !in 9..256) return null
        return try {
            val uri = URI(input)
            val host = uri.host?.trim('[', ']') ?: return null
            if (!uri.scheme.equals("https", true) ||
                host.isBlank() || uri.rawUserInfo != null ||
                uri.rawQuery != null || uri.rawFragment != null ||
                (uri.rawPath.isNotEmpty() && uri.rawPath != "/") ||
                uri.port == 0 || uri.port > 65535 || uri.port < -1
            ) return null
            val lower = host.lowercase(Locale.ROOT)
            val hostLabel = if (lower.contains(":")) "[$lower]" else lower
            val portLabel = if (uri.port > 0) ":" + uri.port else ""
            "https://" + hostLabel + portLabel
        } catch (_: Exception) { null }
    }

    fun probe(address: String): Result {
        val url = normalize(address)
            ?: return Result(State.NOT_CONFIGURED, "Адрес сервера не настроен")
        var connection: HttpsURLConnection? = null
        return try {
            connection = URL(url + "/v1/status").openConnection() as HttpsURLConnection
            connection.connectTimeout = 4000
            connection.readTimeout = 4000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.requestMethod = "GET"
            connection.setRequestProperty("Accept", "application/json")
            // Authenticated status is not accessible without an API token
            // when the server configures LESHY_API_TOKEN.
            val status = connection.responseCode
            val mime = connection.contentType.orEmpty().lowercase(Locale.ROOT)
            when {
                status == HttpURLConnection.HTTP_UNAUTHORIZED ||
                    status == HttpURLConnection.HTTP_FORBIDDEN ->
                    Result(State.AUTH_REQUIRED, "Узел отвечает. Требуется авторизация")
                status == HttpURLConnection.HTTP_OK && mime.startsWith("application/json") ->
                    Result(State.REACHABLE_UNVERIFIED, "Сервер отвечает. Клиент не авторизован")
                status in 300..399 ->
                    Result(State.SERVER_ERROR, "Сервер перенаправил запрос; проверка отклонена")
                else ->
                    Result(State.SERVER_ERROR, "Некорректный ответ сервера: HTTP " + status)
            }
        } catch (_: SSLException) {
            Result(State.TLS_FAILURE, "Не удалось проверить HTTPS-сертификат сервера")
        } catch (_: Exception) {
            Result(State.UNREACHABLE, "Нет связи с сервером Лешего")
        } finally {
            connection?.disconnect()
        }
    }
}

/** Re-probes an explicitly configured HTTPS server while listening is on. */
internal class ServerStatusMonitor(
    private val context: Context,
    private val onChange: (ServerReachability.Result, ServerReachability.State?) -> Unit
) {
    private val worker = Executors.newSingleThreadScheduledExecutor()
    @Volatile private var closed = false
    private var lastAddress: String? = null
    private var lastState: ServerReachability.State? = null

    init {
        worker.scheduleWithFixedDelay({
            if (closed) return@scheduleWithFixedDelay
            try {
                val url = ServerReachability.address(context)
                val state = ServerReachability.probe(url)
                val previous = if (url == lastAddress) lastState else null
                if (previous != state.state && !closed) onChange(state, previous)
                lastAddress = url
                lastState = state.state
            } catch (_: Exception) {
                // Never present failure as confirmed connectivity.
            }
        }, 0, 20, TimeUnit.SECONDS)
    }

    fun close() {
        closed = true
        worker.shutdownNow()
    }
}
