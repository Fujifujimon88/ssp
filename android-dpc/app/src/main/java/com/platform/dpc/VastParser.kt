package com.platform.dpc

import android.util.Log
import org.xml.sax.InputSource
import java.io.StringReader
import javax.xml.parsers.DocumentBuilderFactory

data class VastAd(
    val mediaFileUrl: String,
    val impressionUrls: List<String>,
    val trackingEvents: Map<String, List<String>>,  // event -> list of URLs
)

object VastParser {
    private const val TAG = "VastParser"

    fun parse(vastXml: String): VastAd? {
        return try {
            val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder()
                .parse(InputSource(StringReader(vastXml)))
            doc.documentElement.normalize()

            // MediaFile: prefer mp4, fallback to first
            val mediaFiles = doc.getElementsByTagName("MediaFile")
            var mediaUrl = ""
            for (i in 0 until mediaFiles.length) {
                val el = mediaFiles.item(i)
                val mime = el.attributes.getNamedItem("type")?.nodeValue ?: ""
                val url = el.textContent.trim()
                if (mime.contains("mp4") || mime.contains("mpeg")) {
                    mediaUrl = url; break
                }
                if (mediaUrl.isEmpty()) mediaUrl = url
            }
            if (mediaUrl.isEmpty()) return null

            // Impressions
            val impressions = mutableListOf<String>()
            val impNodes = doc.getElementsByTagName("Impression")
            for (i in 0 until impNodes.length) {
                val url = impNodes.item(i).textContent.trim()
                if (url.isNotEmpty()) impressions.add(url)
            }

            // Tracking events
            val trackingMap = mutableMapOf<String, MutableList<String>>()
            val trackingNodes = doc.getElementsByTagName("Tracking")
            for (i in 0 until trackingNodes.length) {
                val el = trackingNodes.item(i)
                val event = el.attributes.getNamedItem("event")?.nodeValue ?: continue
                val url = el.textContent.trim()
                if (url.isNotEmpty()) {
                    trackingMap.getOrPut(event) { mutableListOf() }.add(url)
                }
            }

            VastAd(mediaUrl, impressions, trackingMap)
        } catch (e: Exception) {
            Log.w(TAG, "VAST parse error: $e")
            null
        }
    }
}
