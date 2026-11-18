package ru.leshy.satellite

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.IBinder
import android.os.PowerManager
import androidx.core.content.ContextCompat
import java.util.concurrent.atomic.AtomicBoolean

/**
 * First stage: keep a user-started microphone foreground service alive with screen off.
 * Audio stays in RAM and is discarded. NO wake-word/ASR, storage or upload yet.
 * Android/OEM may still stop the service; START_STICKY is not a guarantee.
 */
class ListeningService : Service() {
    companion object {
        const val ACTION_STOP = "ru.leshy.satellite.STOP_LISTENING"
        const val PREFS = "satellite_local"
        const val KEY_USER_ENABLED = "user_enabled"
        private const val CHANNEL = "leshy_listening"
        private const val NOTIFICATION_ID = 40
        @Volatile var isListening: Boolean = false
            private set
    }

    private val capture = AtomicBoolean(false)
    @Volatile private var recorder: AudioRecord? = null
    private var captureThread: Thread? = null
    private var cpuLock: PowerManager.WakeLock? = null
    private var connectivityNotifier: ConnectivityNotifier? = null
    @Volatile private var internetConnected: Boolean? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL, "Леший: прослушивание", NotificationManager.IMPORTANCE_LOW)
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putBoolean(KEY_USER_ENABLED, false).apply()
            stopSelf()
            return START_NOT_STICKY
        }
        // Recreate only if user enabled the feature before process death.
        if (!getSharedPreferences(PREFS, MODE_PRIVATE).getBoolean(KEY_USER_ENABLED, false)) {
            stopSelf()
            return START_NOT_STICKY
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) {
            stopSelf()
            return START_NOT_STICKY
        }

        try {
            startForeground(
                NOTIFICATION_ID,
                listeningNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
            if (!capture.get()) beginCapture()
            if (connectivityNotifier == null && capture.get()) {
                connectivityNotifier = ConnectivityNotifier(this) { online ->
                    internetConnected = online
                    getSystemService(NotificationManager::class.java)
                        .notify(NOTIFICATION_ID, listeningNotification())
                }
            }
        } catch (_: SecurityException) {
            // Restart from background can be forbidden by Android 14+.
            stopSelf()
            return START_NOT_STICKY
        } catch (_: RuntimeException) {
            stopSelf()
            return START_NOT_STICKY
        }
        return if (capture.get()) START_STICKY else START_NOT_STICKY
    }

    private fun listeningNotification(): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, ListeningService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Леший: микрофон активен")
            .setContentText(
                when (internetConnected) {
                    false -> "Нет подключения к интернету"
                    true -> "Микрофон активен. Серверное соединение пока не подключено."
                    null -> "Микрофон активен. Проверка сети..."
                }
            )
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Остановить", stop).build())
            .build()
    }

    private fun beginCapture() {
        val sampleRate = 16000
        val channel = AudioFormat.CHANNEL_IN_MONO
        val encoding = AudioFormat.ENCODING_PCM_16BIT
        val minimum = AudioRecord.getMinBufferSize(sampleRate, channel, encoding)
        if (minimum <= 0) {
            stopSelf()
            return
        }
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            sampleRate, channel, encoding, minimum * 2
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            stopSelf()
            return
        }
        recorder = record
        capture.set(true)
        isListening = true

        // Screen can turn off while the CPU continues capturing locally.
        val power = getSystemService(POWER_SERVICE) as PowerManager
        cpuLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Leshy:Listening")
            .also { it.setReferenceCounted(false); it.acquire() }

        captureThread = Thread({
            try {
                record.startRecording()
                val buffer = ShortArray(minimum / 2)
                while (capture.get()) {
                    val read = record.read(buffer, 0, buffer.size)
                    if (read < 0) break
                    // No raw audio leaves the device in this foundation stage.
                }
            } catch (_: SecurityException) {
                // Permission revoked; terminate rather than silently recovering access.
            } catch (_: IllegalStateException) {
                // Audio device unavailable.
            } finally {
                capture.set(false)
                isListening = false
                try { record.stop() } catch (_: IllegalStateException) { }
                record.release()
                if (recorder === record) recorder = null
                stopSelf()
            }
        }, "LeshyAudioCapture").also { it.start() }
    }

    override fun onDestroy() {
        connectivityNotifier?.close()
        connectivityNotifier = null
        capture.set(false)
        isListening = false
        try { recorder?.stop() } catch (_: IllegalStateException) { }
        cpuLock?.let { if (it.isHeld) it.release() }
        cpuLock = null
        super.onDestroy()
    }
}
