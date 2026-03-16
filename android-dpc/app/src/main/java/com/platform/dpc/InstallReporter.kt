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
 * DPC-03 — インストール確認レポート送信
 *
 * APKインストール完了後にサーバーへ報告する。
 * 決定論的アトリビューション: DPCがインストールを確認した時点で
 * 100%確実にキャンペーンへ紐付けができる（詐欺ゼロ）。
 *
 * リトライ: 3回、指数バックオフ（1s → 4s → 16s）。
 */
object InstallReporter {

    private const val TAG = "InstallReporter"
    private const val MAX_RETRIES = 3

    private val JSON_TYPE = "application/json; charset=utf-8".toMediaType()
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    private val serverUrl = BuildConfig.SERVER_URL.trimEnd('/')

    /**
     * インストール完了をサーバーへ報告する（リトライ付き）。
     * バックグラウンドスレッドから呼ぶこと。
     *
     * @param deviceId   デバイス識別子
     * @param packageName インストールされたパッケージ名
     * @param campaignId  キャンペーンID
     * @param apkSha256  APKのSHA-256（任意、整合性検証用）
     * @return true: サーバー記録成功
     */
    fun reportInstall(
        deviceId: String,
        packageName: String,
        campaignId: String,
        apkSha256: String? = null,
    ): Boolean {
        val body = JSONObject().apply {
            put("device_id", deviceId)
            put("package_name", packageName)
            put("campaign_id", campaignId)
            put("install_ts", System.currentTimeMillis() / 1000)
            apkSha256?.let { put("apk_sha256", it) }
        }

        repeat(MAX_RETRIES) { attempt ->
            try {
                val request = Request.Builder()
                    .url("$serverUrl/mdm/install_confirmed")
                    .post(body.toString().toRequestBody(JSON_TYPE))
                    .build()

                val response = http.newCall(request).execute()
                val bodyStr = response.body?.string() ?: ""

                if (response.isSuccessful) {
                    val json = JSONObject(bodyStr)
                    val alreadyRecorded = json.optBoolean("already_recorded", false)
                    Log.i(TAG, "Install reported: pkg=$packageName campaign=$campaignId already_recorded=$alreadyRecorded")
                    return true
                }

                Log.w(TAG, "Report failed (attempt ${attempt + 1}): HTTP ${response.code}")
            } catch (e: Exception) {
                Log.w(TAG, "Report error (attempt ${attempt + 1}): $e")
            }

            // 指数バックオフ: 1s → 4s → 16s
            if (attempt < MAX_RETRIES - 1) {
                Thread.sleep(1000L * Math.pow(4.0, attempt.toDouble()).toLong())
            }
        }

        Log.e(TAG, "Failed to report install after $MAX_RETRIES attempts: pkg=$packageName")
        return false
    }
}
