package ru.leshy.satellite

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
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

class MainActivity : ComponentActivity() {
    private lateinit var micButton: Button
    private lateinit var cameraButton: Button
    private lateinit var micStatus: TextView
    private lateinit var faceStatus: TextView
    private lateinit var cameraPreview: PreviewView
    private lateinit var serverAddress: EditText
    private lateinit var serverStatus: TextView
    private lateinit var deviceStatus: TextView
    private lateinit var deviceCreateButton: Button
    private val deviceExecutor = Executors.newSingleThreadExecutor()
    private val serverExecutor = Executors.newSingleThreadExecutor()

    private val cameraExecutor = Executors.newSingleThreadExecutor()
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
            text = "Прототип: микрофон работает локально. Серверный разговор и узнавание личности ещё не подключены."
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
            text = "Диалог с сервером пока не подключён; передаётся только запрос состояния /v1/status. " +
                "Голос и изображения не отправляются. Bluetooth-динамик необязателен."
        })
        setContentView(ScrollView(this).apply { addView(root) })
        refreshMicStatus()
        refreshDeviceIdentity()
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
        super.onDestroy()
    }
}
