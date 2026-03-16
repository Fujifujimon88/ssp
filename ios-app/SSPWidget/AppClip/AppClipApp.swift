import AppClip
import SwiftUI

/// App Group identifier for this target. Mirrors kAppGroup in the main app target.
/// Defined separately because AppClip is a distinct build target.
let kAppGroup = "group.jp.platform.ssp"

@main
struct SSPAppClipApp: App {
    var body: some Scene {
        WindowGroup {
            AppClipEntryView()
        }
    }
}
