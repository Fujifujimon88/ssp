import AppClip
import SwiftUI

/**
 * iOS-03 — SSP App Clip
 *
 * NFC/QR起動エントリーポイント。
 * appclip.platform.jp/enroll?dealer_id=XXX でディープリンク起動。
 *
 * フロー:
 *   1. クーポン/ポイントティーザー表示
 *   2. 同意サマリー（MDMエンロール説明）
 *   3. ウェブビューでエンロールポータルを開く
 *   4. エンロール完了後にフルアプリインストールCTAを表示
 */
struct AppClipEntryView: View {

    @State private var dealerId: String = ""
    @State private var campaignId: String = ""
    @State private var phase: Phase = .teaser

    enum Phase { case teaser, consent, enrolling, done }

    let serverURL = "https://mdm.example.com"

    var body: some View {
        switch phase {
        case .teaser:
            TeaserView(dealerId: dealerId) {
                withAnimation { phase = .consent }
            }
        case .consent:
            ConsentView {
                withAnimation { phase = .enrolling }
            }
        case .enrolling:
            EnrollWebView(
                url: enrollURL,
                onComplete: { withAnimation { phase = .done } }
            )
        case .done:
            DoneView()
        }
    }

    var enrollURL: URL {
        var components = URLComponents(string: "\(serverURL)/mdm/portal")!
        var items = [URLQueryItem(name: "dealer", value: dealerId)]
        if !campaignId.isEmpty { items.append(URLQueryItem(name: "campaign", value: campaignId)) }
        items.append(URLQueryItem(name: "source", value: "appclip"))
        components.queryItems = items
        return components.url!
    }
}

// MARK: - Teaser

struct TeaserView: View {
    let dealerId: String
    let onContinue: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "gift.fill")
                .font(.system(size: 64))
                .foregroundColor(.blue)
            Text("500ポイント プレゼント")
                .font(.title.bold())
            Text("今すぐ登録して\nお得なクーポンとポイントを受け取ろう")
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
            HStack(spacing: 12) {
                Label("ポイント還元", systemImage: "star.fill").font(.caption)
                Label("クーポン配信", systemImage: "ticket.fill").font(.caption)
                Label("お得情報", systemImage: "bell.fill").font(.caption)
            }
            .foregroundColor(.blue)
            Spacer()
            Button(action: onContinue) {
                Text("登録して受け取る")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.blue)
                    .foregroundColor(.white)
                    .cornerRadius(14)
            }
            .padding(.horizontal)
            Text("dealer: \(dealerId.isEmpty ? "直接アクセス" : dealerId)")
                .font(.caption2)
                .foregroundColor(.tertiary)
            Spacer(minLength: 32)
        }
        .padding()
    }
}

// MARK: - Consent

struct ConsentView: View {
    let onContinue: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            Text("サービス概要")
                .font(.title2.bold())
                .padding(.top, 32)
            VStack(alignment: .leading, spacing: 12) {
                ConsentItem(icon: "gift", text: "ポイント・クーポンをお届けします")
                ConsentItem(icon: "bell", text: "お得な情報をお知らせします")
                ConsentItem(icon: "lock.shield", text: "個人情報は適切に管理されます")
                ConsentItem(icon: "hand.raised", text: "いつでも配信停止できます")
            }
            .padding()
            .background(Color(.systemGray6))
            .cornerRadius(12)
            .padding(.horizontal)
            Spacer()
            Button(action: onContinue) {
                Text("同意して登録する")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.blue)
                    .foregroundColor(.white)
                    .cornerRadius(14)
            }
            .padding(.horizontal)
            Text("続行することで利用規約とプライバシーポリシーに同意したことになります。")
                .font(.caption2)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal)
            Spacer(minLength: 32)
        }
    }
}

struct ConsentItem: View {
    let icon: String
    let text: String
    var body: some View {
        HStack {
            Image(systemName: icon).foregroundColor(.blue).frame(width: 24)
            Text(text).font(.subheadline)
        }
    }
}

// MARK: - Enroll WebView

import WebKit

struct EnrollWebView: UIViewRepresentable {
    let url: URL
    let onComplete: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onComplete: onComplete) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let wv = WKWebView(frame: .zero, configuration: config)
        wv.navigationDelegate = context.coordinator
        wv.load(URLRequest(url: url))
        return wv
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    class Coordinator: NSObject, WKNavigationDelegate {
        let onComplete: () -> Void
        init(onComplete: @escaping () -> Void) { self.onComplete = onComplete }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            // エンロール完了ページを検出
            if webView.url?.path.contains("enrolled") == true ||
               webView.url?.path.contains("complete") == true {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    self.onComplete()
                }
            }
        }
    }
}

// MARK: - Done / Full App CTA

struct DoneView: View {
    var body: some View {
        VStack(spacing: 24) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 72))
                .foregroundColor(.green)
            Text("登録完了！")
                .font(.title.bold())
            Text("500ポイントが付与されました。\nフルアプリをインストールしてウィジェットを設定すると、さらにお得な情報をお届けします。")
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
                .padding(.horizontal)
            Spacer()
            // フルアプリインストールCTA
            if let url = URL(string: "https://apps.apple.com/app/ssp-platform/id0000000000") {
                Link(destination: url) {
                    Text("フルアプリをインストール")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.blue)
                        .foregroundColor(.white)
                        .cornerRadius(14)
                }
                .padding(.horizontal)
            }
            Spacer(minLength: 32)
        }
        .padding()
    }
}
