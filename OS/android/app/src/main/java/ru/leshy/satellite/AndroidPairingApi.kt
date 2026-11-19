package ru.leshy.satellite

import android.os.Build
import android.util.Base64
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.nio.charset.StandardCharsets
import javax.net.ssl.HttpsURLConnection

/** Owner-confirmed enrollment. Does not use or request an admin API token. */
internal object AndroidPairingApi {
    data class Ticket(val requestId: String, val signature: String, val expiresIn: Int)
    data class Result(val state: String, val conversationKey: String?)

    private fun encoded(bytes: ByteArray) = Base64.encodeToString(
        bytes, Base64.NO_WRAP or Base64.NO_PADDING or Base64.URL_SAFE
    )

    private fun decoded(raw: String): ByteArray =
        Base64.decode(raw, Base64.NO_WRAP or Base64.NO_PADDING or Base64.URL_SAFE)

    private fun post(server: String, endpoint: String, body: JSONObject): JSONObject {
        val address = ServerReachability.normalize(server)
            ?: throw IllegalArgumentException("Укажите HTTPS-адрес Лешего")
        val connection = URL(address + endpoint).openConnection() as HttpsURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 6500
            connection.readTimeout = 9000
            connection.instanceFollowRedirects = false
            connection.useCaches = false
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.setRequestProperty("Accept", "application/json")
            val bytes = body.toString().toByteArray(StandardCharsets.UTF_8)
            connection.setFixedLengthStreamingMode(bytes.size)
            connection.outputStream.use { it.write(bytes) }
            val status = connection.responseCode
            if (status !in 200..299) {
                throw IllegalStateException(
                    if (status == HttpURLConnection.HTTP_FORBIDDEN) {
                        "Подключение запрещено сервером"
                    } else "Ошибка привязки: HTTP $status"
                )
            }
            if (!connection.contentType.orEmpty().startsWith("application/json")) {
                throw IllegalStateException("Сервер вернул не JSON")
            }
            val response = connection.inputStream.use {
                it.bufferedReader(StandardCharsets.UTF_8).readText()
            }
            if (response.length > 32768) throw IllegalStateException("Ответ сервера слишком большой")
            return JSONObject(response)
        } finally {
            connection.disconnect()
        }
    }

    fun begin(server: String): Ticket {
        val identity = DeviceIdentity.createIfMissing()
        val deviceLabel = Build.MODEL.filter { it.code in 32..126 }.take(64)
            .ifBlank { "Android device" }
        val start = post(server, "/v1/android/pair/start", JSONObject()
            .put("public_key", identity.publicKeyX509Base64Url)
            .put("name", deviceLabel))
        val requestId = start.getString("request_id")
        val challenge = decoded(start.getString("challenge"))
        if (challenge.size != 32) throw IllegalStateException("Недействительный запрос сервера")
        val signature = encoded(DeviceIdentity.signPairingChallenge(challenge))
        val result = post(server, "/v1/android/pair/prove", JSONObject()
            .put("request_id", requestId)
            .put("signature", signature))
        if (result.optString("state") != "pending_owner") {
            throw IllegalStateException("Сервер не подтвердил ключ телефона")
        }
        return Ticket(requestId, signature, result.optInt("expires_in", 300))
    }

    fun poll(server: String, ticket: Ticket): Result {
        val result = post(server, "/v1/android/pair/status", JSONObject()
            .put("request_id", ticket.requestId)
            .put("signature", ticket.signature))
        val state = result.optString("state")
        if (state !in setOf("pending_owner", "approved")) {
            throw IllegalStateException("Неизвестный статус привязки")
        }
        val key = if (state == "approved") result.optString("conversation_key") else null
        if (state == "approved" && (key == null || key.length !in 32..128)) {
            throw IllegalStateException("Сервер не выдал ключ разговора")
        }
        return Result(state, key)
    }
}
