import SwiftUI

struct ContentView: View {
    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "gift.circle.fill")
                .font(.system(size: 60))
                .foregroundColor(.blue)
            Text("SSP Platform")
                .font(.title2.bold())
            Text("ウィジェットをホーム画面に追加して\nポイントやクーポンを確認できます。")
                .multilineTextAlignment(.center)
                .foregroundColor(.secondary)
                .font(.subheadline)
            Button("ウィジェットを追加する") {
                // Deep link to home screen widget add flow
                if let url = URL(string: "ssp://add-widget") {
                    UIApplication.shared.open(url)
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding()
    }
}
