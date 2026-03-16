package com.platform.dpc

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * ロック画面広告表示アクティビティ
 *
 * update_lockscreen コマンド受信時に起動する。
 * ユーザーが同意済みのロック画面広告コンテンツを表示し、
 * CTAタップ時にクリックをサーバーへ報告してから遷移する。
 * 8秒後に自動解除。
 */
class LockscreenActivity : Activity() {

    companion object {
        private const val AUTO_DISMISS_MS = 8_000L
        private const val TAG = "LockscreenActivity"

        fun launch(context: Context) {
            val intent = Intent(context, LockscreenActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }
            context.startActivity(intent)
        }
    }

    // DPC-07: KPI計測用
    private var displayStartMs: Long = 0L
    private var currentImpressionId: String? = null
    private var currentDeviceId: String? = null

    // ADT-01: OMID viewability
    private var omidSession: com.iab.omid.library.platform.dpc.adsession.AdSession? = null
    private var adRootView: View? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // ロック画面上に表示（API 27+ は新API、それ以前はフラグで対応）
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }

        val prefs = getSharedPreferences("mdm_lockscreen", Context.MODE_PRIVATE)
        val title        = prefs.getString("title", "").orEmpty()
        val ctaUrl       = prefs.getString("cta_url", "").orEmpty()
        val impressionId = prefs.getString("impression_id", null)

        // コンテンツがない場合は即終了
        if (title.isEmpty()) {
            finish()
            return
        }

        currentImpressionId = impressionId
        currentDeviceId = getSharedPreferences("mdm_prefs", Context.MODE_PRIVATE)
            .getString("device_id", null)

        buildUi(title, ctaUrl, impressionId)

        // ADT-01: OMID viewability セッション開始
        OmidAdSessionManager.initialize(this)
        omidSession = OmidAdSessionManager.createNativeSession(this, adRootView ?: window.decorView)
        omidSession?.let { OmidAdSessionManager.reportImpression(it) }

        // 8秒後に自動解除（DPC-07: dismiss_type = auto_dismiss）
        Handler(Looper.getMainLooper()).postDelayed({
            reportKpi(LockscreenKpiReporter.DISMISS_AUTO)
            finish()
        }, AUTO_DISMISS_MS)
    }

    override fun onResume() {
        super.onResume()
        displayStartMs = android.os.SystemClock.elapsedRealtime()  // DPC-07: 滞留時間計測開始
    }

    override fun onBackPressed() {
        reportKpi(LockscreenKpiReporter.DISMISS_SWIPE)
        super.onBackPressed()
    }

    override fun onDestroy() {
        OmidAdSessionManager.finishSession(omidSession)
        super.onDestroy()
    }

    private fun reportKpi(dismissType: String) {
        val impId = currentImpressionId ?: return
        val devId = currentDeviceId ?: return
        val dwell = android.os.SystemClock.elapsedRealtime() - displayStartMs
        CoroutineScope(Dispatchers.IO).launch {
            LockscreenKpiReporter.report(impId, devId, dwell, dismissType)
        }
    }

    private fun buildUi(title: String, ctaUrl: String, impressionId: String?) {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setBackgroundColor(Color.argb(210, 0, 0, 0))
            setPadding(64, 0, 64, 0)
        }

        // スペーサー
        root.addView(spaceView(120))

        // 広告ラベル
        root.addView(TextView(this).apply {
            text = "広告"
            textSize = 11f
            setTextColor(Color.argb(160, 255, 255, 255))
            gravity = Gravity.CENTER
        })

        root.addView(spaceView(12))

        // タイトル
        root.addView(TextView(this).apply {
            text = title
            textSize = 20f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(0, 0, 0, 32)
        })

        // CTAボタン
        root.addView(Button(this).apply {
            text = "詳しく見る"
            setBackgroundColor(Color.rgb(0, 122, 255))
            setTextColor(Color.WHITE)
            textSize = 16f
            setPadding(48, 0, 48, 0)
            setOnClickListener { onCtaTapped(ctaUrl, impressionId) }
        })

        root.addView(spaceView(24))

        // 解除ヒント
        root.addView(TextView(this).apply {
            text = "スワイプして閉じる"
            textSize = 12f
            setTextColor(Color.argb(120, 255, 255, 255))
            gravity = Gravity.CENTER
        })

        setContentView(root)
        adRootView = root
    }

    private fun onCtaTapped(ctaUrl: String, impressionId: String?) {
        // DPC-07: KPI報告（CTA tap）
        reportKpi(LockscreenKpiReporter.DISMISS_CTA_TAP)

        // クリックをバックグラウンドで報告
        if (impressionId != null) {
            CoroutineScope(Dispatchers.IO).launch {
                MdmApiClient.reportClick(impressionId)
                android.util.Log.i(TAG, "Click reported: $impressionId")
            }
        }
        // CTAのURLへ遷移
        if (ctaUrl.isNotEmpty()) {
            startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(ctaUrl)).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
            )
        }
        finish()
    }

    private fun spaceView(heightDp: Int) = android.view.View(this).apply {
        layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dpToPx(heightDp)
        )
    }

    private fun dpToPx(dp: Int): Int =
        (dp * resources.displayMetrics.density).toInt()
}
