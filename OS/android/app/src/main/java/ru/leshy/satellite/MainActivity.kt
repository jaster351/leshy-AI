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

    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private val speechInput = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val words = result.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
            val question = words?.firstOrNull()?.trim().orEmpty()
            if (question.isNotEmpty()) submitVoiceQuestion(question)
            else if (::voiceStatus.isInitialized) voiceStatus.text = "Речь не распознана"
        } else if (::voiceStatus.isInitialized) {
            voiceStatus.text = "Распознавание отменено. Можно ввести вопрос текстом."
        }
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
            text = "Разговор с моделью Лешего доступен по кнопке. Узнавание людей и активация по слову ещё не подключены."
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
            text = "Разговор с Лешим (по нажатию). Используется нейросеть и Knowledge сервера."
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
        root.addView(Button(this).apply {
            text = "Говорить с Лешим"
            setOnClickListener { startVoiceRecognition() }
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
            text = "Кнопка разговора использует системное распознавание Android; " +
                "служба распознавания может отправлять аудио своему поставщику. " +
                "Серверу Лешего передаётся только распознанный текст по HTTPS. " +
                "Ответ произносится через системный TTS, Bluetooth-динамик необязателен."
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

    private fun startVoiceRecognition() {
        if (AndroidVoiceCredentials.read(this).isNullOrBlank()) {
            voiceStatus.text = "Сначала сохраните выданный сервером ключ разговора."
            return
        }
        if (ServerReachability.address(this).isBlank()) {
            voiceStatus.text = "Сначала укажите HTTPS-адрес сервера и сохраните его."
            return
        }
        try {
            val request = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
                putExtra(RecognizerIntent.EXTRA_PROMPT, "Говорите с Лешим")
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            }
            voiceStatus.text = "Слушаю вопрос..."
            speechInput.launch(request)
        } catch (_: ActivityNotFoundException) {
            voiceStatus.text = "Служба распознавания речи не установлена. Можно ввести вопрос текстом."
        } catch (_: Exception) {
            voiceStatus.text = "Не удалось запустить распознавание речи."
        }
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
            return
        }
        val token = AndroidVoiceCredentials.read(this)
        val address = ServerReachability.address(this)
        if (token.isNullOrBlank() || address.isBlank()) {
            voiceStatus.text = "Нужны сохранённые HTTPS-адрес и отдельный Android-ключ."
            return
        }
        voiceBusy = true
        voiceStatus.text = "Вы: " + question + "\nОжидаю ответ Лешего..."
        val activeSession = voiceSession
        serverExecutor.execute {
            try {
                val result = AndroidVoiceApi.ask(address, token, question, activeSession)
                runOnUiThread {
                    if (isDestroyed) return@runOnUiThread
                    voiceSession = result.sessionId
                    getSharedPreferences("android_voice_session", MODE_PRIVATE).edit()
                        .putString("session_id", result.sessionId).apply()
                    voiceStatus.text = "Вы: " + question + "\nЛеший: " + result.text
                    voiceQuestion.setText("")
                    if (speakerReady) {
                        speaker?.speak(
                            result.text.take(3900),
                            TextToSpeech.QUEUE_FLUSH, null, "leshy-android-answer"
                        )
                    } else {
                        voiceStatus.append("\nСистемный TTS недоступен; ответ показан текстом.")
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    if (!isDestroyed) voiceStatus.text =
                        "Не удалось получить ответ: " + (e.message ?: e.javaClass.simpleName)
                }
            } finally {
                runOnUiThread { voiceBusy = false }
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
        micButton.text = if (ListeningService.isListening) "Остановить прослушивание" else "Запустить прослушивание"
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

    override fun onDestroy() {
        stopCamera()
        detector.close()
        cameraExecutor.shutdown()
        serverExecutor.shutdownNow()
        deviceExecutor.shutdownNow()
        speaker?.stop()
        speaker?.shutdown()
        speaker = null
        super.onDestroy()
    }
}
