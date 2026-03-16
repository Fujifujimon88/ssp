package com.platform.dpc

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.TextView
import android.graphics.Color
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request

/**
 * DPC-09 — 動画広告フルスクリーンインタースティシャル
 *
 * ロック画面解除後に表示する（ロック画面中ではない — UX制約）。
 * VAST 3.0トラッキングビーコンを各四分位点で送信。
 * 5秒後にスキップボタンが表示される。
 */
class VideoAdActivity : Activity() {

    companion object {
        private const val TAG = "VideoAdActivity"
        private const val EXTRA_VIDEO_URL     = "video_url"
        private const val EXTRA_IMPRESSION_ID = "impression_id"
        private const val EXTRA_VAST_XML      = "vast_xml"
        private const val SKIP_AFTER_MS       = 5_000L

        fun launch(context: Context, videoUrl: String, impressionId: String, vastXml: String) {
            val intent = Intent(context, VideoAdActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                putExtra(EXTRA_VIDEO_URL, videoUrl)
                putExtra(EXTRA_IMPRESSION_ID, impressionId)
                putExtra(EXTRA_VAST_XML, vastXml)
            }
            context.startActivity(intent)
        }
    }

    private var player: ExoPlayer? = null
    private var vastAd: VastAd? = null
    private val firedEvents = mutableSetOf<String>()
    private val handler = Handler(Looper.getMainLooper())
    private val http = OkHttpClient()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val videoUrl     = intent.getStringExtra(EXTRA_VIDEO_URL) ?: run { finish(); return }
        val impressionId = intent.getStringExtra(EXTRA_IMPRESSION_ID) ?: ""
        val vastXml      = intent.getStringExtra(EXTRA_VAST_XML) ?: ""

        vastAd = VastParser.parse(vastXml)

        val root = FrameLayout(this)
        root.setBackgroundColor(Color.BLACK)

        // ExoPlayer view
        val playerView = PlayerView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            useController = false
        }
        root.addView(playerView)

        // Skip button (hidden until 5s)
        val btnSkip = Button(this).apply {
            text = "スキップ ›"
            textSize = 14f
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.argb(160, 0, 0, 0))
            visibility = View.GONE
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.END
            ).apply { setMargins(0, 0, 32, 32) }
        }
        root.addView(btnSkip)

        // Timer label
        val tvTimer = TextView(this).apply {
            textSize = 12f
            setTextColor(Color.WHITE)
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.END
            ).apply { setMargins(0, 0, 32, 80) }
        }
        root.addView(tvTimer)

        setContentView(root)

        // ExoPlayer setup with pre-cached data source
        val cacheDataSourceFactory = CacheDataSource.Factory()
            .setCache(VideoPreCacheManager.getCache(this))
            .setUpstreamDataSourceFactory(
                androidx.media3.datasource.DefaultHttpDataSource.Factory()
            )

        player = ExoPlayer.Builder(this)
            .setMediaSourceFactory(
                androidx.media3.exoplayer.source.DefaultMediaSourceFactory(cacheDataSourceFactory)
            )
            .build()
            .also { exo ->
                playerView.player = exo
                exo.setMediaItem(MediaItem.fromUri(videoUrl))
                exo.prepare()
                exo.play()
            }

        // Fire impression beacon
        fireBeacons(vastAd?.impressionUrls ?: emptyList(), "impression")

        // Progress polling for quartile tracking + skip button
        val progressChecker = object : Runnable {
            override fun run() {
                val p = player ?: return
                val pos = p.currentPosition
                val dur = p.duration.takeIf { it > 0 } ?: 1L
                val pct = (pos * 100 / dur).toInt()

                // Skip button after 5s
                if (pos >= SKIP_AFTER_MS && btnSkip.visibility == View.GONE) {
                    btnSkip.visibility = View.VISIBLE
                    tvTimer.visibility = View.GONE
                } else if (pos < SKIP_AFTER_MS) {
                    val remaining = ((SKIP_AFTER_MS - pos) / 1000).toInt() + 1
                    tvTimer.text = "スキップまで ${remaining}秒"
                }

                // Quartile beacons
                if (pct >= 25 && !firedEvents.contains("firstQuartile"))  { fireEvent("firstQuartile"); firedEvents.add("firstQuartile") }
                if (pct >= 50 && !firedEvents.contains("midpoint"))        { fireEvent("midpoint");       firedEvents.add("midpoint") }
                if (pct >= 75 && !firedEvents.contains("thirdQuartile"))   { fireEvent("thirdQuartile");  firedEvents.add("thirdQuartile") }

                handler.postDelayed(this, 250)
            }
        }

        player?.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY && !firedEvents.contains("start")) {
                    fireEvent("start"); firedEvents.add("start")
                    handler.post(progressChecker)
                }
                if (state == Player.STATE_ENDED) {
                    fireEvent("complete")
                    handler.removeCallbacks(progressChecker)
                    finish()
                }
            }
        })

        btnSkip.setOnClickListener {
            fireEvent("skip")
            handler.removeCallbacks(progressChecker)
            finish()
        }

        Log.i(TAG, "VideoAdActivity launched: impressionId=$impressionId url=$videoUrl")
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        player?.release()
        player = null
        super.onDestroy()
    }

    private fun fireEvent(event: String) {
        val urls = vastAd?.trackingEvents?.get(event) ?: emptyList()
        Log.d(TAG, "VAST beacon: $event (${urls.size} URLs)")
        fireBeacons(urls, event)
    }

    private fun fireBeacons(urls: List<String>, label: String) {
        if (urls.isEmpty()) return
        CoroutineScope(Dispatchers.IO).launch {
            for (url in urls) {
                try {
                    http.newCall(Request.Builder().url(url).get().build()).execute().close()
                } catch (e: Exception) {
                    Log.w(TAG, "Beacon fire failed [$label]: $e")
                }
            }
        }
    }
}
