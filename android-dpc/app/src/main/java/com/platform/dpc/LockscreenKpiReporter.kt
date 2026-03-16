package com.platform.dpc

import android.util.Log
import com.platform.dpc.BuildConfig
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * DPC-07 — ロック画面KPI計測レポーター
 *
 * LockscreenActivity から呼ばれ、滞留時間・解除タイプ・
 * 時間帯・点灯回数をサーバーへ報告する。
 * これにより朝プレミアム枠（CPM 3倍）の価格根拠データが蓄積される。
 */
object LockscreenKpiReporter {

    private const val TAG = "LockscreenKpiReporter"
    private val JSON_TYPE = "application/json; charset=utf-8".toMediaType()

    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    private val serverUrl = BuildConfig.SERVER_URL.trimEnd('/')

    /** 解除タイプ定数 */
    const val DISMISS_CTA_TAP    = "cta_tap"
    const val DISMISS_SWIPE      = "swipe_dismiss"
    const val DISMISS_AUTO       = "auto_dismiss"

    /**
     * KPIをサーバーへ非同期送信する。
     * バックグラウンドスレッド（IO コルーチン）から呼ぶこと。
     */
    fun report(
        impressionId: String,
        deviceId: String,
        dwellTimeMs: Long,
        dismissType: String,
    ) {
        val calendar = java.util.Calendar.getInstance()
        val hourOfDay = calendar.get(java.util.Calendar.HOUR_OF_DAY)
        val dayOfWeek = calendar.get(java.util.Calendar.DAY_OF_WEEK) - 1  // 0=Sun

        // 本日の点灯回数を取得
        val screenOnCount = getScreenOnCountToday()

        val body = JSONObject().apply {
            put("impression_id",       impressionId)
            put("device_id",           deviceId)
            put("dwell_time_ms",       dwellTimeMs)
            put("dismiss_type",        dismissType)
            put("hour_of_day",         hourOfDay)
            put("day_of_week",         dayOfWeek)
            put("screen_on_count_today", screenOnCount)
        }

        try {
            val request = Request.Builder()
                .url("$serverUrl/mdm/lockscreen_kpi")
                .post(body.toString().toRequestBody(JSON_TYPE))
                .build()
            http.newCall(request).execute().use { response ->
                Log.d(TAG, "KPI reported: dismiss=$dismissType dwell=${dwellTimeMs}ms hour=$hourOfDay impression#=$screenOnCount")
            }
        } catch (e: Exception) {
            Log.w(TAG, "KPI report failed (non-critical): $e")
            // KPI報告失敗は無視（広告表示には影響しない）
        }
    }

    // アプリ起動時にcontextを保持する簡易手段として Application クラス経由が望ましいが、
    // ここでは ScreenOnReceiver が更新した SharedPrefs から読む
    private fun getScreenOnCountToday(): Int {
        // ScreenOnReceiver が "mdm_freq_cap" に count を書いている
        // ただし、ここではApplication contextがないためstaticに保持
        return currentScreenOnCount
    }

    /** ScreenOnReceiver から更新される点灯回数 */
    @Volatile
    var currentScreenOnCount: Int = 1
}
