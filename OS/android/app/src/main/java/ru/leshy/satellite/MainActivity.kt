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
import android.view.View
import android.widget.Button
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
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(28, 28, 28, 28)
        }
        root.addView(TextView(this).apply {
            text = "Леший • Android Satellite"
            textSize = 23f
        })
        root.addView(TextView(this).apply {
            text = "Одна кнопка включает разговор с Лешим на несколько реплик; второе нажатие завершает сеанс."
            textSize = 14f
        })
        root.addView(TextView(this).apply {
            text = "Адрес сервера (HTTPS). Проверка связи не даёт прав владельца."
        })
        serverAddress = EditText(this).apply {
            hint = "https://server.example:443"
            setSingleLine(true)
            setText(ServerReachability.address(this@MainActivity))
        }
        root.addView(serverAddress)
        serverStatus = TextView(this).apply {
            text = "Связь с сервером ещё не проверялась"
        }
        root.addView(serverStatus)
        root.addView(Button(this).apply {
            text = "Сохранить адрес и проверить"
            setOnClickListener { checkServer() }
        })

        root.addView(TextView(this).apply {
            text = "Непрерывный разговор: один раз начать, один раз закончить. Между репликами нажимать не нужно."
            textSize = 16f
        })
        voiceStatus = TextView(this).apply {
            text = "Для первого подключения нужен ограниченный Android-ключ, созданный на сервере."
        }
        root.addView(voiceStatus)
        voiceSecret = EditText(this).apply {
            hint = "Android voice key (не административный API-токен)"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
        }
        root.addView(voiceSecret)
        root.addView(Button(this).apply {
            text = "Сохранить ключ разговора"
            setOnClickListener {
                val value = voiceSecret.text.toString().trim()
                try {
                    AndroidVoiceCredentials.save(this@MainActivity, value)
                    voiceSecret.setText("")
                    voiceSession = null
                    getSharedPreferences("android_voice_session", MODE_PRIVATE).edit().clear().apply()
                    voiceStatus.text = "Ключ сохранён в Android Keystore. Проверьте соединение."
                } catch (_: Exception) {
                    voiceStatus.text = "Ключ не сохранён. Введите выданный сервером Android-ключ."
                }
            }
        })
        root.addView(Button(this).apply {
            text = "Проверить доступ к разговору"
            setOnClickListener { checkVoiceConnection() }
        })
        dialogueButton = Button(this).apply {
            text = "Начать разговор с Лешим"
            textSize = 18f
            setOnClickListener {
                if (continuousActive) stopContinuousConversation("Разговор завершён.")
                else startContinuousConversation()
            }
        }
        root.addView(dialogueButton)
        root.addView(Button(this).apply {
            text = "Отозвать согласие на распознавание речи"
            setOnClickListener {
                if (continuousActive) stopContinuousConversation("Разговор завершён.")
                getSharedPreferences("android_voice_privacy", MODE_PRIVATE)
                    .edit().putBoolean("system_asr_agreed", false).apply()
                voiceStatus.text = "Согласие отозвано. Распознавание будет выключено до нового разрешения."
            }
        })
        voiceQuestion = EditText(this).apply {
            hint = "Или введите вопрос текстом"
            setSingleLine(false)
            minLines = 2
        }
        root.addView(voiceQuestion)
        root.addView(Button(this).apply {
            text = "Отправить вопрос Лешему"
            setOnClickListener { submitVoiceQuestion(voiceQuestion.text.toString()) }
        })
        root.addView(Button(this).apply {
            text = "Новый разговор"
            setOnClickListener {
                voiceSession = null
                getSharedPreferences("android_voice_session", MODE_PRIVATE).edit().clear().apply()
                voiceStatus.text = "Новый разговор. Можете задать вопрос."
            }
        })
        root.addView(TextView(this).apply {
            text = "Привязка телефона: подготовка ключа устройства (без авторизации на сервере)"
        })
        deviceStatus = TextView(this).apply {
            text = "Состояние ключа загружается..."
        }
        root.addView(deviceStatus)
        deviceCreateButton = Button(this).apply {
            text = "Создать локальный ключ устройства"
            setOnClickListener { createDeviceIdentity() }
        }
        root.addView(deviceCreateButton)
        root.addView(Button(this).apply {
            text = "Удалить локальный ключ"
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
        })
        micStatus = TextView(this)
        root.addView(micStatus)
        micButton = Button(this).apply {
            setOnClickListener {
                if (continuousActive) {
                    stopContinuousConversation("Непрерывный диалог остановлен: выбран отдельный режим микрофона.")
                }
                if (ListeningService.isListening) stopListening()
                else if (ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED) startListening()
                else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
        root.addView(micButton)
        root.addView(Button(this).apply {
            text = "Настройки энергосбережения Android"
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        })
        root.addView(TextView(this).apply {
            text = "Камера работает только во время открытого экрана приложения."
        })
        cameraButton = Button(this).apply {
            text = "Включить камеру и обнаружение лиц"
            setOnClickListener {
                if (cameraEnabled) stopCamera()
                else if (ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.CAMERA
                ) == PackageManager.PERMISSION_GRANTED) startCamera()
                else cameraPermission.launch(Manifest.permission.CAMERA)
            }
        }
        root.addView(cameraButton)
        faceStatus = TextView(this).apply {
            text = "Камера выключена; личность не определена"
        }
        root.addView(faceStatus)
        cameraPreview = PreviewView(this).apply {
            visibility = View.GONE
        }
        root.addView(cameraPreview, LinearLayout.LayoutParams(-1, 600))
        root.addView(TextView(this).apply {
            text = "Один сеанс включает несколько реплик: речь распознаёт Android, " +
                "нейросеть отвечает по HTTPS, после окончания TTS прослушивание " +
                "возобновляется автоматически. Системная служба распознавания может " +
                "передавать аудио своему поставщику; Леший получает только текст. " +
                "Сеанс работает при открытом приложении, не в фоне."
        })
        setContentView(ScrollView(this).apply { addView(root) })
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
        if (AndroidVoiceCredentials.read(this).isNullOrBlank()) {
            voiceStatus.text = "Сначала сохраните ограниченный Android-ключ разговора."
            return
        }
        if (ServerReachability.address(this).isBlank()) {
            voiceStatus.text = "Сначала сохраните HTTPS-адрес сервера Лешего."
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
