package com.platform.dpc

import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
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
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

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
            "play_game"         -> playGame(context, command)
            else -> {
                Log.w(TAG, "Unknown command type: ${command.type}")
                true  // 未知コマンドは無視してACK
            }
        }
    }

    // ── ホーム画面Webクリップ追加（DPC-10: Device Owner サイレント配置）─────────

    private fun addWebClip(context: Context, cmd: MdmCommand): Boolean {
        val url      = cmd.payload.optString("url").ifEmpty { return false }
        val label    = cmd.payload.optString("label", "App")
        val iconUrl  = cmd.payload.optString("icon_url").ifEmpty { null }

        // アイコンをダウンロード（オプション）
        val icon: Icon = if (iconUrl != null) {
            try {
                val client = OkHttpClient.Builder()
                    .connectTimeout(5, TimeUnit.SECONDS)
                    .readTimeout(10, TimeUnit.SECONDS)
                    .build()
                val response = client.newCall(
                    Request.Builder().url(iconUrl).get().build()
                ).execute()
                val bytes = response.body?.bytes()
                if (bytes != null) {
                    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    if (bmp != null) Icon.createWithBitmap(bmp)
                    else Icon.createWithResource(context, android.R.drawable.ic_menu_view)
                } else {
                    Icon.createWithResource(context, android.R.drawable.ic_menu_view)
                }
            } catch (e: Exception) {
                Log.w(TAG, "Icon download failed, using default: $e")
                Icon.createWithResource(context, android.R.drawable.ic_menu_view)
            }
        } else {
            Icon.createWithResource(context, android.R.drawable.ic_menu_view)
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val shortcutManager = context.getSystemService(ShortcutManager::class.java)
            if (shortcutManager != null) {
                val shortcutId = "webclip_${cmd.id}"
                val shortcut = ShortcutInfo.Builder(context, shortcutId)
                    .setShortLabel(label)
                    .setLongLabel(label)
                    .setIcon(icon)
                    .setIntent(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                    .build()

                // Device Owner: updateShortcuts() で確認ダイアログなしに更新可能
                // 新規ピン留めはrequestPinShortcut()が必要だが、
                // すでに存在するショートカットはDevice Owner権限でサイレント更新できる
                val existing = shortcutManager.pinnedShortcuts.any { it.id == shortcutId }
                if (existing) {
                    shortcutManager.updateShortcuts(listOf(shortcut))
                    Log.i(TAG, "Shortcut updated silently (Device Owner): $label -> $url")
                    return true
                }

                // 新規配置: requestPinShortcutでブランドダイアログ表示
                if (shortcutManager.isRequestPinShortcutSupported) {
                    shortcutManager.requestPinShortcut(shortcut, null)
                    Log.i(TAG, "WebClip pin shortcut requested: $label -> $url")
                    return true
                }
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
     * APKインストール（DPC-01: Device Owner権限でサイレント実行）。
     * キャッシュ済みAPKがあればPackageInstaller.Session APIでサイレントインストール。
     * キャッシュなしはバックグラウンドDLをキューイングし、完了後に再実行。
     */
    private fun installApk(context: Context, cmd: MdmCommand): Boolean {
        val apkUrl = cmd.payload.optString("apk_url").ifEmpty { return false }
        val packageName = cmd.payload.optString("package_name")
        val campaignId = cmd.payload.optString("campaign_id")
        val apkSha256 = cmd.payload.optString("apk_sha256").ifEmpty { null }

        // キャッシュ済みAPKを確認（DPC-02のプリDL済みファイル）
        val cachedApk = ApkDownloadManager.getCachedApk(context, apkUrl, apkSha256)

        return if (cachedApk != null && packageName.isNotEmpty() && campaignId.isNotEmpty()) {
            // DPC-01: サイレントインストール（Device Owner権限）
            Log.i(TAG, "Silent install from cache: pkg=$packageName")
            SilentInstallManager.install(context, cachedApk, packageName, campaignId)
        } else {
            // キャッシュなし → DL をキューイングしてURLをブラウザで開く
            if (packageName.isNotEmpty() && campaignId.isNotEmpty()) {
                ApkDownloadManager.enqueue(context, apkUrl, apkSha256, campaignId)
                Log.i(TAG, "APK download enqueued for next silent install: $apkUrl")
            }
            // フォールバック: ブラウザ経由インストール画面
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl)).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
            true
        }
    }

    // ── ロック画面コンテンツ更新 ─────────────────────────────

    private fun updateLockscreen(context: Context, cmd: MdmCommand): Boolean {
        val title        = cmd.payload.optString("title")
        val ctaUrl       = cmd.payload.optString("cta_url")
        val impressionId = cmd.payload.optString("impression_id").ifEmpty { null }

        val prefs = context.getSharedPreferences("mdm_lockscreen", Context.MODE_PRIVATE)
        prefs.edit()
            .putString("title",        title)
            .putString("cta_url",      ctaUrl)
            .putString("updated_at",   System.currentTimeMillis().toString())
            .apply {
                if (impressionId != null) putString("impression_id", impressionId)
                else remove("impression_id")
            }
            .apply()

        Log.i(TAG, "Lockscreen content updated | impression=$impressionId")

        // ロック画面広告アクティビティを起動（ユーザーは同意済み）
        LockscreenActivity.launch(context)
        return true
    }

    // ── プレイアブル広告起動（ADT-02）──────────────────────────────
    private fun playGame(context: Context, cmd: MdmCommand): Boolean {
        val gameUrl      = cmd.payload.optString("game_url").ifEmpty { return false }
        val impressionId = cmd.payload.optString("impression_id", cmd.id)
        val ctaUrl       = cmd.payload.optString("cta_url", "")
        Log.i(TAG, "Launching GameAdActivity: $gameUrl")
        GameAdActivity.launch(context, gameUrl, impressionId, ctaUrl)
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
