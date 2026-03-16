package com.platform.dpc

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.platform.dpc.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * MDM DPC メインアクティビティ
 *
 * 起動フロー:
 *   1. Android ID（device_id）を取得してSharedPreferencesに保存
 *   2. 未登録の場合はバックエンドへ登録
 *   3. WorkManagerでコマンドポーリングを開始
 *   4. デバイス管理者が有効になっているか確認・案内
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

        // Android IDをデバイス識別子として使用
        deviceId = Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID)
        binding.tvDeviceId.text = "Device ID: ${deviceId.take(8)}..."

        setupButtons()
        autoInit()
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
        val prefs = getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)

        // device_idを保存
        prefs.edit().putString("device_id", deviceId).apply()

        // エンロールトークンをインテント or Prefsから取得
        val token = intent.getStringExtra("enrollment_token")
            ?: prefs.getString("enrollment_token", null)
        token?.let { prefs.edit().putString("enrollment_token", it).apply() }

        // 管理者権限確認
        if (dpm.isAdminActive(adminComponent)) {
            appendLog("デバイス管理者: 有効")
        } else {
            appendLog("デバイス管理者: 無効 → 「有効化」ボタンをタップしてください")
        }

        // WorkManagerスケジュール
        CommandPoller.schedule(this)
        appendLog("バックグラウンドポーリング: 開始（30分間隔）")

        // 初回登録
        val isRegistered = prefs.getBoolean("registered", false)
        if (!isRegistered) {
            registerToServer()
        } else {
            binding.tvStatus.text = "登録済み ✓"
            appendLog("サーバー登録済み")
        }
    }

    private fun registerToServer() {
        binding.tvStatus.text = "登録中..."
        lifecycleScope.launch {
            val prefs = getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
            val token = prefs.getString("enrollment_token", null)

            val success = withContext(Dispatchers.IO) {
                MdmApiClient.registerDevice(
                    deviceId = deviceId,
                    enrollmentToken = token,
                    fcmToken = null,  // FCM統合後に設定
                    manufacturer = Build.MANUFACTURER,
                    model = Build.MODEL,
                    androidVersion = Build.VERSION.RELEASE,
                    sdkInt = Build.VERSION.SDK_INT,
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

    private fun appendLog(message: String) {
        val current = binding.tvLog.text.toString()
        val timestamp = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
            .format(java.util.Date())
        binding.tvLog.text = "[$timestamp] $message\n$current"
    }
}
