package com.platform.dpc

import android.content.Context
import android.util.Log
import androidx.work.CoroutineWorker
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Wi-Fi SSID 来店チェックインワーカー
 *
 * MdmForegroundService の NetworkCallback がSSID接続を検知したとき、
 * このワーカーをエンキューしてサーバーに報告する。
 * WorkManager が保証するリトライ機構により、接続直後にネットワークが
 * 不安定でも確実に届く。
 *
 * 拡張方法: サーバー側の wifi_trigger_rules テーブルに新しいルールを追加するだけ。
 *          クライアント側の変更は不要。
 */
class WifiCheckinWorker(
    private val context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val prefs = context.getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
        val deviceId = prefs.getString("device_id", null) ?: run {
            Log.w(TAG, "device_id not set, skipping wifi checkin")
            return@withContext Result.failure()
        }
        val ssid = inputData.getString(KEY_SSID) ?: run {
            Log.w(TAG, "ssid not in inputData")
            return@withContext Result.failure()
        }

        Log.i(TAG, "Reporting wifi checkin: ssid=$ssid device=${deviceId.take(8)}")
        val ok = MdmApiClient.reportWifiCheckin(deviceId, ssid)
        if (ok) Result.success() else Result.retry()
    }

    companion object {
        private const val TAG = "WifiCheckinWorker"
        const val KEY_SSID = "ssid"

        /**
         * SSIDが確定したらこのメソッドを呼ぶだけ。
         * WorkManager がリトライ・ライフサイクル管理を担う。
         */
        fun enqueue(context: Context, ssid: String) {
            val request = OneTimeWorkRequestBuilder<WifiCheckinWorker>()
                .setInputData(workDataOf(KEY_SSID to ssid))
                .build()
            WorkManager.getInstance(context).enqueue(request)
            Log.i(TAG, "WifiCheckinWorker enqueued for ssid=$ssid")
        }
    }
}
