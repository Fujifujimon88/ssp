package com.platform.dpc

import com.platform.dpc.BuildConfig
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * バックエンド MDM API クライアント
 *
 * すべての通信はバックグラウンドスレッドで実行すること（メインスレッドNG）。
 * okhttp の同期APIを使用（コルーチン内から呼ぶ）。
 */
object MdmApiClient {

    private val JSON_TYPE = "application/json; charset=utf-8".toMediaType()

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    private val serverUrl = BuildConfig.SERVER_URL.trimEnd('/')

    // ── デバイス登録 ────────────────────────────────────────

    /**
     * DPC APK が初回起動時にサーバーへデバイス情報を登録する。
     * 以降はFCMトークンが更新されたときも呼ぶ。
     *
     * @return true: 成功
     */
    fun registerDevice(
        deviceId: String,
        enrollmentToken: String?,
        fcmToken: String?,
        manufacturer: String,
        model: String,
        androidVersion: String,
        sdkInt: Int,
    ): Boolean {
        val body = JSONObject().apply {
            put("device_id", deviceId)
            enrollmentToken?.let { put("enrollment_token", it) }
            fcmToken?.let { put("fcm_token", it) }
            put("manufacturer", manufacturer)
            put("model", model)
            put("android_version", androidVersion)
            put("sdk_int", sdkInt)
        }

        return try {
            val request = Request.Builder()
                .url("$serverUrl/mdm/android/register")
                .post(body.toString().toRequestBody(JSON_TYPE))
                .build()
            val response = http.newCall(request).execute()
            response.use { it.isSuccessful }
        } catch (e: Exception) {
            android.util.Log.w("MdmApiClient", "registerDevice failed: $e")
            false
        }
    }

    // ── コマンドポーリング ───────────────────────────────────

    /**
     * サーバーから pending コマンドを取得する。
     * 取得と同時にサーバー側で status が sent に変わる。
     *
     * @return コマンドのリスト。エラー時は空リスト。
     *   各要素: {"id": "...", "type": "add_webclip", "payload": {...}}
     */
    fun pollCommands(deviceId: String): List<MdmCommand> {
        return try {
            val request = Request.Builder()
                .url("$serverUrl/mdm/android/commands/$deviceId")
                .get()
                .build()
            val response = http.newCall(request).execute()
            if (!response.isSuccessful) return emptyList()

            val json = JSONObject(response.body!!.string())
            val array = json.getJSONArray("commands")
            (0 until array.length()).map { i ->
                val obj = array.getJSONObject(i)
                MdmCommand(
                    id = obj.getString("id"),
                    type = obj.getString("type"),
                    payload = obj.optJSONObject("payload") ?: JSONObject(),
                )
            }
        } catch (e: Exception) {
            android.util.Log.w("MdmApiClient", "pollCommands failed: $e")
            emptyList()
        }
    }

    // ── ACK ─────────────────────────────────────────────────

    /**
     * コマンド実行結果をサーバーへ報告する。
     */
    fun ackCommand(commandId: String, success: Boolean): Boolean {
        val body = JSONObject().apply { put("success", success) }
        return try {
            val request = Request.Builder()
                .url("$serverUrl/mdm/android/commands/$commandId/ack")
                .post(body.toString().toRequestBody(JSON_TYPE))
                .build()
            val response = http.newCall(request).execute()
            response.use { it.isSuccessful }
        } catch (e: Exception) {
            android.util.Log.w("MdmApiClient", "ackCommand failed: $e")
            false
        }
    }

    // ── ロック画面コンテンツ ─────────────────────────────────

    fun fetchLockscreenContent(deviceId: String): JSONObject? {
        return try {
            val request = Request.Builder()
                .url("$serverUrl/mdm/android/lockscreen/content?device_id=$deviceId")
                .get()
                .build()
            val response = http.newCall(request).execute()
            if (!response.isSuccessful) return null
            val json = JSONObject(response.body!!.string())
            json.optJSONObject("content")
        } catch (e: Exception) {
            null
        }
    }

    // ── インプレッション・クリック報告 ────────────────────────────

    /**
     * ロック画面広告のCTAボタンタップをサーバーへ報告する。
     * バックグラウンドスレッド（IO コルーチン）から呼ぶこと。
     */
    fun reportClick(impressionId: String): Boolean {
        val body = JSONObject().apply { put("impression_id", impressionId) }
        return try {
            val request = Request.Builder()
                .url("$serverUrl/mdm/impression/click")
                .post(body.toString().toRequestBody(JSON_TYPE))
                .build()
            http.newCall(request).execute().use { it.isSuccessful }
        } catch (e: Exception) {
            android.util.Log.w("MdmApiClient", "reportClick failed: $e")
            false
        }
    }

    /**
     * ロック画面コンテンツを取得する。
     * impression_id を含むレスポンス全体を返す（CTR計測用）。
     */
    fun fetchLockscreenAd(deviceId: String, enrollmentToken: String?): JSONObject? {
        return try {
            var url = "$serverUrl/mdm/android/lockscreen/content?device_id=$deviceId"
            if (enrollmentToken != null) url += "&enrollment_token=$enrollmentToken"
            val request = Request.Builder().url(url).get().build()
            val response = http.newCall(request).execute()
            if (!response.isSuccessful) return null
            JSONObject(response.body!!.string())
        } catch (e: Exception) {
            android.util.Log.w("MdmApiClient", "fetchLockscreenAd failed: $e")
            null
        }
    }
}

/** サーバーから受け取るMDMコマンド */
data class MdmCommand(
    val id: String,
    val type: String,
    val payload: JSONObject,
)
