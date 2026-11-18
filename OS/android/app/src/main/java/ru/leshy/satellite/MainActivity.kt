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
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
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

    // The mobile app's primary task is conversation. Diagnostic actions belong
    // to Settings, not a permanently visible list of default Android buttons.
    private val night = Color.rgb(9, 17, 25)
    private val surface = Color.rgb(19, 34, 42)
    private val surfaceRaised = Color.rgb(27, 47, 55)
    private val mint = Color.rgb(122, 233, 190)
    private val pale = Color.rgb(238, 248, 246)
    private val muted = Color.rgb(157, 183, 181)

    private fun px(dp: Int): Int = (dp * resources.displayMetrics.density + 0.5f).toInt()

    private fun shape(
        fill: Int, radius: Int = 22, border: Int? = null
    ): GradientDrawable = GradientDrawable().apply {
        shape = GradientDrawable.RECTANGLE
        setColor(fill)
        cornerRadius = px(radius).toFloat()
        if (border != null) setStroke(px(1), border)
    }

    private fun panel(): LinearLayout = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(px(20), px(20), px(20), px(20))
        background = shape(surface, 25, surfaceRaised)
    }

    private fun heading(value: String, size: Float = 20f): TextView =
        TextView(this).apply {
            text = value
            textSize = size
            setTextColor(pale)
            typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        }

    private fun detail(value: String, size: Float = 14f): TextView =
        TextView(this).apply {
            text = value
            textSize = size
            setTextColor(muted)
            setLineSpacing(px(3).toFloat(), 1f)
        }

    private fun gap(height: Int): View = View(this).apply {
        layoutParams = LinearLayout.LayoutParams(1, px(height))
    }

    private fun fieldStyle(field: EditText, minimum: Int = 50) {
        field.setTextColor(pale)
        field.setHintTextColor(muted)
        field.textSize = 15f
        field.setPadding(px(15), px(10), px(15), px(10))
        field.background = shape(surfaceRaised, 16)
        field.minHeight = px(minimum)
    }

    private fun controlStyle(button: Button, prominent: Boolean = false) {
        button.isAllCaps = false
        button.textSize = if (prominent) 17f else 14f
        button.typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        button.setTextColor(if (prominent) night else pale)
        button.background = shape(if (prominent) mint else surfaceRaised, 18)
        button.minHeight = px(if (prominent) 62 else 48)
        button.setPadding(px(14), px(10), px(14), px(10))
        button.elevation = px(if (prominent) 4 else 0).toFloat()
    }

    private fun refreshTalkStyle() {
        if (!::dialogueButton.isInitialized) return
        dialogueButton.text = if (continuousActive) "■  Завершить разговор" else "●  Начать разговор"
        dialogueButton.background = shape(if (continuousActive) Color.rgb(237, 161, 133) else mint, 22)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = night
        window.navigationBarColor = night
        window.decorView.systemUiVisibility = 0

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(20), px(24), px(20), px(36))
            setBackgroundColor(night)
        }
        val top = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.ic_leshy_brand)
            scaleType = ImageView.ScaleType.CENTER_CROP
            background = shape(surfaceRaised, 15)
            clipToOutline = true
        }
        top.addView(logo, LinearLayout.LayoutParams(px(52), px(52)))
        val titleGroup = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(12), 0, 0, 0)
        }
        titleGroup.addView(heading("Леший", 23f))
        titleGroup.addView(detail("Ваш личный ИИ · Android", 12f))
        top.addView(titleGroup, LinearLayout.LayoutParams(0, -2, 1f))
        val settingsButton = Button(this).apply {
            text = "⚙"
            contentDescription = "Открыть настройки и подключение"
            textSize = 24f
            controlStyle(this)
            setOnClickListener { showSettings() }
        }
        top.addView(settingsButton, LinearLayout.LayoutParams(px(55), px(52)))
        root.addView(top)
        root.addView(gap(30))
        root.addView(heading("На связи с Вами", 29f))
        root.addView(gap(6))
        root.addView(detail("Говорите свободно. Леший продолжит диалог, пока Вы не остановите его.", 15f))
        root.addView(gap(23))

        val statusPanel = panel()
        val statusHeading = heading("СОЕДИНЕНИЕ", 12f).apply {
            setTextColor(mint)
            letterSpacing = 0.14f
        }
        statusPanel.addView(statusHeading)
        statusPanel.addView(gap(9))
        serverStatus = detail("Сервер ещё не настроен", 15f)
        statusPanel.addView(serverStatus)
        val connectionAction = Button(this).apply {
            text = "Настроить подключение"
            controlStyle(this)
            setOnClickListener { showSettings() }
        }
        statusPanel.addView(gap(12))
        statusPanel.addView(connectionAction)
        root.addView(statusPanel)
        root.addView(gap(17))

        val talkPanel = panel()
        talkPanel.addView(heading("Разговор", 22f))
        talkPanel.addView(gap(8))
        voiceStatus = detail(
            "Нажмите «Начать разговор». Если телефон ещё не подключён, откройте настройки.",
            15f
        )
        talkPanel.addView(voiceStatus)
        talkPanel.addView(gap(21))
        dialogueButton = Button(this).apply {
            controlStyle(this, prominent = true)
            setOnClickListener {
                if (continuousActive) stopContinuousConversation("Разговор завершён.")
                else startContinuousConversation()
            }
        }
        refreshTalkStyle()
        talkPanel.addView(dialogueButton, LinearLayout.LayoutParams(-1, px(74)))
        talkPanel.addView(gap(12))
        talkPanel.addView(detail("Микрофон работает только во время разговора. Нажмите ещё раз, чтобы закончить.", 12f))
        root.addView(talkPanel)
        root.addView(gap(17))

        val chatPanel = panel()
        chatPanel.addView(heading("Написать Лешему", 18f))
        chatPanel.addView(gap(12))
        voiceQuestion = EditText(this).apply {
            hint = "Спросите о чём-нибудь..."
            setSingleLine(false)
            minLines = 2
            maxLines = 5
            fieldStyle(this, 84)
        }
        chatPanel.addView(voiceQuestion, LinearLayout.LayoutParams(-1, -2))
        chatPanel.addView(gap(10))
        val sendButton = Button(this).apply {
            text = "Отправить сообщение"
            controlStyle(this)
            setOnClickListener { submitVoiceQuestion(voiceQuestion.text.toString()) }
        }
        chatPanel.addView(sendButton)
        val resetButton = Button(this).apply {
            text = "Новый диалог"
            controlStyle(this)
            setOnClickListener {
                voiceSession = null
                getSharedPreferences("android_voice_session", MODE_PRIVATE).edit().clear().apply()
                voiceStatus.text = "Новый разговор. Можете задать вопрос."
            }
        }
        chatPanel.addView(gap(8))
        chatPanel.addView(resetButton)
        root.addView(chatPanel)
        root.addView(gap(17))

        val cameraPanel = panel()
        cameraPanel.addView(heading("Зрение", 18f))
        cameraPanel.addView(gap(7))
        cameraPanel.addView(detail("Камера включается только по Вашему запросу. Пока Леший обнаруживает лица, но не узнаёт людей.", 13f))
        cameraPanel.addView(gap(12))
        cameraButton = Button(this).apply {
            text = "Включить камеру"
            controlStyle(this)
            setOnClickListener {
                if (cameraEnabled) stopCamera()
                else if (ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.CAMERA
                ) == PackageManager.PERMISSION_GRANTED) startCamera()
                else cameraPermission.launch(Manifest.permission.CAMERA)
            }
        }
        cameraPanel.addView(cameraButton)
        faceStatus = detail("Камера выключена", 13f)
        cameraPanel.addView(gap(8))
        cameraPanel.addView(faceStatus)
        cameraPreview = PreviewView(this).apply {
            visibility = View.GONE
        }
        cameraPanel.addView(cameraPreview, LinearLayout.LayoutParams(-1, px(270)))
        root.addView(cameraPanel)
        root.addView(gap(16))
        root.addView(detail("Личный помощник · Данные и разрешения находятся под Вашим контролем.", 12f))

        // These controls exist even while Settings is closed: background
        // callbacks update them without accidentally dereferencing null.
        serverAddress = EditText(this).apply {
            setSingleLine(true)
            setText(ServerReachability.address(this@MainActivity))
        }
        voiceSecret = EditText(this)
        micStatus = detail("Микрофон выключен")
        micButton = Button(this).apply {
            setOnClickListener {
                if (continuousActive) {
                    stopContinuousConversation("Разговор остановлен: выбран фоновый микрофон.")
                }
                if (ListeningService.isListening) stopListening()
                else if (ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED) startListening()
                else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
        deviceStatus = detail("Загрузка данных устройства...")
        deviceCreateButton = Button(this).apply {
            text = "Создать локальный ключ устройства"
            setOnClickListener { createDeviceIdentity() }
        }

        val scroll = ScrollView(this).apply {
            fillViewport = true
            isVerticalScrollBarEnabled = false
            addView(root)
        }
        setContentView(scroll)
        refreshMicStatus()
        refreshDeviceIdentity()
        checkSavedServer()
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

    private fun showSettings() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(px(20), px(14), px(20), px(20))
            setBackgroundColor(night)
        }
        root.addView(heading("Подключение", 22f))
        root.addView(gap(9))
        root.addView(detail("Укажите HTTPS-адрес Вашего «Лешего». Проверка связи сама по себе не выдаёт права.", 14f))
        root.addView(gap(12))
        serverAddress = EditText(this).apply {
            hint = "https://leshy.example:443"
            setSingleLine(true)
            setText(ServerReachability.address(this@MainActivity))
            fieldStyle(this)
        }
        root.addView(serverAddress)
        root.addView(gap(10))
        root.addView(Button(this).apply {
            text = "Сохранить и проверить сервер"
            controlStyle(this, prominent = true)
            setOnClickListener { checkServer() }
        })
        root.addView(gap(26))
        root.addView(heading("Доступ к разговору", 18f))
        root.addView(gap(7))
        root.addView(detail(
            "Текущая серверная версия ещё не поддерживает подтверждение телефона автоматически. " +
                "Ручной ограниченный ключ оставлен как временный способ подключения. " +
                "Не вводите сюда административный токен.",
            13f
        ))
        root.addView(gap(10))
        voiceSecret = EditText(this).apply {
            hint = "Временный ключ Android-разговора"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
            fieldStyle(this)
        }
        root.addView(voiceSecret)
        root.addView(gap(10))
        root.addView(Button(this).apply {
            text = "Сохранить ограниченный ключ"
            controlStyle(this)
            setOnClickListener {
                val value = voiceSecret.text.toString().trim()
                try {
                    AndroidVoiceCredentials.save(this@MainActivity, value)
                    voiceSecret.setText("")
                    voiceSession = null
                    getSharedPreferences("android_voice_session", MODE_PRIVATE).edit().clear().apply()
                    voiceStatus.text = "Ключ сохранён в Android Keystore. Теперь проверьте доступ."
                } catch (_: Exception) {
                    voiceStatus.text = "Ключ не сохранён. Нужен отдельный Android-ключ, а не токен администратора."
                }
            }
        })
        root.addView(gap(8))
        root.addView(Button(this).apply {
            text = "Проверить доступ к разговору"
            controlStyle(this)
            setOnClickListener { checkVoiceConnection() }
        })
        root.addView(gap(24))
        // A dialog can be reopened: each Settings instance owns fresh views.
        // Re-attaching controls from a dismissed dialog would crash Android.
        micStatus = detail("Микрофон выключен")
        micButton = Button(this).apply {
            setOnClickListener {
                if (continuousActive) {
                    stopContinuousConversation("Разговор остановлен: выбран фоновый микрофон.")
                }
                if (ListeningService.isListening) stopListening()
                else if (ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.RECORD_AUDIO
                ) == PackageManager.PERMISSION_GRANTED) startListening()
                else microphonePermission.launch(Manifest.permission.RECORD_AUDIO)
            }
        }
        deviceStatus = detail("Загрузка данных устройства...")
        deviceCreateButton = Button(this).apply {
            text = "Создать локальный ключ устройства"
            setOnClickListener { createDeviceIdentity() }
        }
        root.addView(heading("Разрешения и устройство", 18f))
        root.addView(gap(12))
        root.addView(micStatus)
        controlStyle(micButton)
        root.addView(gap(8))
        root.addView(micButton)
        root.addView(gap(13))
        root.addView(Button(this).apply {
            text = "Отозвать согласие на распознавание речи"
            controlStyle(this)
            setOnClickListener {
                if (continuousActive) stopContinuousConversation("Разговор завершён.")
                getSharedPreferences("android_voice_privacy", MODE_PRIVATE)
                    .edit().putBoolean("system_asr_agreed", false).apply()
                voiceStatus.text = "Согласие отозвано. Голосовое распознавание выключено."
            }
        })
        root.addView(gap(8))
        root.addView(Button(this).apply {
            text = "Настройки энергосбережения"
            controlStyle(this)
            setOnClickListener {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        })
        root.addView(gap(22))
        root.addView(heading("Ключ этого телефона", 18f))
        root.addView(gap(8))
        root.addView(detail(
            "Ключ устройства пока не даёт доступа к серверу; для привязки потребуется подтверждение владельца.",
            13f
        ))
        root.addView(gap(9))
        root.addView(deviceStatus)
        controlStyle(deviceCreateButton)
        root.addView(gap(8))
        root.addView(deviceCreateButton)
        root.addView(gap(8))
        root.addView(Button(this).apply {
            text = "Удалить локальный ключ устройства"
            controlStyle(this)
            setOnClickListener {
                AlertDialog.Builder(this@MainActivity)
                    .setTitle("Удалить локальный ключ?")
                    .setMessage("Удаление необратимо. Если устройство было привязано к серверу, отдельно отзовите доступ на сервере.")
                    .setNegativeButton("Отмена", null)
                    .setPositiveButton("Удалить") { _, _ -> deleteDeviceIdentity() }
                    .show()
            }
        })
        val scroll = ScrollView(this).apply {
            isVerticalScrollBarEnabled = false
            addView(root)
        }
        AlertDialog.Builder(this)
            .setView(scroll)
            .setPositiveButton("Готово", null)
            .show()
            .window?.setBackgroundDrawableResource(android.R.color.transparent)
        refreshDeviceIdentity()
        refreshMicStatus()
    }

    private fun checkSavedServer() {
        val address = ServerReachability.address(this)
        if (address.isBlank()) {
            serverStatus.text = "Нет подключения. Настройте адрес сервера."
            return
        }
        serverStatus.text = "Проверяем соединение..."
        serverExecutor.execute {
            val result = ServerReachability.probe(address)
            runOnUiThread { if (!isDestroyed) serverStatus.text = result.message }
        }
    }

    override fun onResume() {
        super.onResume()
        if (::micStatus.isInitialized) refreshMicStatus()
        if (::serverStatus.isInitialized) checkSavedServer()
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
            voiceStatus.text = "Телефон не подключён для разговора. Откройте настройки подключения."
            return
        }
        if (ServerReachability.address(this).isBlank()) {
            voiceStatus.text = "Не указан сервер Лешего. Откройте настройки подключения."
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
        refreshTalkStyle()
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
        refreshTalkStyle()
        voiceStatus.text = message
    }

    private fun checkVoiceConnection() {
        val token = AndroidVoiceCredentials.read(this)
        val address = ServerReachability.address(this)
        if (token.isNullOrBlank() || address.isBlank()) {
            voiceStatus.text = "Откройте настройки: нужны HTTPS-адрес и доступ к разговору."
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
            voiceStatus.text = "Телефон не подключён. Откройте настройки."
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
        cameraButton.text = "Включить камеру"
        faceStatus.text = "Камера выключена"
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
