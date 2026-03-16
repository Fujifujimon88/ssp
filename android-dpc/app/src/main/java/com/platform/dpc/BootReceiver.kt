package com.platform.dpc

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * 端末起動時にWorkerManagerのスケジュールを再設定する。
 * WorkManagerはリブート後も自動で再登録されるが、念のため明示的に呼ぶ。
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Log.i(TAG, "Boot completed, scheduling CommandPoller")
            CommandPoller.schedule(context)
        }
    }

    companion object {
        private const val TAG = "BootReceiver"
    }
}
