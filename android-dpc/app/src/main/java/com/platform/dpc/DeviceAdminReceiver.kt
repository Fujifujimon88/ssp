package com.platform.dpc

import android.app.admin.DeviceAdminReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import android.widget.Toast

/**
 * デバイス管理者レシーバー
 *
 * ユーザーが「デバイス管理者を有効化」するとこのレシーバーが呼ばれる。
 * MDMコマンドの中には管理者権限が必要なものがある:
 *   - パスワードポリシー設定
 *   - デバイスロック
 *   - ワイプ（出荷時リセット）
 */
class DeviceAdminReceiver : DeviceAdminReceiver() {

    override fun onEnabled(context: Context, intent: Intent) {
        super.onEnabled(context, intent)
        Log.i(TAG, "Device admin ENABLED")
        Toast.makeText(context, "デバイス管理者が有効になりました", Toast.LENGTH_SHORT).show()
    }

    override fun onDisabled(context: Context, intent: Intent) {
        super.onDisabled(context, intent)
        Log.i(TAG, "Device admin DISABLED")
    }

    override fun onPasswordChanged(context: Context, intent: Intent) {
        Log.d(TAG, "Password changed")
    }

    companion object {
        private const val TAG = "DeviceAdminReceiver"
    }
}
