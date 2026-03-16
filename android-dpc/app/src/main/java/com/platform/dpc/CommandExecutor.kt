package com.platform.dpc

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.graphics.drawable.Icon
import android.os.Build
import android.util.Log

/**
 * MDMコマンドの実行エンジン
 *
 * DPCがサーバーからコマンドを受け取った後、このクラスで実行する。
 * コマンド種別:
 *   add_webclip        - ホーム画面にピン留めショートカットを追加
 *   show_notification  - プッシュ通知を表示
 *   install_apk        - APKインストール（ダウンロード → インストール画面を開く）
 *   update_lockscreen  - ロック画面コンテンツを取得してSharedPreferencesに保存
 */
object CommandExecutor {

    private const val TAG = "CommandExecutor"
    private const val CHANNEL_ID = "mdm_notifications"

    fun execute(context: Context, command: MdmCommand): Boolean {
        Log.i(TAG, "Executing command: type=${command.type} id=${command.id}")
        return when (command.type) {
            "add_webclip"       -> addWebClip(context, command)
            "show_notification" -> showNotification(context, command)
            "install_apk"       -> installApk(context, command)
            "update_lockscreen" -> updateLockscreen(context, command)
            else -> {
                Log.w(TAG, "Unknown command type: ${command.type}")
                true  // 未知コマンドは無視してACK
            }
        }
    }

    // ── ホーム画面Webクリップ追加 ─────────────────────────────

    private fun addWebClip(context: Context, cmd: MdmCommand): Boolean {
        val url   = cmd.payload.optString("url").ifEmpty { return false }
        val label = cmd.payload.optString("label", "App")

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val shortcutManager = context.getSystemService(ShortcutManager::class.java)
            if (shortcutManager?.isRequestPinShortcutSupported == true) {
                val shortcut = ShortcutInfo.Builder(context, "webclip_${cmd.id}")
                    .setShortLabel(label)
                    .setLongLabel(label)
                    .setIcon(Icon.createWithResource(context, android.R.drawable.ic_menu_view))
                    .setIntent(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    .build()
                shortcutManager.requestPinShortcut(shortcut, null)
                Log.i(TAG, "WebClip shortcut requested: $label -> $url")
                return true
            }
        }

        // フォールバック: ブラウザで開く
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
        return true
    }

    // ── プッシュ通知表示 ──────────────────────────────────────

    private fun showNotification(context: Context, cmd: MdmCommand): Boolean {
        createNotificationChannel(context)

        val title = cmd.payload.optString("title", "お知らせ")
        val body  = cmd.payload.optString("body", "")
        val url   = cmd.payload.optString("url", "")

        val pendingIntent = if (url.isNotEmpty()) {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            android.app.PendingIntent.getActivity(
                context, 0, intent,
                android.app.PendingIntent.FLAG_IMMUTABLE,
            )
        } else null

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .apply { pendingIntent?.let { setContentIntent(it) } }
            .build()

        try {
            NotificationManagerCompat.from(context)
                .notify(cmd.id.hashCode(), notification)
            Log.i(TAG, "Notification shown: $title")
        } catch (e: SecurityException) {
            Log.w(TAG, "Notification permission not granted")
        }
        return true
    }

    // ── APKインストール ───────────────────────────────────────

    /**
     * APKをダウンロードしてインストール画面を開く。
     * ※ サイレントインストールはデバイスオーナー権限が必要。
     *   まずはユーザーにインストール画面を表示する方式で実装する。
     */
    private fun installApk(context: Context, cmd: MdmCommand): Boolean {
        val apkUrl = cmd.payload.optString("apk_url").ifEmpty { return false }
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl)).apply {
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
        Log.i(TAG, "APK install triggered: $apkUrl")
        return true
    }

    // ── ロック画面コンテンツ更新 ─────────────────────────────

    private fun updateLockscreen(context: Context, cmd: MdmCommand): Boolean {
        val prefs = context.getSharedPreferences("mdm_lockscreen", Context.MODE_PRIVATE)
        prefs.edit()
            .putString("title",   cmd.payload.optString("title"))
            .putString("cta_url", cmd.payload.optString("cta_url"))
            .putString("updated_at", System.currentTimeMillis().toString())
            .apply()
        Log.i(TAG, "Lockscreen content updated")
        return true
    }

    // ── ユーティリティ ────────────────────────────────────────

    private fun createNotificationChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "サービス通知",
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = "おすすめアプリやクーポン情報を受け取ります"
            }
            context.getSystemService(NotificationManager::class.java)
                ?.createNotificationChannel(channel)
        }
    }
}
