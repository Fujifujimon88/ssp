# 開発スケジュール（SSP Platform + モバイル広告）

最終更新: 2026-03-17
競合調査: Digital Turbine (Ignite/SingleTap/DT Exchange) + Glance (InMobi) 分析済み

---

## 完了済み ✅

### フェーズ 1: SSP プラットフォーム基盤

- [x] SSP 基本アーキテクチャ（FastAPI + PostgreSQL/Supabase）
- [x] オークションエンジン（ヘッダービディング）
- [x] DSP 入札インターフェース
- [x] 管理画面 KPI リアルデータ接続
- [x] Ads.txt / sellers.json 実装
- [x] セキュリティ修正
- [x] マルチサイズ対応（DSP 入札率向上）
- [x] E2E テストセットアップ（Playwright）

### フェーズ 2: MDM + ロック画面広告基盤

- [x] MDM エンロールポータル（QRスキャン → デバイス登録）
- [x] iOS .mobileconfig 動的生成（VPN + Webクリップ）
- [x] Android DPC APK 基本実装
- [x] FCM HTTP v1 API（OAuth2移行済み）
- [x] エンロール同意画面 v2.0（6チェックボックス + バックエンド検証）
- [x] ConsentLogDB（同意記録の永続化）
- [x] LockscreenActivity（8秒自動解除 + CTAクリック報告）
- [x] eCPMランキング（Bayesian blending + 周波数キャッピング）
- [x] A/Bテスト基盤（CreativeExperimentDB）
- [x] iOS クリックトラッキング（リダイレクトパターン）
- [x] 全デバイス一括配信API（/mdm/admin/broadcast）
- [x] SSE リアルタイムKPIダッシュボード
- [x] プライバシーポリシーページ・代理店マニュアル
- [x] シードスクリプト（campaigns / creatives / ad slots）
- [x] Alembic マイグレーション（非同期対応）

---

## 進行中・未着手

---

## フェーズ 3: Glance / Digital Turbine 相当機能（収益最大化）

### 3-1. DPCサイレントインストール強化（Digital Turbine SingleTap相当）
> DPCエンロール済み端末は `INSTALL_PACKAGES` 権限があり、Digital Turbineと同等のサイレントインストールが実現可能。これが最大の競合優位。

- [ ] `PackageInstaller.Session` API によるサイレントAPKインストール実装
  - ユーザーダイアログなし（DPCデバイスオーナー権限を活用）
  - インストール完了をサーバーへ即報告（決定論的アトリビューション）
- [ ] APKダウンロードマネージャー（Wi-Fi/充電中に事前DL）
- [ ] インストール完了S2Sポストバック（AppsFlyer/Adjust）
  - 100%確実な紐付け → 詐欺ゼロ証明 → 高CPIを広告主に請求可能
- [ ] CPI課金トリガー（install確認後に自動課金）

**期待値: CVR 5% → 15〜20%（SingleTap同等）、CPI単価 ¥100→¥300〜500**

---

### 3-2. コンテンツプリフェッチ（Glance競合優位の核心）
> 画面点灯 → 即表示。APIコール待ち0ms。Glanceが1日70回のエンゲージを維持できている根本理由。

**Android DPC側:**
- [ ] WorkManager による定期バックグラウンドフェッチ
  - 充電中 + Wi-Fi 接続時のみ実行（バッテリー影響ゼロ）
  - 次の広告コンテンツをSharedPreferencesにキャッシュ
- [ ] スクリーンオンイベントでキャッシュから即レンダリング
  - `Intent.ACTION_SCREEN_ON` レシーバー実装
  - APIコール不要、サーバー障害でも表示可能

**バックエンド側:**
- [ ] コンテンツプリフェッチAPI（`GET /mdm/prefetch/{device_id}`）
  - 次の3件の広告を一括返却（impression_id付き）
  - 端末側でキューイング

**期待値: 広告表示成功率 70% → 98%、CTR +20〜40%、サーバーコスト 1/10**

---

### 3-3. Lock screen専用KPI指標（プレミアム枠の価格根拠）
> データがなければ価格交渉できない。「朝7〜8時の1回目点灯」はCPM3倍で売れる。

- [ ] `mdm_impressions` テーブルに指標カラム追加
  - `screen_on_count_today`（その日何回目の点灯か）
  - `dwell_time_ms`（広告が表示されていた時間）
  - `dismiss_type`（swipe_dismiss / cta_tap / auto_dismiss）
  - `hour_of_day`（何時に表示されたか）
