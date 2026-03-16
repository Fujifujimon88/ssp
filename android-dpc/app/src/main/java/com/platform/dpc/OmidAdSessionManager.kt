package com.platform.dpc

import android.content.Context
import android.util.Log
import android.view.View
import com.iab.omid.library.platform.dpc.Omid
import com.iab.omid.library.platform.dpc.adsession.AdSession
import com.iab.omid.library.platform.dpc.adsession.AdSessionConfiguration
import com.iab.omid.library.platform.dpc.adsession.AdSessionContext
import com.iab.omid.library.platform.dpc.adsession.CreativeType
import com.iab.omid.library.platform.dpc.adsession.ImpressionType
import com.iab.omid.library.platform.dpc.adsession.Owner
import com.iab.omid.library.platform.dpc.adsession.Partner
import com.iab.omid.library.platform.dpc.adsession.VerificationScriptResource
import com.iab.omid.library.platform.dpc.adevents.AdEvents
import com.iab.omid.library.platform.dpc.adevents.MediaEvents
import java.net.URL

/**
 * ADT-01 — IAB OM SDK Viewability セッション管理
 *
 * LockscreenActivityから呼ばれ、広告表示開始時にセッションを作成し、
 * impression_occurred/loaded イベントを報告する。
 * 広告消去時には必ずfinishSession()を呼ぶこと。
 */
object OmidAdSessionManager {
    private const val TAG = "OmidAdSessionManager"
    private const val PARTNER_NAME    = "SSPPlatform"
    private const val PARTNER_VERSION = "1.0.0"

    @Volatile private var initialized = false

    fun initialize(context: Context) {
        if (initialized) return
        try {
            if (!Omid.isActive()) {
                Omid.activate(context.applicationContext)
            }
            initialized = true
            Log.i(TAG, "OMID SDK initialized: v${Omid.getVersion()}")
        } catch (e: Exception) {
            Log.w(TAG, "OMID init failed (non-critical): $e")
        }
    }

    /**
     * ネイティブバナー広告のAdSessionを作成する。
     * @param adView 広告を表示しているView（LockscreenActivityのroot view）
     * @return AdSession（終了時にfinishSession()を呼ぶこと）またはnull（SDK未初期化時）
     */
    fun createNativeSession(context: Context, adView: View): AdSession? {
        if (!initialized) return null
        return try {
            val partner = Partner.createPartner(PARTNER_NAME, PARTNER_VERSION)
            val adSessionContext = AdSessionContext.createNativeAdSessionContext(
                partner,
                "",  // custom reference data
                emptyList<VerificationScriptResource>(),
                null,
                null,
            )
            val config = AdSessionConfiguration.createAdSessionConfiguration(
                CreativeType.HTML_DISPLAY,
                ImpressionType.ONE_PIXEL,
                Owner.NATIVE,
                Owner.NONE,
                false,
            )
            val session = AdSession.createAdSession(config, adSessionContext)
            session.registerAdView(adView)
            session.start()
            Log.d(TAG, "OMID native session started")
            session
        } catch (e: Exception) {
            Log.w(TAG, "OMID session creation failed: $e")
            null
        }
    }

    /**
     * AdEventsを生成してimpression/loadedを報告する。
     * createNativeSession()の直後に呼ぶこと。
     */
    fun reportImpression(session: AdSession) {
        try {
            val adEvents = AdEvents.createAdEvents(session)
            adEvents.loaded()
            adEvents.impressionOccurred()
            Log.d(TAG, "OMID impression reported")
        } catch (e: Exception) {
            Log.w(TAG, "OMID impression report failed: $e")
        }
    }

    fun finishSession(session: AdSession?) {
        try {
            session?.finish()
            Log.d(TAG, "OMID session finished")
        } catch (e: Exception) {
            Log.w(TAG, "OMID session finish failed: $e")
        }
    }
}
