package com.platform.dpc

import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.StatFs
import android.provider.Settings
import android.telephony.TelephonyManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.platform.dpc.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * MDM DPC メインアクティビティ
 *
 * 起動フロー:
 *   1. 旧 mdm_prefs（平文）から mdm_prefs_encrypted へのマイグレーション（一回限り）
 *   2. Android ID（device_id）を取得してEncryptedSharedPreferencesに保存
 *   3. enrollment_token あり & registered=false → 再登録フロー（機種変更後の復旧）
 *   4. WorkManagerでコマンドポーリングを開始
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var deviceId: String
    private val adminComponent by lazy {
        ComponentName(this, DeviceAdminReceiver::class.java)
    }
    private val dpm by lazy {
        getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // 旧平文 SharedPreferences から暗号化版へのマイグレーション（初回のみ）
        migrateLegacyPrefsIfNeeded()

        // Android IDをデバイス識別子として使用
        deviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)
        binding.tvDeviceId.text = "Device ID: ${deviceId.take(8)}..."

        setupButtons()
        autoInit()
    }

    /**
     * 旧 mdm_prefs（平文）から mdm_prefs_encrypted（暗号化）へ一回限りのマイグレーション。
     * enrollment_token・device_id・registered フラグを移行し、旧ファイルを削除する。
     */
    private fun migrateLegacyPrefsIfNeeded() {
        val oldPrefs = getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
        val oldToken = oldPrefs.getString("enrollment_token", null) ?: return

        val newPrefs = getEncryptedPrefs()
        if (newPrefs.getString("enrollment_token", null) != null) return  // 移行済み

        newPrefs.edit()
            .putString("enrollment_token", oldToken)
            .putString("device_id", oldPrefs.getString("device_id", null))
            .putBoolean("registered", oldPrefs.getBoolean("registered", false))
            .apply()

        // enrollment_token のみ平文バックアップファイルにも保存（BackupAgent用）
        getSharedPreferences("mdm_token_backup", Context.MODE_PRIVATE)
            .edit().putString("enrollment_token", oldToken).apply()

        oldPrefs.edit().clear().apply()
        deleteSharedPreferences("mdm_prefs")
    }

    fun getEncryptedPrefs(): android.content.SharedPreferences {
        val masterKey = MasterKey.Builder(this)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        return EncryptedSharedPreferences.create(
            this,
            "mdm_prefs_encrypted",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    private fun setupButtons() {
        binding.btnAdminEnable.setOnClickListener {
            if (dpm.isAdminActive(adminComponent)) {
                appendLog("管理者権限: 有効")
            } else {
                val intent = Intent(DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN).apply {
                    putExtra(DevicePolicyManager.EXTRA_DEVICE_ADMIN, adminComponent)
                    putExtra(
                        DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                        "サービスの設定と通知を受け取るために必要です。",
                    )
                }
                startActivity(intent)
            }
        }

        binding.btnRegister.setOnClickListener {
            registerToServer()
        }

        binding.btnPoll.setOnClickListener {
            pollCommandsNow()
        }
    }

    private fun autoInit() {
        val prefs = getEncryptedPrefs()

        // device_idを保存（常に最新のAndroid IDで上書き）
        prefs.edit().putString("device_id", deviceId).apply()

        // enrollment_token のバックアップファイルにも同期（BackupAgent用）
        val token = intent.getStringExtra("enrollment_token")
            ?: prefs.getString("enrollment_token", null)
        if (token != null) {
            prefs.edit().putString("enrollment_token", token).apply()
            getSharedPreferences("mdm_token_backup", Context.MODE_PRIVATE)
                .edit().putString("enrollment_token", token).apply()
        }

        // 管理者権限確認
        if (dpm.isAdminActive(adminComponent)) {
            appendLog("デバイス管理者: 有効")
        } else {
            appendLog("デバイス管理者: 無効 → 「有効化」ボタンをタップしてください")
        }

        // WorkManagerスケジュール + 常駐サービス起動
        CommandPoller.schedule(this)
        PrefetchWorker.schedule(this)
        MdmForegroundService.start(this)
        appendLog("バックグラウンドポーリング: 開始（30分間隔）")
        appendLog("コンテンツプリフェッチ: 開始（30分間隔）")
        appendLog("常駐サービス: 起動")

        // 初回登録 or 機種変更後の再登録（BackupAgentがregistered=falseにリセット済みの場合も含む）
        val isRegistered = prefs.getBoolean("registered", false)
        if (!isRegistered) {
            registerToServer()
        } else {
            binding.tvStatus.text = "登録済み ✓"
            appendLog("サーバー登録済み")
        }

        // DPC-08: デバイスプロファイル送信（毎起動時）
        sendDeviceProfile()
    }

    private fun registerToServer() {
        binding.tvStatus.text = "登録中..."
        lifecycleScope.launch {
            val prefs = getEncryptedPrefs()
            val token = prefs.getString("enrollment_token", null)
            val fingerprint = "${Build.MANUFACTURER}:${Build.MODEL}:${Build.BRAND}".hashCode().toString(16)

            val success = withContext(Dispatchers.IO) {
                MdmApiClient.registerDevice(
                    deviceId = deviceId,
                    enrollmentToken = token,
                    fcmToken = null,  // FCM統合後に設定
                    manufacturer = Build.MANUFACTURER,
                    model = Build.MODEL,
                    androidVersion = Build.VERSION.RELEASE,
                    sdkInt = Build.VERSION.SDK_INT,
                    deviceFingerprint = fingerprint,
                )
            }

            if (success) {
                prefs.edit().putBoolean("registered", true).apply()
                binding.tvStatus.text = "登録完了 ✓"
                appendLog("サーバー登録: 成功")
                Toast.makeText(this@MainActivity, "登録が完了しました", Toast.LENGTH_SHORT).show()
            } else {
                binding.tvStatus.text = "登録失敗 ✗"
                appendLog("サーバー登録: 失敗 - サーバーURLを確認してください")
                appendLog("  SERVER_URL: ${BuildConfig.SERVER_URL}")
            }
        }
    }

    private fun pollCommandsNow() {
        appendLog("コマンド確認中...")
        CommandPoller.runNow(this)
        appendLog("WorkManager ジョブをキューに追加しました")
    }

    // DPC-08: デバイスプロファイルをバックグラウンドで送信
    private fun sendDeviceProfile() {
        lifecycleScope.launch {
            withContext(Dispatchers.IO) {
                val tm = getSystemService(Context.TELEPHONY_SERVICE) as TelephonyManager
                val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
                val memInfo = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
                val stat = StatFs(android.os.Environment.getDataDirectory().path)

                MdmApiClient.sendDeviceProfile(
                    deviceId      = deviceId,
                    manufacturer  = Build.MANUFACTURER,
                    model         = Build.MODEL,
                    osVersion     = Build.VERSION.RELEASE,
                    carrier       = tm.networkOperatorName.ifEmpty { null },
                    mccMnc        = tm.networkOperator.ifEmpty { null },
                    screenWidth   = resources.displayMetrics.widthPixels,
                    screenHeight  = resources.displayMetrics.heightPixels,
                    ramGb         = (memInfo.totalMem / 1024 / 1024 / 1024).toInt(),
                    storageFreeMb = stat.availableBytes / 1024 / 1024,
                )
            }
        }
    }

    private fun appendLog(message: String) {
        val current = binding.tvLog.text.toString()
        val timestamp = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
            .format(java.util.Date())
        binding.tvLog.text = "[$timestamp] $message\n$current"
    }
}
