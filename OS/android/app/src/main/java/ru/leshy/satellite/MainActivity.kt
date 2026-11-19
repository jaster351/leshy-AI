package ru.leshy.satellite

import android.Manifest
import android.app.AlertDialog
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.content.ActivityNotFoundException
import android.content.Context
import android.speech.RecognizerIntent
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.text.InputType
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.ImageView
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.face.FaceDetection
import com.google.mlkit.vision.face.FaceDetectorOptions
import java.util.concurrent.Executors
import java.util.Locale

class MainActivity : ComponentActivity() {
    private lateinit var micButton: Button
    private lateinit var cameraButton: Button
    private lateinit var micStatus: TextView
    private lateinit var faceStatus: TextView
    private lateinit var cameraPreview: PreviewView
    private lateinit var voiceStatus: TextView
    private lateinit var voiceQuestion: EditText
    private lateinit var voiceSecret: EditText
    private lateinit var dialogueButton: Button
    private var continuousRecognizer: ContinuousSpeechRecognizer? = null
    private var continuousActive = false
    private var continuousEpoch = 0
    private var voiceRequestEpoch = 0
    private var activeVoiceRequest: AndroidVoiceApi.RequestHandle? = null
    private var pendingUtteranceId: String? = null
    private var voiceSession: String? = null
    private var speaker: TextToSpeech? = null
    private var speakerReady = false
    private var voiceBusy = false
    private lateinit var serverAddress: EditText
    private lateinit var serverStatus: TextView
    private lateinit var deviceStatus: TextView
    private lateinit var deviceCreateButton: Button
    private val deviceExecutor = Executors.newSingleThreadExecutor()
    private val serverExecutor = Executors.newSingleThreadExecutor()
    private val voiceExecutor = Executors.newCachedThreadPool()

    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private val continuousMicPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startContinuousConversation()
        else if (::voiceStatus.isInitialized) voiceStatus.text = "Микрофон не разрешён. Разговор не начат."
    }
    private val detector = FaceDetection.getClient(
        FaceDetectorOptions.Builder()
            .setPerformanceMode(FaceDetectorOptions.PERFORMANCE_MODE_FAST)
            .build()
    )
    private var cameraProvider: ProcessCameraProvider? = null
    private var cameraEnabled = false

    private val microphonePermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startListening()
        else micStatus.text = "Разрешение на микрофон не предоставлено"
    }

    private val notificationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* Notification may be denied; the service remains user-started. */ }

    private val cameraPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startCamera()
        else faceStatus.text = "Разрешение на камеру не предоставлено"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildHomeScreen()
        refreshMicStatus()
        refreshDeviceIdentity()
        voiceSession = getSharedPreferences("android_voice_session", MODE_PRIVATE)
            .getString("session_id", null)
        speaker = TextToSpeech(this) { status ->
            runOnUiThread {
                speakerReady = status == TextToSpeech.SUCCESS
                if (speakerReady) {
                    speaker?.language = Locale("ru", "RU")
                }
            }
        }
        speaker?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) { }
            override fun onDone(utteranceId: String?) {
                runOnUiThread {
                    if (continuousActive && pendingUtteranceId == utteranceId) {
                        pendingUtteranceId = null
                        continuousRecognizer?.resumeAfterAnswer()
                    }
                }
            }
            @Deprecated("Deprecated in Android SDK")
            override fun onError(utteranceId: String?) {
                runOnUiThread {
                    if (continuousActive && pendingUtteranceId == utteranceId) {
                        pendingUtteranceId = null
                        voiceStatus.append("\nНе удалось озвучить ответ. Снова слушаю.")
                        continuousRecognizer?.resumeAfterAnswer()
                    }
                }
            }
        })
    }

    private fun buildHomeScreen() {
        val ui = LeshyMobileUi(this)
        window.statusBarColor = ui.backgroundColor
        window.navigationBarColor = ui.backgroundColor
        window.decorView.systemUiVisibility = 0
        val screen = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(ui.backgroundColor)
            clipToPadding = false
        }
        val root = ui.column(18)
        screen.addView(root)

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.ic_leshy_brand)
            scaleType = ImageView.ScaleType.FIT_CENTER
            contentDescription = "Эмблема Лешего"
        }
        header.addView(logo, LinearLayout.LayoutParams(ui.dp(58), ui.dp(58)))
        val brand = ui.column().apply {
            setPadding(ui.dp(12), 0, 0, 0)
            addView(ui.label("ЛЕШИЙ", 24f, bold = true))
            addView(ui.label("Персональный ИИ · Android", 12f, muted = true))
        }
        header.addView(brand, LinearLayout.LayoutParams(0, -2, 1f))
        ui.add(root, header, bottom = 22)

        val settingsPanel = ui.column()
        settingsPanel.visibility = View.GONE
        fun revealSettings() {
            settingsPanel.visibility = View.VISIBLE
            screen.post { screen.smoothScrollTo(0, settingsPanel.top + ui.dp(130)) }
        }

        val conversation = ui.panel(prominent = true)
        ui.add(conversation, ui.label("ГОЛОСОВОЙ ДИАЛОГ", 12f, muted = true, bold = true))
        ui.add(conversation, ui.label("Я рядом.", 30f, bold = true), top = 5)
        ui.add(conversation, ui.label(
            "Говорите естественно. Леший ответит через Ваш сервер.",
            14f, muted = true
        ), top = 6)
        serverStatus = ui.label(
            if (ServerReachability.address(this).isBlank())
                "● Сервер ещё не настроен"
            else "● Проверяем связь с сервером",
            13f, muted = true
        )
        ui.add(conversation, serverStatus, top = 18)
        voiceStatus = ui.label(
            if (AndroidVoiceCredentials.read(this).isNullOrBlank())
                "Для начала разговора подключите телефон к Лешему."
            else "Готов к разговору.",
            15f
        )
        ui.add(conversation, voiceStatus, top = 12)
        dialogueButton = ui.button("🎙 Начать разговор", prominent = true).apply {
            setOnClickListener {
                if (continuousActive) {
                    stopContinuousConversation("Разговор завершён.")
                } else if (AndroidVoiceCredentials.read(this@MainActivity).isNullOrBlank() ||
                    ServerReachability.address(this@MainActivity).isBlank()) {
                    voiceStatus.text = "Настройте адрес сервера и доступ к разговору."
                    revealSettings()
                } else {
                    startContinuousConversation()
                }
            }
        }
        ui.add(conversation, dialogueButton, top = 18)
        ui.add(conversation, ui.label(
            "Нажмите ещё раз, чтобы закончить. Микрофон включается только с Вашего разрешения.",
            12f, muted = true
        ), top = 10)
        ui.add(root, conversation)

        val messageCard = ui.panel()
        ui.add(messageCard, ui.label("СООБЩЕНИЕ", 12f, muted = true, bold = true))
        voiceQuestion = ui.input("Напишите Лешему...").apply {
            setSingleLine(false)
            minLines = 2
            maxLines = 5
        }
        ui.add(messageCard, voiceQuestion, top = 12)
        ui.add(messageCard, ui.button("Отправить сообщение", prominent = true).apply {
            setOnClickListener {
                if (AndroidVoiceCredentials.read(this@MainActivity).isNullOrBlank() ||
                    ServerReachability.address(this@MainActivity).isBlank()) {
                    voiceStatus.text = "Сначала подключите телефон к серверу Лешего."
                    revealSettings()
                } else {
                    submitVoiceQuestion(voiceQuestion.text.toString())
                }
            }
        }, top = 10)
        ui.add(messageCard, ui.button("Новый диалог").apply {
            setOnClickListener {
                voiceSession = null
                getSharedPreferences("android_voice_session", MODE_PRIVATE).edit().clear().apply()
                voiceStatus.text = "Новый разговор. Можете задать вопрос."
            }
        }, top = 8)
        ui.add(root, messageCard, top = 16)

        val settingsToggle = ui.button("⚙ Подключение и настройки").apply {
            contentDescription = "Показать или скрыть настройки подключения и устройства"
            setOnClickListener {
                settingsPanel.visibility =
                    if (settingsPanel.visibility == View.VISIBLE) View.GONE else View.VISIBLE
                if (settingsPanel.visibility == View.VISIBLE) {
                    screen.post { screen.smoothScrollTo(0, settingsPanel.top) }
                }
            }
        }
        ui.add(root, settingsToggle, top = 18)

        val connection = ui.panel()
        ui.add(connection, ui.label("ПОДКЛЮЧЕНИЕ К ЛЕШЕМУ", 12f, muted = true, bold = true))
        ui.add(connection, ui.label("Сервер", 17f, bold = true), top = 8)
        ui.add(connection, ui.label(
            "Для подключения требуется HTTPS. Автоматическая привязка " +
                "с подтверждением владельца пока не реализована.",
            13f, muted = true
        ), top = 7)
        serverAddress = ui.input("https://адрес-сервера:порт").apply {
            setSingleLine(true)
            setText(ServerReachability.address(this@MainActivity))
        }
        ui.add(connection, serverAddress, top = 12)
        ui.add(connection, ui.button("Сохранить адрес и проверить").apply {
            setOnClickListener { checkServer() }
        }, top = 9)
        ui.add(connection, ui.label(
            "Временный способ подключения: ограниченный ключ Android-разговора. " +
                "Не используйте административный токен.",
            13f, muted = true
        ), top = 18)
        voiceSecret = ui.input("Ключ доступа к разговору").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
        }
        ui.add(connection, voiceSecret, top = 10)
        ui.add(connection, ui.button("Сохранить ключ доступа").apply {
            setOnClickListener {
                val value = voiceSecret.text.toString().trim()
                try {
                    AndroidVoiceCredentials.save(this@MainActivity, value)
                    voiceSecret.setText("")
                    voiceSession = null
                    getSharedPreferences("android_voice_session", MODE_PRIVATE).edit().clear().apply()
                    voiceStatus.text = "Ключ сохранён в Android Keystore. Проверьте подключение."
                } catch (_: Exception) {
                    voiceStatus.text = "Ключ не сохранён. Проверьте правильность значения."
                }
            }
        }, top = 9)
        ui.add(connection, ui.button("Проверить доступ к разговору").apply {
            setOnClickListener { checkVoiceConnection() }
        }, top = 8)
        ui.add(settingsPanel, connection, top = 12)

        val cameraCard = ui.panel()
        ui.add(cameraCard, ui.label("КАМЕРА И ЗРЕНИЕ", 12f, muted = true, bold = true))
        ui.add(cameraCard, ui.label(
            "Пока Леший только обнаруживает лица. Узнавание людей ещё не подключено.",
            13f, muted = true
        ), top = 9)
        cameraButton = ui.button("Включить камеру и обнаружение лиц").apply {
            setOnClickListener {
                if (cameraEnabled) stopCamera()
                else if (ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.CAMERA
                ) == PackageManager.PERMISSION_GRANTED) startCamera()
                else cameraPermission.launch(Manifest.permission.CAMERA)
            }
        }
        ui.add(cameraCard, cameraButton, top = 12)
        faceStatus = ui.label("Камера выключена; личность не определена", 13f, muted = true)
        ui.add(cameraCard, faceStatus, top = 10)
        cameraPreview = PreviewView(this).apply { visibility = View.GONE }
        cameraCard.addView(cameraPreview, LinearLayout.LayoutParams(-1, ui.dp(220)).apply {
            topMargin = ui.dp(10)
        })
        ui.add(cameraCard, ui.label(
            "Камера работает только при открытом приложении.",
            12f, muted = true
        ), top = 8)
        ui.add(settingsPanel, cameraCard, top = 14)

        val deviceCard = ui.panel()
        ui.add(deviceCard, ui.label("БЕЗОПАСНОСТЬ УСТРОЙСТВА", 12f, muted = true, bold = true))
        ui.add(deviceCard, ui.label(
            "Локальный ключ устройства сам по себе не даёт доступа к серверу.",
            13f, muted = true
        ), top = 9)
        deviceStatus = ui.label("Состояние ключа загружается...", 13f, muted = true)
        ui.add(deviceCard, deviceStatus, top = 10)
        deviceCreateButton = ui.button("Подготовить ключ устройства").apply {
            setOnClickListener { createDeviceIdentity() }
        }
        ui.add(deviceCard, deviceCreateButton, top = 10)
        ui.add(deviceCard, ui.button("Удалить локальный ключ").apply {
            setOnClickListener {
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Удалить ключ устройства?")
                    .setMessage(
                        "Действие необратимо. Для будущих серверных привязок " +
                            "потребуется отдельный отзыв доступа на сервере."
                    )
                    .setNegativeButton("Отмена", null)
                    .setPositiveButton("Удалить") { _, _ -> deleteDeviceIdentity() }
                    .show()
            }
        }, top = 8)
        ui.add(settingsPanel, deviceCard, top = 14)

        val soundCard = ui.panel()
        ui.add(soundCard, ui.label("МИКРОФОН И ПИТАНИЕ", 12f, muted = true, bold = true))
        micStatus = ui.label("Микрофон выключен", 13f, muted = true)
        ui.add(soundCard, micStatus, top = 10)
        micButton = ui.button("Включить фоновый микрофон (без диалога)").apply {
            setOnClickListener {
                if (continuousActive) {
                    stopContinuousConversation(
                        "Непрерывный диалог остановлен: выбран отдельный режим микрофона."
                    )
                }
                if (ListeningService.isListening) stopListening()
                else if (ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED) startListening()
                else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
        ui.add(soundCard, micButton, top = 10)
        ui.add(soundCard, ui.button("Настройки энергосбережения Android").apply {
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        }, top = 8)
        ui.add(soundCard, ui.button("Отозвать согласие на распознавание речи").apply {
            setOnClickListener {
                if (continuousActive) stopContinuousConversation("Разговор завершён.")
                getSharedPreferences("android_voice_privacy", MODE_PRIVATE)
                    .edit().putBoolean("system_asr_agreed", false).apply()
                voiceStatus.text = "Согласие отозвано. Распознавание выключено."
            }
        }, top = 8)
        ui.add(settingsPanel, soundCard, top = 14)

        ui.add(settingsPanel, ui.label(
            "Речь распознаётся системой Android; она может передавать звук " +
                "своему поставщику. Леший получает распознанный текст по HTTPS. " +
                "Непрерывный диалог работает только при открытом приложении.",
            12f, muted = true
        ), top = 15, bottom = 10)
        ui.add(root, settingsPanel)
        ui.add(root, ui.label(
            "Леший · Экспериментальный мобильный клиент",
            12f, muted = true
        ), top = 24, bottom = 20)
        setContentView(screen)
    }

    override fun onResume() {
        super.onResume()
        if (::micStatus.isInitialized) refreshMicStatus()
        if (::serverAddress.isInitialized &&
            ServerReachability.address(this).isNotBlank()) {
            checkServer()
        }
    }

    private fun checkServer() {
        val normalized = ServerReachability.normalize(serverAddress.text.toString())
        if (normalized == null) {
            serverStatus.text = "Укажите HTTPS-адрес без пути, логина и параметров"
            return
        }
        ServerReachability.saveAddress(this, normalized)
        serverStatus.text = "Проверяем связь с сервером..."
        serverExecutor.execute {
            val result = ServerReachability.probe(normalized)
            runOnUiThread {
                if (!isDestroyed) serverStatus.text = result.message
            }
        }
    }

    private fun startContinuousConversation() {
        if (continuousActive) return
        if (voiceBusy) {
            voiceStatus.text = "Дождитесь текущего ответа, затем включите разговор."
            return
        }
        if (AndroidVoiceCredentials.read(this).isNullOrBlank()) {
            voiceStatus.text = "Сначала сохраните ограниченный Android-ключ разговора."
            return
        }
        if (ServerReachability.address(this).isBlank()) {
            voiceStatus.text = "Сначала сохраните HTTPS-адрес сервера Лешего."
            return
        }
        val consent = getSharedPreferences("android_voice_privacy", MODE_PRIVATE)
        if (!consent.getBoolean("system_asr_agreed", false)) {
            AlertDialog.Builder(this)
                .setTitle("Разрешить распознавание речи?")
                .setMessage(
                    "Сначала используется распознавание на самом телефоне, если оно доступно. " +
                        "В противном случае системная служба Android может отправлять звук " +
                        "своему поставщику для распознавания. На сервер Лешего уходит только " +
                        "распознанный текст по HTTPS. Разрешить использование распознавания?"
                )
                .setNegativeButton("Не разрешаю") { _, _ ->
                    voiceStatus.text = "Без разрешения доступен только текстовый разговор."
                }
                .setPositiveButton("Разрешить") { _, _ ->
                    consent.edit().putBoolean("system_asr_agreed", true).apply()
                    startContinuousConversation()
                }
                .show()
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            continuousMicPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        // Старый фоновый PCM-сервис не должен одновременно занимать микрофон.
        if (ListeningService.isListening) stopListening()
        continuousActive = true
        // Предотвращаем автоматическое засыпание экрана во время открытого разговора.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val epoch = ++continuousEpoch
        dialogueButton.text = "Завершить разговор"
        val recognizer = ContinuousSpeechRecognizer(
            this,
            onState = { state ->
                if (continuousActive && epoch == continuousEpoch) voiceStatus.text = state
            },
            onUtterance = { phrase ->
                if (continuousActive && epoch == continuousEpoch) submitVoiceQuestion(phrase)
            },
            onStopped = {
                if (continuousActive && epoch == continuousEpoch) {
                    stopContinuousConversation("Распознавание остановлено системой Android.")
                }
            }
        )
        continuousRecognizer = recognizer
        if (!recognizer.start()) {
            stopContinuousConversation("Не удалось начать разговор. Попробуйте текстовый ввод.")
        }
    }

    private fun stopContinuousConversation(message: String) {
        if (!continuousActive && continuousRecognizer == null) return
        continuousActive = false
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        continuousEpoch++
        ++voiceRequestEpoch
        activeVoiceRequest?.cancel()
        activeVoiceRequest = null
        continuousRecognizer?.stop()
        continuousRecognizer = null
        pendingUtteranceId = null
        speaker?.stop()
        voiceBusy = false
        dialogueButton.text = "Начать разговор с Лешим"
        voiceStatus.text = message
    }

    private fun checkVoiceConnection() {
        val token = AndroidVoiceCredentials.read(this)
        val address = ServerReachability.address(this)
        if (token.isNullOrBlank() || address.isBlank()) {
            voiceStatus.text = "Сохраните адрес HTTPS и ключ Android-разговора."
            return
        }
        voiceStatus.text = "Проверяю авторизацию Android..."
        serverExecutor.execute {
            val state = try {
                if (AndroidVoiceApi.status(address, token)) {
                    "Android авторизован. Можно разговаривать с Лешим."
                } else "Сервер не подтвердил доступ к разговору."
            } catch (e: Exception) {
                "Ошибка подключения: " + (e.message ?: e.javaClass.simpleName)
            }
            runOnUiThread { if (!isDestroyed) voiceStatus.text = state }
        }
    }

    private fun submitVoiceQuestion(raw: String) {
        if (voiceBusy) {
            voiceStatus.text = "Дождитесь текущего ответа Лешего."
            return
        }
        val question = raw.trim()
        if (question.isEmpty() || question.length > 2500) {
            voiceStatus.text = "Введите вопрос длиной до 2500 символов."
            if (continuousActive) continuousRecognizer?.resumeAfterAnswer()
            return
        }
        val token = AndroidVoiceCredentials.read(this)
        val address = ServerReachability.address(this)
        if (token.isNullOrBlank() || address.isBlank()) {
            voiceStatus.text = "Нужны сохранённые HTTPS-адрес и ограниченный Android-ключ."
            if (continuousActive) stopContinuousConversation("Нет доступа к серверу.")
            return
        }
        val wasContinuous = continuousActive
        val activeEpoch = continuousEpoch
        if (wasContinuous) continuousRecognizer?.pauseForAnswer()
        val requestId = ++voiceRequestEpoch
        val handle = AndroidVoiceApi.RequestHandle()
        activeVoiceRequest = handle
        voiceBusy = true
        voiceStatus.text = "Вы: " + question + "\nЛеший думает..."
        val sid = voiceSession
        voiceExecutor.execute {
            try {
                val result = AndroidVoiceApi.ask(address, token, question, sid, handle)
                runOnUiThread {
                    if (isDestroyed || requestId != voiceRequestEpoch ||
                        (wasContinuous && (!continuousActive || activeEpoch != continuousEpoch))) {
                        return@runOnUiThread
                    }
                    voiceSession = result.sessionId
                    getSharedPreferences("android_voice_session", MODE_PRIVATE).edit()
                        .putString("session_id", result.sessionId).apply()
                    voiceStatus.text = "Вы: " + question + "\nЛеший: " + result.text
                    voiceQuestion.setText("")
                    if (speakerReady) {
                        val speechId = "leshy-$requestId-$activeEpoch"
                        if (wasContinuous) pendingUtteranceId = speechId
                        val started = speaker?.speak(
                            result.text.take(TextToSpeech.getMaxSpeechInputLength() - 1),
                            TextToSpeech.QUEUE_FLUSH, null, speechId
                        ) ?: TextToSpeech.ERROR
                        if (started == TextToSpeech.ERROR && wasContinuous) {
                            pendingUtteranceId = null
                            continuousRecognizer?.resumeAfterAnswer()
                        }
                    } else {
                        voiceStatus.append("\nОзвучка TTS недоступна; ответ показан текстом.")
                        if (wasContinuous) continuousRecognizer?.resumeAfterAnswer()
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    if (!isDestroyed && requestId == voiceRequestEpoch &&
                        (!wasContinuous || (continuousActive && activeEpoch == continuousEpoch))) {
                        voiceStatus.text = "Не удалось получить ответ: " +
                            (e.message ?: e.javaClass.simpleName)
                        if (wasContinuous) continuousRecognizer?.resumeAfterAnswer()
                    }
                }
            } finally {
                handle.release()
                runOnUiThread {
                    if (requestId == voiceRequestEpoch) {
                        voiceBusy = false
                        if (activeVoiceRequest === handle) activeVoiceRequest = null
                    }
                }
            }
        }
    }

    private fun deviceMessage(identity: DeviceIdentity.PublicIdentity?): String =
        if (identity == null) {
            "Ключ ещё не создан. Серверная привязка не выполнена."
        } else {
            "Локальный ключ подготовлен. SHA-256 отпечаток открытого ключа:\n" +
                identity.fingerprintSha256.chunked(8).joinToString(" ") +
                "\nСерверная привязка НЕ выполнена. Доступ к данным владельца не выдан."
        }

    private fun refreshDeviceIdentity() {
        deviceExecutor.execute {
            val message = try {
                deviceMessage(DeviceIdentity.existing())
            } catch (_: Exception) {
                "Не удалось прочитать ключ Android Keystore."
            }
            runOnUiThread {
                if (!isDestroyed && !isFinishing) deviceStatus.text = message
            }
        }
    }

    private fun createDeviceIdentity() {
        deviceCreateButton.isEnabled = false
        deviceStatus.text = "Готовим ключ в Android Keystore..."
        deviceExecutor.execute {
            val message = try {
                deviceMessage(DeviceIdentity.createIfMissing())
            } catch (_: Exception) {
                "Не удалось создать ключ устройства. Серверная привязка не выполнена."
            }
            runOnUiThread {
                if (!isDestroyed && !isFinishing) {
                    deviceStatus.text = message
                    deviceCreateButton.isEnabled = true
                }
            }
        }
    }

    private fun deleteDeviceIdentity() {
        deviceCreateButton.isEnabled = false
        deviceExecutor.execute {
            val message = try {
                DeviceIdentity.deleteLocalKey()
                deviceMessage(null)
            } catch (_: Exception) {
                "Не удалось удалить локальный ключ."
            }
            runOnUiThread {
                if (!isDestroyed && !isFinishing) {
                    deviceStatus.text = message
                    deviceCreateButton.isEnabled = true
                }
            }
        }
    }

    private fun refreshMicStatus() {
        val enabled = getSharedPreferences(
            ListeningService.PREFS, MODE_PRIVATE
        ).getBoolean(ListeningService.KEY_USER_ENABLED, false)
        micStatus.text = when {
            ListeningService.isListening -> "Микрофон активен. CPU поддерживается включённым; экран можно погасить."
            enabled -> "Был включён ранее; если сервис остановлен системой, нажмите запуск."
            else -> "Микрофон выключен"
        }
        micButton.text = if (ListeningService.isListening) "Остановить фоновый микрофон" else "Включить фоновый микрофон (без диалога)"
    }

    private fun startListening() {
        getSharedPreferences(ListeningService.PREFS, MODE_PRIVATE)
            .edit().putBoolean(ListeningService.KEY_USER_ENABLED, true).apply()
        try {
            ContextCompat.startForegroundService(this, Intent(this, ListeningService::class.java))
            micStatus.text = "Запрос на запуск отправлен (состояние подтвердится при возвращении)"
            micButton.text = "Остановить прослушивание"
            if (android.os.Build.VERSION.SDK_INT >= 33 &&
                ContextCompat.checkSelfPermission(
                    this, Manifest.permission.POST_NOTIFICATIONS
                ) != PackageManager.PERMISSION_GRANTED) {
                notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        } catch (_: RuntimeException) {
            getSharedPreferences(ListeningService.PREFS, MODE_PRIVATE)
                .edit().putBoolean(ListeningService.KEY_USER_ENABLED, false).apply()
            micStatus.text = "Android запретил запуск: откройте настройки приложения и повторите"
        }
    }

    private fun stopListening() {
        getSharedPreferences(ListeningService.PREFS, MODE_PRIVATE)
            .edit().putBoolean(ListeningService.KEY_USER_ENABLED, false).apply()
        stopService(Intent(this, ListeningService::class.java))
        refreshMicStatus()
    }

    private fun startCamera() {
        cameraPreview.visibility = View.VISIBLE
        cameraEnabled = true
        cameraButton.text = "Выключить камеру"
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            if (!cameraEnabled || isDestroyed) return@addListener
            try {
                val provider = future.get()
                cameraProvider = provider
                val preview = Preview.Builder().build().also {
                    it.surfaceProvider = cameraPreview.surfaceProvider
                }
                val analysis = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(cameraExecutor) { frame ->
                    val image = frame.image
                    if (image == null) {
                        frame.close()
                        return@setAnalyzer
                    }
                    try {
                        val input = InputImage.fromMediaImage(
                            image, frame.imageInfo.rotationDegrees
                        )
                        detector.process(input)
                            .addOnSuccessListener { faces ->
                                runOnUiThread {
                                    if (cameraEnabled) {
                                        faceStatus.text = "Лиц в кадре: ${faces.size}. " +
                                            "Идентификация пользователей ещё не подключена."
                                    }
                                }
                            }
                            .addOnFailureListener { error ->
                                runOnUiThread {
                                    faceStatus.text = "Ошибка обнаружения лица: ${error.javaClass.simpleName}"
                                }
                            }
                            .addOnCompleteListener { frame.close() }
                    } catch (_: RuntimeException) {
                        frame.close()
                    }
                }
                provider.unbindAll()
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_FRONT_CAMERA, preview, analysis)
            } catch (_: Exception) {
                faceStatus.text = "Не удалось запустить камеру"
                stopCamera()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun stopCamera() {
        cameraEnabled = false
        cameraProvider?.unbindAll()
        cameraPreview.visibility = View.GONE
        cameraButton.text = "Включить камеру и обнаружение лиц"
        faceStatus.text = "Камера выключена; личность не определена"
    }

    override fun onStop() {
        // При сворачивании приложения Android не гарантирует работу распознавателя.
        if (continuousActive) {
            stopContinuousConversation("Разговор остановлен: приложение свернуто или экран заблокирован.")
        }
        super.onStop()
    }

    override fun onDestroy() {
        if (continuousActive) stopContinuousConversation("Разговор завершён.")
        stopCamera()
        detector.close()
        cameraExecutor.shutdown()
        serverExecutor.shutdownNow()
        voiceExecutor.shutdownNow()
        deviceExecutor.shutdownNow()
        speaker?.stop()
        speaker?.shutdown()
        speaker = null
        super.onDestroy()
    }
}
