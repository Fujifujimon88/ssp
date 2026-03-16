import AppClip
import SwiftUI

extension AppClipEntryView {
    /// NFC / QR コードからの起動時にURLを解析してdealer_idを取得する
    func handleUserActivity(_ userActivity: NSUserActivity) {
        guard userActivity.activityType == NSUserActivityTypes.appClipActivation,
              let url = userActivity.webpageURL,
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else { return }

        for item in components.queryItems ?? [] {
            switch item.name {
            case "dealer_id", "dealer": dealerId    = item.value ?? ""
            case "campaign":            campaignId  = item.value ?? ""
            default: break
            }
        }
    }
}

private extension NSUserActivityTypes {
    static let appClipActivation = "NSUserActivityTypeBrowsingWeb"
}
