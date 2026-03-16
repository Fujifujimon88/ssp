package com.platform.dpc

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File

/**
 * DPC-01 — サイレントAPKインストール（Device Owner権限）
 *
 * Android Enterprise の PackageInstaller.Session API を使用。
 * Device Owner として登録済みの DPC のみ実行可能（ユーザーダイアログなし）。
 *
 * 利用条件:
 *   - DevicePolicyManager.isDeviceOwnerApp(packageName) == true
 *   - エンロール同意フローで app_install に同意済み
 *
 * Digital Turbine Ignite / Microsoft Intune / VMware Workspace ONE
 * と同じ Android Enterprise 標準 API を使用。
 */
object SilentInstallManager {

    private const val TAG = "SilentInstallManager"

    /**
     * APKファイルをサイレントインストールする。
     * バックグラウンドスレッドから呼ぶこと。
     *
     * @param context   Context（Device Owner アプリ）
     * @param apkFile   インストールするAPKファイル（SHA-256検証済み）
     * @param packageName インストール対象パッケージ名
     * @param campaignId  CPI課金用キャンペーンID
     * @return true: インストール開始成功（完了は PackageInstallStatusReceiver で検知）
     */
    fun install(
        context: Context,
        apkFile: File,
        packageName: String,
        campaignId: String,
    ): Boolean {
        if (!apkFile.exists()) {
            Log.e(TAG, "APK file not found: ${apkFile.absolutePath}")
            return false
        }

        val packageInstaller = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(
            PackageInstaller.SessionParams.MODE_FULL_INSTALL,
        )

        return try {
            val sessionId = packageInstaller.createSession(params)
            val session = packageInstaller.openSession(sessionId)

            // APKをセッションに書き込む
            session.use { s ->
                apkFile.inputStream().use { input ->
                    s.openWrite(packageName, 0, apkFile.length()).use { output ->
                        input.copyTo(output)
                        s.fsync(output)
                    }
                }

                // インストール完了通知用 PendingIntent
                val intent = Intent(context, PackageInstallStatusReceiver::class.java).apply {
                    putExtra("package_name", packageName)
                    putExtra("campaign_id", campaignId)
                }
                val pendingIntent = android.app.PendingIntent.getBroadcast(
                    context,
                    sessionId,
                    intent,
                    android.app.PendingIntent.FLAG_UPDATE_CURRENT or
                        android.app.PendingIntent.FLAG_IMMUTABLE,
                )

                s.commit(pendingIntent.intentSender)
            }

            // pending_installs に記録（PackageInstallReceiver でも補完）
            context.getSharedPreferences("mdm_pending_installs", Context.MODE_PRIVATE)
                .edit().putString(packageName, campaignId).apply()

            Log.i(TAG, "Silent install session committed: pkg=$packageName sessionId=$sessionId")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Silent install failed: $e")
            false
        }
    }
}

/**
 * PackageInstaller.Session のコミット結果を受け取るレシーバー。
 * インストール成功時に InstallReporter でサーバーへ報告する。
 */
class PackageInstallStatusReceiver : android.content.BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -1)
        val packageName = intent.getStringExtra("package_name") ?: return
        val campaignId = intent.getStringExtra("campaign_id") ?: return

        when (status) {
            PackageInstaller.STATUS_SUCCESS -> {
                Log.i(TAG, "Silent install SUCCESS: $packageName")

                // pending_installs をクリア
                context.getSharedPreferences("mdm_pending_installs", Context.MODE_PRIVATE)
                    .edit().remove(packageName).apply()

                val deviceId = context.getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
                    .getString("device_id", null) ?: return

                // バックグラウンドでサーバー報告（DPC-03）
                CoroutineScope(Dispatchers.IO).launch {
                    InstallReporter.reportInstall(
                        deviceId = deviceId,
                        packageName = packageName,
                        campaignId = campaignId,
                    )
                }
            }
            PackageInstaller.STATUS_FAILURE,
            PackageInstaller.STATUS_FAILURE_ABORTED,
            PackageInstaller.STATUS_FAILURE_BLOCKED,
            PackageInstaller.STATUS_FAILURE_CONFLICT,
            PackageInstaller.STATUS_FAILURE_INCOMPATIBLE,
            PackageInstaller.STATUS_FAILURE_INVALID,
            PackageInstaller.STATUS_FAILURE_STORAGE -> {
                val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                Log.w(TAG, "Silent install FAILED: $packageName status=$status msg=$msg")
            }
            else -> Log.d(TAG, "Install status=$status pkg=$packageName")
        }
    }

    companion object {
        private const val TAG = "PackageInstallStatus"
    }
}
