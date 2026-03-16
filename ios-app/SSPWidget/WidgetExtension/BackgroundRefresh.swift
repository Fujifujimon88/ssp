import BackgroundTasks
import UIKit

/// iOS-01: BGAppRefreshTask — フォアグラウンド復帰時により頻繁にウィジェット更新を促す
/// Info.plist に BGTaskSchedulerPermittedIdentifiers: ["jp.platform.ssp.widget-refresh"] を追加すること
final class BackgroundRefreshScheduler {

    static let taskIdentifier = "jp.platform.ssp.widget-refresh"

    static func register() {
        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: taskIdentifier,
            using: nil
        ) { task in
            handleRefresh(task: task as! BGAppRefreshTask)
        }
    }

    static func schedule() {
        let request = BGAppRefreshTaskRequest(identifier: taskIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)  // 15分後
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handleRefresh(task: BGAppRefreshTask) {
        schedule()  // 次回スケジュール

        // WidgetCenter にリロードを要求
        WidgetCenter.shared.reloadAllTimelines()
        task.setTaskCompleted(success: true)
    }
}
