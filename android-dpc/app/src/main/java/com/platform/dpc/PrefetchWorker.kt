package com.platform.dpc

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * DPC-06 — WorkManager コンテンツプリフェッチ
 *
 * 30分ごとにバックグラウンドでサーバーから次の広告スロット（最大3件）を取得し、
 * SharedPreferencesにキャッシュする。
 * 画面点灯時（ScreenOnReceiver）はこのキャッシュから即座に表示するため、
 * APIコール待ちゼロで広告を表示できる（Glanceモデル）。
 *
 * ネットワーク接続時のみ実行。バッテリー残量低下時はスキップ。
 */
class PrefetchWorker(
    private val context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val prefs = context.getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
        val deviceId = prefs.getString("device_id", null) ?: run {
            Log.w(TAG, "device_id not set, skipping prefetch")
            return@withContext Result.success()
        }

        Log.i(TAG, "Prefetching ad content for device: ${deviceId.take(8)}...")

        val slots = MdmApiClient.prefetchAdSlots(deviceId) ?: run {
            Log.w(TAG, "Prefetch failed, keeping existing cache")
            return@withContext Result.retry()
        }

        if (slots.length() == 0) {
            Log.d(TAG, "No slots returned from prefetch")
            return@withContext Result.success()
        }

        // キャッシュに保存（最大3スロット）
        val lockscreenPrefs = context.getSharedPreferences("mdm_lockscreen", Context.MODE_PRIVATE)
        val editor = lockscreenPrefs.edit()

        // スロット0をメインロック画面コンテンツとして保存
        val slot0 = slots.optJSONObject(0)
        if (slot0 != null) {
            editor.putString("title", slot0.optString("title"))
            editor.putString("cta_url", slot0.optString("cta_url"))
            editor.putString("impression_id", slot0.optString("impression_id"))
            editor.putString("campaign_id", slot0.optString("campaign_id"))
            editor.putString("updated_at", System.currentTimeMillis().toString())
        }

        // 追加スロットをJSON配列で保存（将来のカルーセル表示用）
        editor.putString("prefetch_slots", slots.toString())
        editor.putString("prefetch_refreshed_at", System.currentTimeMillis().toString())
        editor.apply()

        Log.i(TAG, "Prefetch complete: ${slots.length()} slot(s) cached")

        // DPC-09: 動画クリエイティブのプリキャッシュ
        try {
            val slotsJson = lockscreenPrefs.getString("prefetch_slots", null) ?: return@withContext Result.success()
            val slotsArray = org.json.JSONArray(slotsJson)
            for (i in 0 until slotsArray.length()) {
                val slot = slotsArray.getJSONObject(i)
                if (slot.optString("creative_type") == "video") {
                    val vastUrl = slot.optString("vast_url")
                    if (vastUrl.isNotEmpty()) {
                        val vastXml = fetchVastXml(vastUrl) ?: continue
                        val parsed = VastParser.parse(vastXml) ?: continue
                        VideoPreCacheManager.precache(applicationContext, parsed.mediaFileUrl)
                        // Store vast_xml in prefs for VideoAdActivity
                        lockscreenPrefs.edit().putString("vast_xml_${slot.optString("impression_id")}", vastXml).apply()
                        android.util.Log.i(TAG, "Video pre-cached for slot $i")
                    }
                }
            }
        } catch (e: Exception) {
            android.util.Log.w(TAG, "Video pre-cache step failed (non-critical): $e")
        }

        Result.success()
    }

    private fun fetchVastXml(vastUrl: String): String? {
        return try {
            val client = okhttp3.OkHttpClient()
            val req = okhttp3.Request.Builder().url(vastUrl).get().build()
            client.newCall(req).execute().use { it.body?.string() }
        } catch (e: Exception) { null }
    }

    companion object {
        private const val TAG = "PrefetchWorker"
        private const val WORK_NAME = "mdm_prefetch"

        /**
         * WorkManagerに定期プリフェッチジョブを登録する。
         * アプリ起動時・再起動時に呼ぶ。
         */
        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .setRequiresBatteryNotLow(true)
                .build()

            val request = PeriodicWorkRequestBuilder<PrefetchWorker>(
                30, TimeUnit.MINUTES,
                15, TimeUnit.MINUTES,  // フレックスインターバル（正確な時刻を避けてバッテリー節約）
            )
                .setConstraints(constraints)
                .build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
            Log.i(TAG, "PrefetchWorker scheduled (30min interval)")
        }

        /** FCMプッシュ受信時などに即時実行 */
        fun runNow(context: Context) {
            val request = androidx.work.OneTimeWorkRequestBuilder<PrefetchWorker>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(context).enqueue(request)
            Log.i(TAG, "PrefetchWorker triggered immediately")
        }
    }
}
