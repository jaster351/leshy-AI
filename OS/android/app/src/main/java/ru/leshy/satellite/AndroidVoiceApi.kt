package ru.leshy.satellite

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.HttpsURLConnection

/**
 * Owner-provisioned conversation key. Never use the administrator LESHY_API_TOKEN.
 * Key ciphertext is in private SharedPreferences; AES key stays in Android Keystore.
 */
internal object AndroidVoiceCredentials {
    private const val ALIAS = "leshy_android_voice_aes_v1"
    private const val PREFS = "leshy_android_voice_credentials"
    private const val CIPHER = "ciphertext"
    private const val IV = "iv"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(
                KeyGenParameterSpec.Builder(
                    ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
        }.generateKey()
    }

    fun save(context: Context, value: String) {
        require(value.length in 32..128 && value.all { it.code in 33..126 }) {
            "Invalid Android conversation key"
        }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val bytes = cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8))
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(CIPHER, Base64.encodeToString(bytes, Base64.NO_WRAP))
            .putString(IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .apply()
    }

    fun read(context: Context): String? {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val value = prefs.getString(CIPHER, null) ?: return null
        val nonce = prefs.getString(IV, null) ?: return null
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(
                Cipher.DECRYPT_MODE, key(),
                GCMParameterSpec(128, Base64.decode(nonce, Base64.NO_WRAP))
            )
            String(
                cipher.doFinal(Base64.decode(value, Base64.NO_WRAP)),
                StandardCharsets.UTF_8
            )
        } catch (_: Exception) {
            null
        }
    }

    fun clear(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
        try {
            val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (store.containsAlias(ALIAS)) store.deleteEntry(ALIAS)
        } catch (_: Exception) { }
    }
}

internal object AndroidVoiceApi {
    data class Answer(val text: String, val sessionId: String)

    /**
     * Завершение разговора сразу разрывает клиентский HTTPS-запрос.
     * На сервере генерация могла уже начаться; для её завершения в будущем
     * понадобится поддержка отмены задания на серверной стороне.
     */
    class RequestHandle {
        private val lock = Any()
        private var connection: HttpsURLConnection? = null
        private var cancelled = false

        fun attach(value: HttpsURLConnection) {
            synchronized(lock) {
                if (cancelled) {
                    value.disconnect()
                    throw java.util.concurrent.CancellationException("Разговор остановлен")
                }
                connection = value
            }
        }

        fun cancel() {
            synchronized(lock) {
                cancelled = true
                connection?.disconnect()
                connection = null
            }
        }

        fun release() {
            synchronized(lock) { connection = null }
        }

        fun checkActive() {
            synchronized(lock) {
                if (cancelled) throw java.util.concurrent.CancellationException("Разговор остановлен")
            }
        }
    }

    private fun connect(server: String, endpoint: String, token: String, method: String): HttpsURLConnection {
        val normalized = ServerReachability.normalize(server)
            ?: throw IllegalArgumentException("Provide a valid HTTPS server address")
        return (URL(normalized + endpoint).openConnection() as HttpsURLConnection).apply {
            requestMethod = method
            instanceFollowRedirects = false
            connectTimeout = 6000
            readTimeout = 120000
            useCaches = false
            setRequestProperty("Authorization", "Bearer $token")
            setRequestProperty("Accept", "application/json")
        }
    }

    fun status(server: String, token: String): Boolean {
        val connection = connect(server, "/v1/android/voice/status", token, "GET")
        return try {
            val code = connection.responseCode
            if (code == HttpURLConnection.HTTP_UNAUTHORIZED) {
                throw SecurityException("Ключ телефона не принят сервером")
            }
            if (code != HttpURLConnection.HTTP_OK) {
                throw IllegalStateException("Сервер ответил HTTP $code")
            }
            connection.inputStream.use { stream ->
                JSONObject(stream.bufferedReader().readText()).optBoolean("ready", false)
            }
        } finally { connection.disconnect() }
    }

    fun ask(
        server: String,
        token: String,
        message: String,
        sessionId: String?,
        request: RequestHandle? = null
    ): Answer {
        require(message.trim().isNotEmpty() && message.length <= 2500)
        request?.checkActive()
        val connection = connect(server, "/v1/android/voice/chat", token, "POST")
        return try {
            request?.attach(connection)
            request?.checkActive()
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            val body = JSONObject().put("message", message)
            if (!sessionId.isNullOrBlank()) body.put("session_id", sessionId)
            val bytes = body.toString().toByteArray(StandardCharsets.UTF_8)
            connection.setFixedLengthStreamingMode(bytes.size)
            connection.outputStream.use { it.write(bytes) }
            val code = connection.responseCode
            if (code == HttpURLConnection.HTTP_UNAUTHORIZED) {
                throw SecurityException("Ключ телефона не принят сервером")
            }
            if (code != HttpURLConnection.HTTP_OK) {
                throw IllegalStateException("Ошибка ответа Лешего: HTTP $code")
            }
            if (!connection.contentType.orEmpty().startsWith("application/json")) {
                throw IllegalStateException("Сервер вернул не JSON")
            }
            val text = connection.inputStream.use { stream ->
                stream.bufferedReader(StandardCharsets.UTF_8).readText()
            }
            if (text.length > 1000000) throw IllegalStateException("Ответ сервера слишком большой")
            request?.checkActive()
            val response = JSONObject(text)
            val answer = response.optString("answer", "").trim()
            val sid = response.optString("session_id", "")
            if (answer.isBlank() || sid.isBlank()) {
                throw IllegalStateException("Сервер вернул пустой ответ или сеанс")
            }
            Answer(answer, sid)
        } finally { connection.disconnect() }
    }
}
