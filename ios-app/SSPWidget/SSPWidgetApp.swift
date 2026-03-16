import SwiftUI

/// App Group identifier shared between main app and widget extension.
let kAppGroup = "group.jp.platform.ssp"

/// Production server URL — override via Xcode Build Settings (SSP_SERVER_URL)
/// so the same binary works for staging and production without code changes.
private let defaultServerURL: String = {
    // Check build-time injected value first (set via .xcconfig or scheme env)
    if let url = Bundle.main.object(forInfoDictionaryKey: "SSPServerURL") as? String,
       !url.isEmpty, url != "$(SSP_SERVER_URL)" {
        return url
    }
    return "https://mdm.example.com"
}()

@main
struct SSPWidgetApp: App {

    init() {
        // Write server_url into the shared App Group so the WidgetExtension
        // can read it without needing its own network config.
        let shared = UserDefaults(suiteName: kAppGroup)
        // Only set if not already overridden by a previous enrollment flow.
        if shared?.string(forKey: "server_url") == nil {
            shared?.set(defaultServerURL, forKey: "server_url")
        }
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
