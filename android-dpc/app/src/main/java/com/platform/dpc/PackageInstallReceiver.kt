package com.platform.dpc

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * DPC-03 — パッケージインストール検知レシーバー
 *
 * ユーザーがインストール確認ダイアログでOKした後、
 * ACTION_PACKAGE_ADDED を受信してサーバーへ報告する。
 * pending_installs に保存したキャンペーンIDと紐付けて報告。
 */
class PackageInstallReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_PACKAGE_ADDED) return

        val packageName = intent.data?.schemeSpecificPart ?: return
        val isReplacing = intent.getBooleanExtra(Intent.EXTRA_REPLACING, false)

        // アップデート（再インストール）は除外
        if (isReplacing) {
            Log.d(TAG, "Package updated (not new install), skipping: $packageName")
            return
        }

        val prefs = context.getSharedPreferences("mdm_pending_installs", Context.MODE_PRIVATE)
        val campaignId = prefs.getString(packageName, null) ?: run {
            Log.d(TAG, "No pending campaign for: $packageName")
            return
        }

        // 報告済みなら削除
        prefs.edit().remove(packageName).apply()

        val deviceId = context.getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
            .getString("device_id", null) ?: return

        Log.i(TAG, "Package installed: $packageName → reporting to server (campaign=$campaignId)")

        // バックグラウンドでサーバー報告（リトライ付き）
        CoroutineScope(Dispatchers.IO).launch {
            InstallReporter.reportInstall(
                deviceId = deviceId,
                packageName = packageName,
                campaignId = campaignId,
            )
        }
    }

    companion object {
        private const val TAG = "PackageInstallReceiver"
    }
}
