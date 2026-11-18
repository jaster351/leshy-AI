package ru.leshy.satellite

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import java.util.Locale

/**
 * An explicit, one-toggle Android voice *session*, not one button per utterance.
 * Speech recognition restarts automatically after each spoken Leshy answer.
 *
 * System SpeechRecognizer may use an online third-party ASR engine. The session
 * only works with a visible Activity; a separate foreground service would be
 * required for reliable screen-off use on Android 14+.
 */
internal class ContinuousSpeechRecognizer(
    private val activity: Activity,
    private val onState: (String) -> Unit,
    private val onUtterance: (String) -> Unit,
    private val onStopped: () -> Unit
) {
    private val handler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var pending: Runnable? = null
    private var epoch = 0
    private var listening = false
    private var waitingForAnswer = false
    private var consecutiveErrors = 0
    var isActive: Boolean = false
        private set

    private fun current(id: Int) = isActive && epoch == id

    fun start(): Boolean {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (isActive) return true
        if (!SpeechRecognizer.isRecognitionAvailable(activity)) {
            onState("На телефоне нет службы распознавания речи. Можно использовать текстовый ввод.")
            return false
        }
        isActive = true
        val id = ++epoch
        waitingForAnswer = false
        consecutiveErrors = 0
        try {
            val instance = SpeechRecognizer.createSpeechRecognizer(activity)
            recognizer = instance
            instance.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {
                    if (current(id)) onState("Леший слушает. Говорите свободно.")
                }
                override fun onBeginningOfSpeech() {
                    if (current(id)) onState("Слышу Вас...")
                }
                override fun onRmsChanged(rmsdB: Float) { }
                override fun onBufferReceived(buffer: ByteArray?) { }
                override fun onEndOfSpeech() {
                    if (current(id)) onState("Распознаю Вашу фразу...")
                }
                override fun onError(error: Int) {
                    if (!current(id) || waitingForAnswer) return
                    listening = false
                    if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) {
                        onState("Нет разрешения на микрофон: разговор остановлен.")
                        stop()
                        return
                    }
                    val silence = error == SpeechRecognizer.ERROR_NO_MATCH ||
                        error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                    if (!silence) consecutiveErrors++
                    if (consecutiveErrors >= 8) {
                        onState("Распознавание неоднократно завершилось ошибкой ($error). Разговор остановлен.")
                        stop()
                        return
                    }
                    val delay = if (error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY ||
                        error == SpeechRecognizer.ERROR_CLIENT) 1400L
                        else if (error == SpeechRecognizer.ERROR_NO_MATCH ||
                            error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) 750L
                        else 1750L
                    onState("Жду следующую фразу...")
                    queueRecognition(id, delay)
                }
                override fun onResults(results: Bundle?) {
                    if (!current(id) || waitingForAnswer) return
                    listening = false
                    val phrase = results
                        ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull()?.trim().orEmpty()
                    if (phrase.isBlank()) {
                        queueRecognition(id, 650)
                    } else {
                        consecutiveErrors = 0
                        waitingForAnswer = true
                        onState("Вы: $phrase\\nЛеший думает...")
                        onUtterance(phrase)
                    }
                }
                override fun onPartialResults(partialResults: Bundle?) {
                    if (!current(id) || waitingForAnswer) return
                    val partial = partialResults
                        ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                        ?.firstOrNull()?.trim().orEmpty()
                    if (partial.isNotBlank()) onState("Вы говорите: $partial")
                }
                override fun onEvent(eventType: Int, params: Bundle?) { }
            })
            queueRecognition(id, 800L)
            return true
        } catch (_: Exception) {
            onState("Не удалось начать непрерывное распознавание речи.")
            stop()
            return false
        }
    }

    /** Used when a text question is submitted during an active conversation. */
    fun pauseForAnswer() {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!isActive || waitingForAnswer) return
        waitingForAnswer = true
        pending?.let { handler.removeCallbacks(it) }
        pending = null
        listening = false
        try { recognizer?.cancel() } catch (_: Exception) { }
    }

    /** Call when TTS finishes (or a text-only answer/error is shown). */
    fun resumeAfterAnswer() {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!isActive || !waitingForAnswer) return
        waitingForAnswer = false
        queueRecognition(epoch, 450L)
    }

    private fun queueRecognition(id: Int, delayMs: Long) {
        if (!current(id) || waitingForAnswer) return
        pending?.let { handler.removeCallbacks(it) }
        val runnable = Runnable {
            pending = null
            if (!current(id) || waitingForAnswer || listening) return@Runnable
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(
                    RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                    RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
                )
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
                putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, activity.packageName)
            }
            try {
                listening = true
                onState("Слушаю Вас...")
                recognizer?.startListening(intent)
                    ?: throw IllegalStateException("Recognizer is unavailable")
            } catch (_: Exception) {
                listening = false
                consecutiveErrors++
                if (consecutiveErrors >= 8) {
                    onState("Не удалось запустить распознавание. Разговор остановлен.")
                    stop()
                } else queueRecognition(id, 1300L)
            }
        }
        pending = runnable
        handler.postDelayed(runnable, delayMs)
    }

    fun stop() {
        check(Looper.myLooper() == Looper.getMainLooper())
        if (!isActive && recognizer == null) return
        isActive = false
        epoch++
        pending?.let { handler.removeCallbacks(it) }
        pending = null
        waitingForAnswer = false
        listening = false
        val old = recognizer
        recognizer = null
        try { old?.cancel() } catch (_: Exception) { }
        try { old?.destroy() } catch (_: Exception) { }
        onStopped()
    }
}
