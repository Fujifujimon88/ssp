package com.platform.dpc

import android.content.Context
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
     *
     * @param context アクティビティまたはサービスの Context。
     *   SharedPrefs から当日の点灯回数を読み取るために使用。
     *   アプリ再起動をまたいでも正確な値を返せる。
     */
    fun report(
        impressionId: String,
        deviceId: String,
        dwellTimeMs: Long,
        dismissType: String,
        context: Context,
    ) {
        val calendar = java.util.Calendar.getInstance()
        val hourOfDay = calendar.get(java.util.Calendar.HOUR_OF_DAY)
        val dayOfWeek = calendar.get(java.util.Calendar.DAY_OF_WEEK) - 1  // 0=Sun

        // 本日の点灯回数を取得（ScreenOnReceiver が書いた SharedPrefs から読む）
        val screenOnCount = getScreenOnCountToday(context)

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

    /**
     * ScreenOnReceiver が FreqCapPrefs SharedPrefs に書いた当日の点灯回数を返す。
     * アプリ再起動後も正確な値を返せる。
     */
    private fun getScreenOnCountToday(context: Context): Int {
        val prefs = context.getSharedPreferences(FreqCapPrefs.PREFS_NAME, Context.MODE_PRIVATE)
        val lastDate = prefs.getString(FreqCapPrefs.KEY_DATE, "")
        return if (lastDate == FreqCapPrefs.today()) prefs.getInt(FreqCapPrefs.KEY_COUNT, 0) else 0
    }
}
