package ru.leshy.satellite

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech

/**
 * System-validated public internet state, NOT availability of the Leshy server.
 * Only announces a state change. Does not transmit any network/audio data.
 */
internal class ConnectivityNotifier(
    context: Context,
    private val onStatus: (Boolean) -> Unit
) {
    private val manager = context.getSystemService(ConnectivityManager::class.java)
    private val handler = Handler(Looper.getMainLooper())
    private var lastOnline: Boolean? = null
    private var closed = false
    private var registered = false
    private var tts: TextToSpeech? = null
    private var offlineVoiceReady = false
    private var pendingSpeech: String? = null
    private val check = Runnable { checkState() }

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) { schedule() }
        override fun onLost(network: Network) { schedule() }
        override fun onCapabilitiesChanged(network: Network, capabilities: NetworkCapabilities) {
            schedule()
        }
    }

    init {
        tts = TextToSpeech(context.applicationContext) { status ->
            handler.post {
                if (!closed && status == TextToSpeech.SUCCESS) {
                    val engine = tts
                    val localRussian = engine?.voices?.firstOrNull {
                        it.locale.language == "ru" && !it.isNetworkConnectionRequired
                    }
                    if (engine != null && localRussian != null &&
                        engine.setVoice(localRussian) == TextToSpeech.SUCCESS) {
                        offlineVoiceReady = true
                        pendingSpeech?.let { speak(it) }
                        pendingSpeech = null
                    }
                }
            }
        }
        try {
            manager.registerDefaultNetworkCallback(networkCallback)
            registered = true
        } catch (_: RuntimeException) {
            // Notification still works; initial connectivity check follows.
        }
        schedule()
    }

    private fun schedule() {
        handler.removeCallbacks(check)
        if (!closed) handler.postDelayed(check, 1200L)
    }

    private fun checkState() {
        if (closed) return
        val active = manager.activeNetwork
        val capabilities = active?.let { manager.getNetworkCapabilities(it) }
        val online = capabilities?.hasCapability(
            NetworkCapabilities.NET_CAPABILITY_VALIDATED
        ) == true
        val previous = lastOnline
        if (previous == online) return
        lastOnline = online
        onStatus(online)
        if (!online) {
            speak("Нет подключения к интернету.")
        } else if (previous == false) {
            speak("Подключение к интернету восстановлено.")
        }
    }

    private fun speak(text: String) {
        val engine = tts
        if (!offlineVoiceReady || engine == null) {
            pendingSpeech = text
            return
        }
        engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, "leshy-network")
    }

    fun close() {
        if (closed) return
        closed = true
        handler.removeCallbacks(check)
        if (registered) {
            try { manager.unregisterNetworkCallback(networkCallback) }
            catch (_: IllegalArgumentException) { }
        }
        tts?.stop()
        tts?.shutdown()
        tts = null
        pendingSpeech = null
        offlineVoiceReady = false
    }
}