- [ ] Android DPC側: 滞留時間計測（onResume/onPause タイムスタンプ）
- [ ] 管理画面: Lock screen専用アナリティクス
  - 時間帯別CTR（朝/昼/夜）
  - 点灯回数別CTR（1回目が最高 → プレミアム枠として販売）
  - スワイプ解除率（ユーザー嫌悪度指標）
- [ ] 広告主向けレポートAPI（Lock screen固有指標を含む）

**期待値: 朝1回目枠をCPM ¥1,500〜3,000で販売可能（通常枠の3倍）**

---

### 3-4. OpenRTB 2.5 DSP接続（収益の天井突破）
> 直販だけでは空き枠の収益がゼロ。RTBで自動的に最高値で売れる仕組みを作る。

- [ ] OpenRTB 2.5 bid request 送信エンジン
  - Lock screen impression発生 → 250ms以内に複数DSPへ同時入札依頼
  - 必須フィールド: `device.ua`, `device.geo`, `device.os`, `imp.banner`, `app.bundle`
- [ ] 国内DSP接続（優先順）
  1. i-mobile（国内最大のアフィリエイトDSP、API申請から）
  2. CyberAgent DSP（Ameba Ads）
  3. Google ADX（AdMob mediation経由）
- [ ] フロア価格管理（DSP落札価格 < フロア → 自社直販クリエイティブにフォールバック）
- [ ] Take rate設定（DSP落札額の15〜20%をプラットフォームが取得）
- [ ] DSP別パフォーマンスレポート

**期待値: 空き枠収益化 + 繁忙期（年末商戦）でCPM自動上昇**

---

### 3-5. 動画広告プリキャッシュ（AdColony Aurora HD相当）
> 日本のゲームアプリ広告は動画が最高CPM（¥2,000〜5,000/千回）。ローディングなし即再生が必須。

- [ ] バックグラウンド動画プリDL（WorkManager + ExoPlayer）
  - MPEG-DASH ABR（回線速度に応じて画質自動調整）
  - ストレージ上限設定（最大50MB/端末）
- [ ] 動画広告フォーマット対応（VAST 3.0）
  - フルスクリーンインタースティシャル（ロック画面解除後）
  - 報酬型動画（視聴完了でポイント付与）
- [ ] 動画インプレッション計測（再生開始・25/50/75/100%通過）
- [ ] Lock screen解除後の動画インタースティシャル配信フロー

**期待値: 動画CPM ¥2,000〜5,000（現在のバナーCPM ¥500の4〜10倍）**

---

### 3-6. Two-Tower 推薦モデル（Glance競合優位の核心・中長期）
> 全員に同じ広告 → eCPM低下の悪循環を断ち切る。パーソナライズでCTRを2〜5倍に。

- [ ] ユーザー特徴量収集
  - エンロール時属性（年齢層、キャリア、機種、店舗エリア）
  - 行動ログ（クリック履歴、滞留時間、dismiss種別）
  - 時間帯パターン（朝型/夜型）
- [ ] Two-Tower モデル設計
  - User Tower: ユーザー属性 + 行動履歴 → ユーザー埋め込みベクトル
  - Item Tower: クリエイティブカテゴリ + 実績CTR → アイテム埋め込みベクトル
  - 内積でスコアリング → eCPMと組み合わせた最終ランキング
- [ ] TFLite変換 + Android端末推論（プライバシー保護 + レイテンシゼロ）
- [ ] MLflow でモデルバージョン管理

**期待値: CTR 2〜5倍 → eCPM上昇 → 広告主ROI向上 → 予算増額の好循環**

---

## フェーズ 4: iOS 強化 + iOSウィジェット

### 4-1. iOS ウィジェット（手動誘導 + インセンティブ設計）

> Apple の制約: MDM でホーム画面・ロック画面ウィジェットの強制追加は不可。
> 方針: インセンティブでユーザーに自発的に設定させる。

- [ ] VPN アプリ（iOS）App Store 審査対応設計
- [ ] WidgetKit Extension 実装
  - ホーム画面ウィジェット（ポイント残高 + 今日のクーポン + 広告枠）
  - ロック画面ウィジェット（iOS 16+、ポイント残高 + クーポン残数）
- [ ] ポイント・クーポン機能（ウィジェット設定完了でボーナス付与）
- [ ] エル投げ連携（ウィジェット設定案内を LINE で自動送信）
- [ ] WidgetKit → SSP API 呼び出し（TimelineProvider + Background refresh）
- [ ] 設定率KPI管理（目標: 50〜70%）

