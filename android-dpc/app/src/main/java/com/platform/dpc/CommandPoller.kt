package com.platform.dpc

import android.content.Context
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * WorkManagerで定期実行されるMDMコマンドポーラー
 *
 * 30分ごとにサーバーへコマンドをポーリングし、
 * 受け取ったコマンドを CommandExecutor で実行してACKを送信する。
 * FCMサイレントプッシュを受け取った際は即時実行もできる。
 */
class CommandPoller(
    private val context: Context,
    params: WorkerParameters,
) : Worker(context, params) {

    private fun getEncryptedPrefs(): android.content.SharedPreferences {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        return EncryptedSharedPreferences.create(
            context,
            "mdm_prefs_encrypted",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun doWork(): Result {
        val prefs = getEncryptedPrefs()
        val deviceId = prefs.getString("device_id", null) ?: run {
            Log.w(TAG, "device_id not set, skipping poll")
            return Result.success()
        }

        Log.i(TAG, "Polling commands for device: ${deviceId.take(8)}...")

        val commands = MdmApiClient.pollCommands(deviceId)
        if (commands.isEmpty()) {
            Log.d(TAG, "No pending commands")
            return Result.success()
        }

        Log.i(TAG, "Received ${commands.size} command(s)")

        var allSuccess = true
        for (cmd in commands) {
            val success = try {
                CommandExecutor.execute(context, cmd)
            } catch (e: Exception) {
                Log.e(TAG, "Command execution failed: ${cmd.type} - $e")
                false
            }
            MdmApiClient.ackCommand(cmd.id, success)
            if (!success) allSuccess = false
        }

        return if (allSuccess) Result.success() else Result.retry()
    }

    companion object {
        private const val TAG = "CommandPoller"
        private const val WORK_NAME = "mdm_command_poll"

        /**
         * WorkManagerにポーリングジョブを登録する。
         * アプリ起動時・再起動時に呼ぶ。
         */
        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()

            val request = PeriodicWorkRequestBuilder<CommandPoller>(
                30, TimeUnit.MINUTES,
                5, TimeUnit.MINUTES,   // フレックスインターバル
            )
                .setConstraints(constraints)
                .build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
            Log.i(TAG, "CommandPoller scheduled (30min interval)")
        }

        /**
         * FCMサイレントプッシュ受信時などに即時実行する。
         */
        fun runNow(context: Context) {
            val request = androidx.work.OneTimeWorkRequestBuilder<CommandPoller>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(context).enqueue(request)
            Log.i(TAG, "CommandPoller triggered immediately")
        }
    }
}
