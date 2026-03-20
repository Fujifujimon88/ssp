# MDM アフィリエイト計測ガイド

## 計測方式

| 方式 | cv_trigger | 発火タイミング | 用途 |
|------|-----------|--------------|------|
| Method 1 (CPI) | `install` | プリインストール完了 → 即CV | アプリ導入数課金 |
| Method 2 (CPE) | `app_open` | プッシュ通知タップ → アプリ起動 → CV | 起動確認型課金 |

### cv_trigger 優先順位（高→低）
```
① DealerDB.default_cv_trigger  （代理店レベルで強制）
② AffiliateCampaignDB.cv_trigger（キャンペーンレベル）
③ "install"（デフォルト）
```

---

## postback_url_template — テンプレート変数一覧

キャンペーン登録時に `postback_url_template` フィールドへ設定するURL。
以下の変数が使用可能（`{変数名}` 形式）：

| 変数 | 内容 | 例 |
|------|------|-----|
| `{device_id}` | Android ID | `a1b2c3d4e5f6a7b8` |
| `{enrollment_token}` | エンロールトークン（URLエンコード済） | `abc123xyz` |
| `{dealer_id}` | 代理店ID | `uuid-xxxx` |
| `{store_id}` | 店舗ID | `uuid-yyyy` |
| `{amount}` | CV単価（円・整数） | `500` |
| `{install_ts}` | インストール時刻（Unix ms） | `1742400000000` |
| `{package_name}` | APKパッケージ名（URLエンコード済） | `com.example.app` |
| `{event_type}` | イベント種別 | `install` or `app_open` |

---

## ASP別 URL 設定例

### JANet（インバウンド型 — JANetが我々のエンドポイントを叩く）
```
【スキーム】
① クリックURL生成:
   GET /mdm/affiliate/click/{campaign_id}?device_id={android_device_id}
   → 302 Redirect: https://click.j-a-net.jp/{janet_media_id}/{janet_original_id}/{device_id}
   ※ JANetの仕様: UserID はパスに直接付与（?key=value 形式は不可）

② JANet管理画面にポストバックURLを登録:
   https://your-server.com/mdm/affiliate/postback/janet?uid={uid}&price={price}&ad={ad}

③ CV発生時、JANetが②のURLを叩いてCV通知
   → device_id で AffiliateClickDB を照合 → InstallEventDB を billable に更新
```

キャンペーン登録時の設定:
```json
{
  "janet_media_id": "12345",
  "janet_original_id": "67890"
}
```

### smaad（JANetと同スキーム — インバウンド型）
```
【スキーム】
① クリックURL生成:
   GET /mdm/affiliate/click/{campaign_id}?device_id={android_device_id}
   → 302 Redirect: https://tr.smaad.net/redirect?zo=745468462&ad=198337123&uid={device_id}
   ※ click_url_template に {device_id} プレースホルダを使用（クエリパラメータ形式）

② smaad管理画面にポストバックURLを登録:
   https://your-server.com/mdm/affiliate/postback/smaad?uid={uid}&price={price}

③ CV発生時、smaadが②のURLを叩いてCV通知
   → uid(device_id) で AffiliateClickDB を照合 → InstallEventDB を billable に更新
```

キャンペーン登録時の設定:
```json
{
  "click_url_template": "https://tr.smaad.net/redirect?zo=745468462&ad=198337123&uid={device_id}"
}
```
> ※ `zo`（ゾーンID）と `ad`（広告ID）は smaad 管理画面から取得

### A8.net（JANetと同スキーム — インバウンド型）
```
【スキーム】
① クリックURL生成:
   GET /mdm/affiliate/click/{campaign_id}?device_id={android_device_id}
   → 302 Redirect: https://px.a8.net/a8fly/earnings?a8mat=XXXXXXXX&uid={device_id}

② A8.net管理画面にポストバックURLを登録:
   https://your-server.com/mdm/affiliate/postback/a8?uid={uid}&price={price}

③ CV発生時、A8.netが②のURLを叩いてCV通知
   → uid(device_id) で AffiliateClickDB を照合 → InstallEventDB を billable に更新
```

キャンペーン登録時の設定:
```json
{
  "click_url_template": "https://px.a8.net/a8fly/earnings?a8mat=XXXXXXXX&uid={device_id}"
}
```
> ※ `a8mat` は A8.net の計測パラメータ（案件ごとに発行）

### ASP別 設定フィールドまとめ

| ASP | クリックURLの形式 | 設定フィールド | ポストバック受信URL |
|-----|----------------|--------------|------------------|
| JANet | パス形式 `/{media_id}/{original_id}/{device_id}` | `janet_media_id` + `janet_original_id` | `/affiliate/postback/janet` |
| smaad | クエリパラメータ `?uid={device_id}` | `click_url_template` | `/affiliate/postback/smaad` |
| A8.net | クエリパラメータ `?uid={device_id}` | `click_url_template` | `/affiliate/postback/a8` |

