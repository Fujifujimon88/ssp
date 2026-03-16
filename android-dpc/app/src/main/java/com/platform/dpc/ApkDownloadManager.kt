package com.platform.dpc

import android.content.Context
import android.util.Log
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * DPC-02 — APKバックグラウンドダウンロードマネージャー
 *
 * Wi-Fi接続中かつ充電中のみ実行（バッテリー・通信量ゼロ影響）。
 * ダウンロード完了後はSHA-256を検証してからキャッシュに保存する。
 * 実際のインストールはユーザーへの確認ダイアログを経て実行する。
 */
object ApkDownloadManager {

    private const val TAG = "ApkDownloadManager"
    private const val APK_CACHE_DIR = "apk_cache"
    private const val MAX_CACHE_MB = 50L

    private val http = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    /**
     * APKダウンロードをWorkManagerにキューイング。
     * Wi-Fi + 充電中の制約付き。
     */
    fun enqueue(context: Context, apkUrl: String, expectedSha256: String?, campaignId: String) {
        val data = Data.Builder()
            .putString("apk_url", apkUrl)
            .putString("expected_sha256", expectedSha256)
            .putString("campaign_id", campaignId)
            .build()

        val constraints = Constraints.Builder()
            .setRequiredNetworkType(NetworkType.UNMETERED)  // Wi-Fiのみ
            .setRequiresCharging(true)                       // 充電中のみ
            .build()

        val request = OneTimeWorkRequestBuilder<ApkDownloadWorker>()
            .setConstraints(constraints)
            .setInputData(data)
            .build()

        WorkManager.getInstance(context).enqueue(request)
        Log.i(TAG, "APK download enqueued: $apkUrl")
    }

    /**
     * キャッシュ済みAPKファイルを取得する。
     * @return ファイルが存在してSHA-256が一致すればFile、なければnull
     */
    fun getCachedApk(context: Context, apkUrl: String, expectedSha256: String?): File? {
        val file = cacheFile(context, apkUrl)
        if (!file.exists()) return null
        if (expectedSha256 != null && sha256(file) != expectedSha256) {
            file.delete()
            Log.w(TAG, "Cached APK SHA-256 mismatch, deleted: ${file.name}")
            return null
        }
        return file
    }

    /** キャッシュディレクトリの合計サイズが上限を超えたらLRU削除 */
    fun evictIfNeeded(context: Context) {
        val dir = cacheDir(context)
        val totalMb = dir.walkTopDown().sumOf { it.length() } / 1024 / 1024
        if (totalMb <= MAX_CACHE_MB) return

        dir.listFiles()
            ?.sortedBy { it.lastModified() }
            ?.take(3)
            ?.forEach {
                it.delete()
                Log.i(TAG, "Evicted cached APK: ${it.name}")
            }
    }

    internal fun cacheDir(context: Context): File =
        File(context.getExternalFilesDir(null), APK_CACHE_DIR).also { it.mkdirs() }

    internal fun cacheFile(context: Context, url: String): File {
        val name = url.hashCode().toString(16) + ".apk"
        return File(cacheDir(context), name)
    }

    internal fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(8192)
            var read: Int
            while (input.read(buf).also { read = it } != -1) {
                digest.update(buf, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    internal fun download(context: Context, apkUrl: String, expectedSha256: String?): File? {
        evictIfNeeded(context)
        val dest = cacheFile(context, apkUrl)

        return try {
            val req = Request.Builder().url(apkUrl).get().build()
            http.newCall(req).execute().use { response ->
                if (!response.isSuccessful) {
                    Log.w(TAG, "Download failed: HTTP ${response.code}")
                    return null
                }
                response.body!!.byteStream().use { input ->
                    dest.outputStream().use { output -> input.copyTo(output) }
                }
            }

            if (expectedSha256 != null) {
                val actual = sha256(dest)
                if (actual != expectedSha256) {
                    dest.delete()
                    Log.e(TAG, "SHA-256 mismatch: expected=$expectedSha256 actual=$actual")
                    return null
                }
            }

            Log.i(TAG, "APK downloaded and verified: ${dest.name} (${dest.length() / 1024}KB)")
            dest
        } catch (e: Exception) {
            Log.e(TAG, "Download error: $e")
            dest.takeIf { it.exists() }?.delete()
            null
        }
    }
}

/**
 * WorkManagerワーカー。Wi-Fi + 充電中に実行される。
 */
class ApkDownloadWorker(
    private val context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val apkUrl = inputData.getString("apk_url") ?: return@withContext Result.failure()
        val sha256 = inputData.getString("expected_sha256")
        val campaignId = inputData.getString("campaign_id") ?: ""

        Log.i(TAG, "Downloading APK for campaign=$campaignId url=$apkUrl")

        val file = ApkDownloadManager.download(context, apkUrl, sha256)
        return@withContext if (file != null) {
            Log.i(TAG, "APK ready: ${file.absolutePath}")
            Result.success()
        } else {
            Log.w(TAG, "APK download failed, will retry")
            Result.retry()
        }
    }

    companion object {
        private const val TAG = "ApkDownloadWorker"
    }
}
