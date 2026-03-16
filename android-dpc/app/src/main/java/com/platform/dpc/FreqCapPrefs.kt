package com.platform.dpc

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 周波数キャップ用 SharedPreferences のキー定数と日付ユーティリティ。
 * ScreenOnReceiver（書込み）と LockscreenKpiReporter（読取り）で共有する。
 */
internal object FreqCapPrefs {
    const val PREFS_NAME = "mdm_freq_cap"
    const val KEY_DATE   = "date"
    const val KEY_COUNT  = "count"

    // SimpleDateFormat は生成コストが高いためオブジェクトスコープでキャッシュ。
    // 同期アクセスは IO コルーチン（シングルスレッド）前提のため問題なし。
    private val DATE_FMT = SimpleDateFormat("yyyyMMdd", Locale.getDefault())

    fun today(): String = DATE_FMT.format(Date())
}
