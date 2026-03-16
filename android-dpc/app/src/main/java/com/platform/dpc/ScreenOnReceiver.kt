package com.platform.dpc

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * DPC-05 — 画面点灯イベントレシーバー
 *
 * ACTION_SCREEN_ON 受信時にキャッシュ済みの広告コンテンツを確認し、
 * 周波数キャップ（3回/日）の範囲内であれば LockscreenActivity を起動する。
 *
 * Glance (InMobi) と同じモデル。エンロール同意済みユーザーのみ対象。
 * MdmForegroundService から動的登録される（マニフェスト登録は不可）。
 */
class ScreenOnReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_SCREEN_ON) return

        val lockPrefs = context.getSharedPreferences("mdm_lockscreen", Context.MODE_PRIVATE)
        val title = lockPrefs.getString("title", null)

        // キャッシュがなければ何もしない
        if (title.isNullOrEmpty()) {
            Log.d(TAG, "No cached content, skipping")
            return
        }

        // キャッシュの鮮度確認（4時間以内）
        val updatedAt = lockPrefs.getString("updated_at", null)?.toLongOrNull() ?: 0L
        val ageHours = (System.currentTimeMillis() - updatedAt) / 3_600_000
        if (ageHours > 4) {
            Log.d(TAG, "Cache stale ($ageHours h), requesting refresh")
            PrefetchWorker.runNow(context)
            return
        }

        // 周波数キャップ確認（3回/日）— クライアントサイドチェック
        val today     = FreqCapPrefs.today()
        val countPrefs = context.getSharedPreferences(FreqCapPrefs.PREFS_NAME, Context.MODE_PRIVATE)
        val lastDate  = countPrefs.getString(FreqCapPrefs.KEY_DATE, "")
        val count     = if (lastDate == today) countPrefs.getInt(FreqCapPrefs.KEY_COUNT, 0) else 0

        if (count >= DAILY_CAP) {
            Log.d(TAG, "Frequency cap reached ($count/$DAILY_CAP), skipping")
            return
        }

        // カウントを更新
        countPrefs.edit()
            .putString(FreqCapPrefs.KEY_DATE, today)
            .putInt(FreqCapPrefs.KEY_COUNT, count + 1)
            .apply()

        Log.i(TAG, "Screen on → launching LockscreenActivity (impression ${count + 1}/$DAILY_CAP)")
        LockscreenActivity.launch(context)
    }

    companion object {
        private const val TAG = "ScreenOnReceiver"
        private const val DAILY_CAP = 3
    }
}
