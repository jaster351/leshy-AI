package ru.leshy.satellite

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat

/**
 * Android 14+ forbids launching a microphone FGS from BOOT_COMPLETED.
 * Ask the user to open the activity instead of claiming hidden auto-start.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        val enabled = context.getSharedPreferences(
            ListeningService.PREFS, Context.MODE_PRIVATE
        ).getBoolean(ListeningService.KEY_USER_ENABLED, false)
        if (!enabled) return
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) return

        val notifications = context.getSystemService(NotificationManager::class.java)
        val channel = "leshy_resume"
        notifications.createNotificationChannel(
            NotificationChannel(channel, "Леший: возобновление", NotificationManager.IMPORTANCE_DEFAULT)
        )
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        notifications.notify(
            41,
            Notification.Builder(context, channel)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle("Леший: возобновите прослушивание")
                .setContentText("После перезагрузки Android требует открыть приложение.")
                .setContentIntent(open)
                .setAutoCancel(true)
                .build()
        )
    }
}
