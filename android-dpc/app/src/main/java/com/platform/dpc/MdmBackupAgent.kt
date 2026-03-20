package com.platform.dpc

import android.app.backup.BackupAgentHelper
import android.app.backup.SharedPreferencesBackupHelper
import android.content.Context
import android.provider.Settings
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Android Auto Backup エージェント
 *
 * 設計方針:
 *   - EncryptedSharedPreferences (mdm_prefs_encrypted) は KeyStore 依存のため
 *     別端末に復元すると復号できない → バックアップ対象外
 *   - enrollment_token のみ平文の mdm_token_backup.xml に保存してバックアップ
 *   - 復元後 (onRestoreFinished) で:
 *       1. mdm_token_backup から enrollment_token を読み取る
 *       2. EncryptedSharedPreferences に書き直す
 *       3. device_id を新機種の ANDROID_ID で更新
 *       4. registered = false にリセット → MainActivity が再登録フローを実行
 */
class MdmBackupAgent : BackupAgentHelper() {

    override fun onCreate() {
        // mdm_token_backup.xml（enrollment_tokenのみ平文保存）のみバックアップ対象
        addHelper(
            "mdm_token_backup",
            SharedPreferencesBackupHelper(this, "mdm_token_backup"),
        )
    }

    override fun onRestoreFinished() {
        super.onRestoreFinished()

        val tokenPrefs = getSharedPreferences("mdm_token_backup", Context.MODE_PRIVATE)
        val token = tokenPrefs.getString("enrollment_token", null)

        if (token == null) {
            Log.w(TAG, "onRestoreFinished: no enrollment_token in backup, skipping")
            return
        }

        Log.i(TAG, "onRestoreFinished: restoring enrollment_token to new device")

        try {
            val masterKey = MasterKey.Builder(this)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            val encryptedPrefs = EncryptedSharedPreferences.create(
                this,
                "mdm_prefs_encrypted",
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )

            // 新機種の Android ID で device_id を更新
            val newDeviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)

            encryptedPrefs.edit()
                .putString("enrollment_token", token)
                .putString("device_id", newDeviceId)
                .putBoolean("registered", false)  // 再登録フローをトリガー
                .apply()

            Log.i(TAG, "onRestoreFinished: enrollment_token restored, device_id updated to new device")
        } catch (e: Exception) {
            Log.e(TAG, "onRestoreFinished: failed to restore to EncryptedSharedPreferences", e)
        }
    }

    companion object {
        private const val TAG = "MdmBackupAgent"
    }
}