### 4-2. NanoMDM + APNs（iOS MDM本格稼働）

- [ ] NanoMDM Go バイナリをFastAPIと並行起動
- [ ] APNs証明書取得（Apple Developer Portal、手作業2時間）
- [ ] MDMチェックイン → コマンドキュー
- [ ] OTAプロファイル更新（VPN設定変更を遠隔反映）
- [ ] App Clips実装（NFC/QR起動、クーポン・ポイント表示）

---

## フェーズ 5: アフィリエイト管理 + 計測統合

- [ ] GTM自動埋め込み（WebクリップLPにGTMコンテナIDを自動挿入）
- [ ] S2Sポストバック AppsFlyer（`s2s.appsflyer.com/api/v2/installs`）
- [ ] S2Sポストバック Adjust（`s2s.adjust.com/event`）
- [ ] 広告主管理画面（GTM Key / AppsFlyer Dev Key / Adjust App Token 登録UI）
- [ ] 収益自動精算エンジン（CPI/CPS/月額を自動集計）
- [ ] 代理店別月次レポート・支払い計算

---

## フェーズ 6: ダッシュボード統合 + スケール

- [ ] 代理店ポータル（QR管理・端末数・収益レポート）
- [ ] 広告主ポータル（キャンペーン設定・配信実績・CV数）
- [ ] VPS本番デプロイ（HTTPS + systemd + Nginx）
- [ ] Android APK ビルド + 署名（Play Store or 直接配布）
- [ ] LINE公式アカウント設定（エル投げ連携）
- [ ] 代理店実機テスト

---

## 収益目標（フェーズ3完了後）

> 前提: 周波数キャップ3回/日 × 1万台 × 30日 = 90万インプレッション/月

| 指標 | 現在 | フェーズ3完了後 |
|------|------|----------------|
| 広告表示成功率 | 70% | 98% |
| 平均CTR | 2% | 4〜8%（パーソナライズ後）|
| CPM単価（通常枠）| ¥500 | ¥800 |
| CPM単価（朝プレミアム枠）| ¥500 | ¥2,000〜3,000 |
| CPM単価（動画）| なし | ¥2,000〜5,000 |
| CPI単価（サイレントインストール）| ¥300 | ¥400〜500 |
| CPI CVR | 5%（手動誘導）| 15〜20%（サイレント）|
| **1万台あたり月収** | **¥60〜70万** | **¥280〜300万** |
| **10万台あたり月収** | **¥600〜700万** | **¥2,800〜3,000万** |
| **100万台あたり月収** | **¥6,000〜7,000万** | **¥2.8〜3億** |

### 収益内訳（1万台・フェーズ3完了後）
| 収益源 | 月収 | 構成比 |
|--------|------|--------|
| ロック画面 朝プレミアム枠 | ¥60万 | 21% |
| ロック画面 通常枠 | ¥48万 | 17% |
| 動画広告（解除後）| ¥30万 | 11% |
| **CPI サイレントインストール** | **¥80〜200万** | **28〜50%** |
| OpenRTB DSP 空き枠 | +30% | — |
| **合計** | **¥280〜300万** | |

---

## 競合優位まとめ

```
Digital Turbine の弱点（日本）
├── Docomo/au との関係なし → 我々は代理店ネットワークで全キャリア対応可
└── 日本語/APPI対応が弱い → 同意UI実装済み

Glance の弱点（日本）
├── SoftBankのみ → 代理店経由で全キャリア端末対応可
└── コンテンツパートナーが少ない → Yahoo Japan/LINE/楽天と直接交渉余地あり

我々の差別化軸:
✅ DPCサイレントインストール（Digital Turbine Ignite相当）
✅ ロック画面広告 LockscreenActivity（Glance相当、実装済み）
✅ 携帯代理店ネットワーク（両社にない店頭接点）
✅ APPI完全準拠の同意UI（実装済み）
✅ エル投げ連携（LINE自動化、両社にない）
```

---

## メモ・制約事項

- iOS WidgetKit: バックグラウンド更新頻度は OS が制御（1日数回）
- iOS ロック画面ウィジェット: iOS 16 以上のみ対象
- Apple ガイドライン: ウィジェット内に動画広告不可、静止画・テキストのみ
- SYSTEM_ALERT_WINDOW (Android): targetSdk 33 以上では権限ダイアログ必須
- OpenRTB DSP接続: 各社との契約・審査が必要（最短2〜4週間）
- APNs証明書: Apple Developer Portal で手作業取得（有料開発者アカウント必須）
