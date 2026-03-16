package com.platform.dpc

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.ViewGroup
import android.view.WindowManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ProgressBar
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * ADT-02 — HTML5プレイアブル広告フルスクリーンWebView
 *
 * ロック画面解除後に表示するゲーム型広告。
 * CPM ¥3,000〜8,000（バナーの6〜16倍）。
 * - WebView: JS有効、ファイル/コンテンツアクセス無効（サンドボックス）
 * - JS Bridge: Android.onGameComplete(score) → 変換イベント送信
 * - 外部URL遷移をブロック（ゲームHTML内のリンクのみ）
 * - トラッキング: game_start / game_complete / game_converted
 */
class GameAdActivity : Activity() {

    companion object {
        private const val TAG = "GameAdActivity"
        private const val EXTRA_GAME_URL      = "game_url"
        private const val EXTRA_IMPRESSION_ID = "impression_id"
        private const val EXTRA_CTA_URL       = "cta_url"

        fun launch(context: Context, gameUrl: String, impressionId: String, ctaUrl: String = "") {
            context.startActivity(
                Intent(context, GameAdActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
                    putExtra(EXTRA_GAME_URL,      gameUrl)
                    putExtra(EXTRA_IMPRESSION_ID, impressionId)
                    putExtra(EXTRA_CTA_URL,       ctaUrl)
                }
            )
        }
    }

    private var webView: WebView? = null
    private var gameStarted = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val gameUrl      = intent.getStringExtra(EXTRA_GAME_URL)      ?: run { finish(); return }
        val impressionId = intent.getStringExtra(EXTRA_IMPRESSION_ID) ?: ""
        val ctaUrl       = intent.getStringExtra(EXTRA_CTA_URL)       ?: ""

        val root = FrameLayout(this)
        root.setBackgroundColor(Color.BLACK)

        // ── WebView（サンドボックス）──────────────────────────────
        val wv = WebView(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            settings.apply {
                javaScriptEnabled      = true
                allowFileAccess        = false   // セキュリティ: ファイルアクセス無効
                allowContentAccess     = false   // セキュリティ: コンテンツアクセス無効
                domStorageEnabled      = true
                mediaPlaybackRequiresUserGesture = false
            }

            // 外部URLナビゲーションをブロック
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                    val url = req.url.toString()
                    // ゲームアセット（同一オリジン）は許可、外部URLはブロック
                    return !url.startsWith(gameUrl.substringBefore("?").substringBeforeLast("/"))
                }

                override fun onPageFinished(view: WebView, url: String) {
                    super.onPageFinished(view, url)
                    if (!gameStarted) {
                        gameStarted = true
                        reportGameEvent("game_start", impressionId)
                        Log.i(TAG, "Game started: impressionId=$impressionId")
                    }
                }
            }

            // JS Bridge: Android.onGameComplete(score)
            addJavascriptInterface(
                GameBridge(impressionId, ctaUrl, this@GameAdActivity),
                "Android"
            )
        }
        root.addView(wv)
        webView = wv

        // ローディングインジケーター
        val progress = ProgressBar(this).apply {
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
            )
            isIndeterminate = true
        }
        root.addView(progress)

        // 閉じるボタン
        val btnClose = Button(this).apply {
            text = "✕"
            textSize = 14f
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.argb(160, 0, 0, 0))
            layoutParams = FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.END
            ).apply { setMargins(0, 32, 32, 0) }
            setOnClickListener { finish() }
        }
        root.addView(btnClose)

        setContentView(root)
        wv.loadUrl(gameUrl)
    }

    override fun onDestroy() {
        webView?.destroy()
        webView = null
        super.onDestroy()
    }

    private fun reportGameEvent(event: String, impressionId: String) {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val prefs = getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
                val deviceId = prefs.getString("device_id", "") ?: ""
                MdmApiClient.reportGameEvent(event, impressionId, deviceId)
            } catch (e: Exception) {
                Log.w(TAG, "Game event report failed: $e")
            }
        }
    }

    // ── JS Bridge ─────────────────────────────────────────────
    inner class GameBridge(
        private val impressionId: String,
        private val ctaUrl: String,
        private val activity: Activity,
    ) {
        @JavascriptInterface
        fun onGameComplete(score: Int) {
            Log.i(TAG, "onGameComplete: score=$score impressionId=$impressionId")
            CoroutineScope(Dispatchers.IO).launch {
                val prefs = activity.getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
                val deviceId = prefs.getString("device_id", "") ?: ""
                MdmApiClient.reportGameEvent("game_complete", impressionId, deviceId, score)
            }
            // CTAへ遷移
            if (ctaUrl.isNotEmpty()) {
                Handler(Looper.getMainLooper()).postDelayed({
                    CoroutineScope(Dispatchers.IO).launch {
                        val prefs = activity.getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
                        val deviceId = prefs.getString("device_id", "") ?: ""
                        MdmApiClient.reportGameEvent("game_converted", impressionId, deviceId, score)
                    }
                    activity.startActivity(
                        Intent(Intent.ACTION_VIEW, android.net.Uri.parse(ctaUrl)).apply {
                            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        }
                    )
                    activity.finish()
                }, 1_000L)
            }
        }

        @JavascriptInterface
        fun getDeviceId(): String {
            return activity.getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
                .getString("device_id", "") ?: ""
        }
    }
}
