import WidgetKit
import SwiftUI

// MARK: - Data Model

struct WidgetContent: Codable {
    let device_id: String
    let points_balance: Int
    let coupon_count: Int
    let ad: AdContent?
    let updated_at: String
    let refresh_interval_minutes: Int
}

struct AdContent: Codable {
    let image_url: String
    let title: String
    let cta_url: String
    let impression_id: String?
}

struct WidgetEntry: TimelineEntry {
    let date: Date
    let content: WidgetContent?
}

// MARK: - Timeline Provider

struct SSPTimelineProvider: TimelineProvider {

    let serverURL = UserDefaults(suiteName: "group.jp.platform.ssp")?.string(forKey: "server_url")
        ?? "https://mdm.example.com"
    let deviceId  = UserDefaults(suiteName: "group.jp.platform.ssp")?.string(forKey: "device_id")
        ?? "unknown"

    func placeholder(in context: Context) -> WidgetEntry {
        WidgetEntry(date: Date(), content: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (WidgetEntry) -> Void) {
        completion(WidgetEntry(date: Date(), content: nil))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<WidgetEntry>) -> Void) {
        let url = URL(string: "\(serverURL)/mdm/ios/widget_content/\(deviceId)")!
        URLSession.shared.dataTask(with: url) { data, _, _ in
            var entry: WidgetEntry
            if let data = data,
               let content = try? JSONDecoder().decode(WidgetContent.self, from: data) {
                entry = WidgetEntry(date: Date(), content: content)
            } else {
                entry = WidgetEntry(date: Date(), content: nil)
            }
            // Refresh in 30 minutes (OS may throttle)
            let nextUpdate = Calendar.current.date(byAdding: .minute, value: 30, to: Date())!
            completion(Timeline(entries: [entry], policy: .after(nextUpdate)))
        }.resume()
    }
}

// MARK: - Widget Views

@main
struct SSPWidgetBundle: WidgetBundle {
    var body: some Widget {
        SSPHomeWidget()
        SSPLockScreenWidget()
    }
}

// ── Home screen widget（全サイズ）────────────────────────────────

struct SSPHomeWidget: Widget {
    let kind = "SSPHomeWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: SSPTimelineProvider()) { entry in
            SSPHomeWidgetView(entry: entry)
        }
        .configurationDisplayName("SSP ポイント＆クーポン")
        .description("ポイント残高・本日のクーポン・おすすめ情報を表示します。")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge])
    }
}

struct SSPHomeWidgetView: View {
    let entry: WidgetEntry

    var body: some View {
        if let content = entry.content {
            VStack(alignment: .leading, spacing: 8) {
                // ポイント残高
                HStack {
                    Image(systemName: "star.fill")
                        .foregroundColor(.yellow)
                    Text("\(content.points_balance) pt")
                        .font(.headline.bold())
                        .foregroundColor(.primary)
                }

                // クーポン数
                if content.coupon_count > 0 {
                    Label("\(content.coupon_count)件のクーポン", systemImage: "ticket.fill")
                        .font(.caption)
                        .foregroundColor(.blue)
                }

                // 広告バナー（静止画）
                if let ad = content.ad, let url = URL(string: ad.image_url) {
                    AsyncImage(url: url) { image in
                        image.resizable().aspectRatio(contentMode: .fit)
                    } placeholder: {
                        Color.gray.opacity(0.2)
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 8))

                    if !ad.title.isEmpty {
                        Text(ad.title)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }
                }

                Spacer()

                Text(entry.date, style: .time)
                    .font(.caption2)
                    .foregroundColor(.tertiary)
            }
            .padding()
            .widgetURL(URL(string: "ssp://home"))
        } else {
            // プレースホルダー
            VStack {
                Image(systemName: "gift.circle.fill")
                    .foregroundColor(.blue)
                    .font(.largeTitle)
                Text("読み込み中...")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding()
        }
    }
}

// ── Lock screen widget（iOS 16+、静止画のみ）─────────────────────

struct SSPLockScreenWidget: Widget {
    let kind = "SSPLockScreenWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: SSPTimelineProvider()) { entry in
            SSPLockScreenWidgetView(entry: entry)
        }
        .configurationDisplayName("SSP ポイント")
        .description("ポイント残高とクーポン件数を表示します。")
        .supportedFamilies([.accessoryRectangular, .accessoryCircular, .accessoryInline])
    }
}

struct SSPLockScreenWidgetView: View {
    @Environment(\.widgetFamily) var family
    let entry: WidgetEntry

    var body: some View {
        let points  = entry.content?.points_balance ?? 0
        let coupons = entry.content?.coupon_count   ?? 0

        switch family {
        case .accessoryRectangular:
            VStack(alignment: .leading) {
                Label("\(points) pt", systemImage: "star.fill")
                    .font(.headline)
                if coupons > 0 {
                    Label("\(coupons)件のクーポン", systemImage: "ticket.fill")
                        .font(.caption)
                }
            }
        case .accessoryCircular:
            VStack {
                Image(systemName: "star.fill")
                Text("\(points)")
                    .font(.caption2.bold())
            }
        case .accessoryInline:
            Text("★ \(points)pt  🎫 \(coupons)")
        default:
            Text("\(points) pt")
        }
    }
}
