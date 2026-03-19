package com.platform.dpc

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/**
 * DPC-04 — MDM常駐フォアグラウンドサービス
 *
 * エンロール同意済みユーザー向けに、バックグラウンドでコンテンツ配信と
 * プリフェッチを維持する。Android 8+のバックグラウンド制限に対応。
 *
 * 起動条件: ユーザーがエンロール同意フロー（6チェックボックス）完了後のみ。
 */
class MdmForegroundService : Service() {

    private var screenOnReceiver: ScreenOnReceiver? = null
    private var wifiNetworkCallback: ConnectivityManager.NetworkCallback? = null

    override fun onCreate() {
        super.onCreate()

        createNotificationChannel()
        startForeground()

        // ScreenOnReceiver を動的登録（ACTION_SCREEN_ON はマニフェスト登録不可）
        screenOnReceiver = ScreenOnReceiver().also { receiver ->
            val filter = IntentFilter(Intent.ACTION_SCREEN_ON)
            registerReceiver(receiver, filter)
            Log.i(TAG, "ScreenOnReceiver registered")
        }

        // Wi-Fi SSID 来店トリガー：Wi-Fi接続イベントを監視
        registerWifiCallback()

        // WorkManagerジョブを確認・起動
        CommandPoller.schedule(this)
        PrefetchWorker.schedule(this)

        Log.i(TAG, "MdmForegroundService started")
    }

    override fun onDestroy() {
        super.onDestroy()
        screenOnReceiver?.let {
            unregisterReceiver(it)
            screenOnReceiver = null
        }
        wifiNetworkCallback?.let {
            getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(it)
            wifiNetworkCallback = null
        }
        Log.i(TAG, "MdmForegroundService stopped")
    }

    /**
     * Wi-Fi接続を検知してSSIDを報告する。
     * NetworkCallback は Android 8+ でフォアグラウンドサービス内から登録可能。
     */
    private fun registerWifiCallback() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .build()

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                val wifiManager = applicationContext.getSystemService(WifiManager::class.java)
                    ?: return
                @Suppress("DEPRECATION")
                val rawSsid = wifiManager.connectionInfo?.ssid ?: return
                // Android はSSIDを "<SSID名>" のようにクォートで返す
                val ssid = rawSsid.removeSurrounding("\"")
                if (ssid.isBlank() || ssid == "<unknown ssid>") return

                Log.i(TAG, "Wi-Fi connected: ssid=$ssid")
                WifiCheckinWorker.enqueue(applicationContext, ssid)
            }
        }

        cm.registerNetworkCallback(request, callback)
        wifiNetworkCallback = callback
        Log.i(TAG, "WifiNetworkCallback registered")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int =
        START_STICKY  // 強制終了されても再起動

    override fun onBind(intent: Intent?): IBinder? = null

    // ── プライベート ──────────────────────────────────────────

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "お得情報配信サービス",
                NotificationManager.IMPORTANCE_MIN,  // 最小 — バッジ・音なし
            ).apply {
                description = "エンロール登録済みのお得情報・クーポン配信サービスです"
                setShowBadge(false)
            }
            getSystemService(NotificationManager::class.java)
                ?.createNotificationChannel(channel)
        }
    }

    private fun startForeground() {
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("お得情報配信")
            .setContentText("クーポン・特典情報を受け取っています")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setOngoing(true)
            .build()

        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE)
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            else 0,
        )
    }

    companion object {
        private const val TAG = "MdmForegroundService"
        private const val CHANNEL_ID = "mdm_service"
        private const val NOTIFICATION_ID = 1001

        fun start(context: Context) {
            val intent = Intent(context, MdmForegroundService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