> **共通ポイント**: 3社とも「弊社→ASP（クリック時リダイレクト）」→「広告主サイト表示」→「CV発生→ASP→弊社（ポストバック受信）」の同一スキーム。

### Felmat
```
https://t.felmat.net/fmcv?ak=XXXXX&ev=install&price={amount}&uid={device_id}&dealer={dealer_id}
```
> ※ Felmat は `postback_url_template`（DPCインストール起点のアウトバウンド型）に分類

### ValueCommerce
```
https://ad.jp.ap.valuecommerce.com/servlet/gifbanner?sid=XXXXX&pid=XXXXX&vc_url={package_name}&price={amount}&uid={device_id}
```
> ※ ValueCommerce は `postback_url_template`（アウトバウンド型）に分類

---

## API エンドポイント

### キャンペーン登録例: JANet
```
POST /mdm/admin/affiliate/campaigns
{
  "name": "JANet案件: 春のアプリインストール",
  "category": "app",
  "destination_url": "https://example.com/lp",
  "reward_type": "cpi",
  "reward_amount": 500,
  "cv_trigger": "install",
  "janet_media_id": "12345",
  "janet_original_id": "67890"
}
```

### キャンペーン登録例: smaad / A8.net（click_url_template を使用）
```
POST /mdm/admin/affiliate/campaigns
{
  "name": "smaad案件: 春のアプリインストール",
  "category": "app",
  "destination_url": "https://example.com/lp",
  "reward_type": "cpi",
  "reward_amount": 500,
  "cv_trigger": "install",
  "click_url_template": "https://tr.smaad.net/redirect?zo=745468462&ad=198337123&uid={device_id}"
}
```

### デバイス登録（代理店・店舗紐づけ）
```
POST /mdm/android/register
{
  "device_id": "android_id_here",
  "enrollment_token": "token_here",
  "fcm_token": "fcm_token_here",
  "dealer_id": "代理店UUID",
  "store_id":  "店舗UUID",
  "gaid": "google-adv-id"  // GAIDがある場合
}
```

### APKインストールコマンド送信（campaign_id を必ず含める）
```
POST /mdm/admin/android/push
{
  "device_id": "android_id_here",
  "command_type": "install_apk",
  "payload": {
    "package_name": "com.example.app",
    "app_url": "https://cdn.example.com/app.apk",
    "title": "テストアプリ",
    "campaign_id": "キャンペーンUUID"   ← サーバーが保存してCV追跡に使用
  },
  "send_fcm": true
}
```

### Method 2 のみ: アプリ起動報告（DPCが送信）
```
POST /mdm/android/app_open
{
  "device_id": "android_id_here",
  "package_name": "com.example.app",
  "trigger": "push_tap"
}
```

### 店舗別CVレポート
```
GET /mdm/admin/affiliate/report/store/{store_id}?year=2026&month=3
→ { store_id, dealer_id, period, total_cv, total_revenue_jpy, events: [...] }
```

---

## フロー図

### Method 1（即時CV）
```
Admin → POST /admin/android/push  { campaign_id: "xxx" }
          ↓ AndroidCommandDB に campaign_id + store_id を保存
DPC   → APKサイレントインストール
DPC   → POST /install_confirmed
Server: campaign_id をコマンドキューから解決（DPC送信値はフォールバック）
        dealer.default_cv_trigger or campaign.cv_trigger → "install"
        InstallEventDB { cv_method: "install", dealer_id, store_id }
        → ASP直接ポストバック発火（{dealer_id} {store_id} 付き）
```

### Method 2（プッシュ起動後CV）
```
（インストールまでは同じ）
Server: cv_trigger = "app_open"
        InstallEventDB { cv_method: "pending_app_open" }  ← ポストバック保留
Admin → POST /admin/android/push  { command_type: "show_notification", ... }
DPC   → 通知タップ → POST /android/app_open
Server: cv_method → "app_open", app_open_at を記録
        → ASP直接ポストバック発火（event_type="app_open"）
```

---

## DPC側の追加実装（Method 2 のみ）

```kotlin
// FCM通知ハンドラーに追記
val appOpenUrl = remoteMessage.data["app_open_url"]
if (!appOpenUrl.isNullOrEmpty()) {
    HttpClient.post(appOpenUrl, JsonObject(mapOf(
        "device_id" to deviceId,
        "package_name" to packageName,
        "trigger" to "push_tap"
    )))
}
```
> `app_open_url` は show_notification コマンドの payload に含めて送信する。

---

## PostbackLogDB — ポストバック送信ログの確認

provider カラムの値：
- `appsflyer` — AppsFlyer S2S
- `adjust` — Adjust S2S
- `direct_asp` — ASP直接ポストバック（smaad / A8.net 等）
