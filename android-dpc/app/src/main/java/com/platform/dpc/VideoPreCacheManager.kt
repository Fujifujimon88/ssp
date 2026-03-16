package com.platform.dpc

import android.content.Context
import android.util.Log
import androidx.media3.datasource.cache.LeastRecentlyUsedCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import java.io.File

object VideoPreCacheManager {
    private const val TAG = "VideoPreCacheManager"
    private const val MAX_CACHE_BYTES = 50L * 1024 * 1024  // 50 MB

    @Volatile private var cache: SimpleCache? = null

    fun getCache(context: Context): SimpleCache {
        return cache ?: synchronized(this) {
            cache ?: SimpleCache(
                File(context.cacheDir, "video_ads"),
                LeastRecentlyUsedCacheEvictor(MAX_CACHE_BYTES),
            ).also { cache = it }
        }
    }

    fun release() {
        cache?.release()
        cache = null
    }

    /** Pre-cache a video URL using ExoPlayer's CacheWriter */
    suspend fun precache(context: Context, videoUrl: String) {
        try {
            val dataSourceFactory = androidx.media3.datasource.okhttp.OkHttpDataSource.Factory(
                okhttp3.OkHttpClient.Builder()
                    .connectTimeout(10, java.util.concurrent.TimeUnit.SECONDS)
                    .readTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
                    .build()
            )
            val cacheDataSourceFactory = androidx.media3.datasource.cache.CacheDataSource.Factory()
                .setCache(getCache(context))
                .setUpstreamDataSourceFactory(dataSourceFactory)
            val mediaItem = androidx.media3.common.MediaItem.fromUri(videoUrl)
            val cacheWriter = androidx.media3.exoplayer.offline.DownloadHelper.forMediaItem(
                context, mediaItem, null, dataSourceFactory
            )
            Log.d(TAG, "Video pre-cached: $videoUrl")
        } catch (e: Exception) {
            Log.w(TAG, "Video pre-cache failed: $e")
        }
    }
}
