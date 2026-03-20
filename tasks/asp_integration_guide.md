# ASP連携 完全ガイド

> 最終更新: 2026-03-20

---

## 目次

1. [スキームの全体像](#スキームの全体像)
2. [ユーザーID設計方針](#ユーザーid設計方針)
3. [インバウンド型](#インバウンド型)
   - ASPパラメータ正規化マップ
   - JANet
   - SKYFLAG
   - smaad
   - A8.net
4. [アウトバウンド型](#アウトバウンド型)
   - AppsFlyer S2S
   - Adjust S2S
   - postback_url_template（汎用）
5. [ポイント付与設計](#ポイント付与設計)
6. [進捗サマリー](#進捗サマリー)

---

## スキームの全体像

| 種別 | 誰がCV通知を送るか | 代表ASP | 実装状況 |
|------|------------------|--------|---------|
| **インバウンド型** | ASP → 弊社 | JANet / SKYFLAG / smaad / A8.net | 一部実装済み |
| **アウトバウンド型（計測ツール）** | 弊社 → AppsFlyer/Adjust | AppsFlyer / Adjust | ✅ 実装済み |
| **アウトバウンド型（直接）** | 弊社 → ASP | Felmat / ValueCommerce 等 | ✅ 実装済み（テンプレート方式） |

---

## ユーザーID設計方針

### なぜ device_id をASPに渡さないか

`device_id`（Android ID）は **個人識別符号**（APPI：個人情報保護法上の保護対象）であり、外部ASPに直接渡すべきではない。代わりに内部で管理する不透明なトークン（`user_token`）をASPに渡す。

```
AndroidDeviceDB
  device_id   = "a1b2c3d4e5f6a7b8"  ← 内部管理のみ（ASPには一切渡さない）
  user_token  = "f8e2-xxxx-yyyy-..."  ← ASPに渡す不透明UUID（永続）
```

### メリット

| 観点 | 内容 |
|------|------|
| **プライバシー** | ASPは `user_token` しか知らない。device_id は外部に出ない |
| **応用性** | user_token はリセット可能。複数デバイスを1ユーザーに統合も将来対応可 |
| **セキュリティ** | 逆引きできないため、ASP側でデバイスを特定不可 |

### クリック〜ポストバックの流れ

```
クリック時:
  DPC → GET /mdm/affiliate/click/{campaign_id}?user_token={user_token}
             ↓ user_token を AffiliateClickDB に記録
             ↓ ASPへ user_token を渡してリダイレクト

ポストバック受信時:
  ASP → GET /mdm/affiliate/postback/{source}?user_id={user_token}&...
             ↓ user_token → AndroidDeviceDB で device_id を逆引き
             ↓ AffiliateConversionDB 作成
             ↓ ポイント付与（キャンペーン設定に従う）
```

---

## インバウンド型

> **共通スキーム**: 弊社がクリックURLを発行 → ユーザーがASP経由で広告主サイトへ → 成果発生 → ASPが弊社にポストバック通知

```
ユーザー（端末）
  │
  ├─ GET /mdm/affiliate/click/{campaign_id}?user_token={user_token}
  │         ↓ user_token を AffiliateClickDB に記録
  │         ↓ 302 Redirect（ASPクリック計測URLへ）
  ├─ ASPクリック計測URL（user_token を uid/suid 等として付与）
  │         ↓
  ├─ 広告主サイト（ユーザーがCV行動）
  │
ASP
  │  成果確認後
  └─ GET /mdm/affiliate/postback/{source}?{user_id_param}={user_token}&{price_param}={amount}
            ↓ user_token → device_id に逆引き
            ↓ AffiliateConversionDB 作成
            ↓ ポイント付与（enable_points=true のキャンペーンのみ）
```

---

### ASPパラメータ正規化マップ

ASPによってパラメータ名は異なるが、**概念・ロジックは共通**。内部でマッピングして統一処理する。

| 概念（内部名） | JANet | SKYFLAG | smaad | A8.net |
|--------------|-------|---------|-------|--------|
| 弊社ユーザーID | `user_id` | `suid` | `uid` | `uid` |
| 報酬額 | `commission` | `price` | `price` | `price` |
| CV固有ID（冪等性キー） | `action_id` | `cv_id` | なし | なし |
| 2段階通知フラグ | `attestation_flag` | `install` / `pt`+`mcv` | なし | なし |
| キャンペーン識別 | `thanks_id` | — | — | — |

#### 2段階通知の有無

| ASP | 2段階通知 | ポイント付与タイミング |
|-----|----------|-------------------|
| **JANet** | あり | `attestation_flag=0`（approved）受信後 |
| **SKYFLAG** | あり | `install=1` または最終ステップ（`pt=SKYFLAG&mcv=空`）受信後 |
| **smaad** | なし | ポストバック受信時に即付与 |
| **A8.net** | なし | ポストバック受信時に即付与 |

#### JANet 2段階通知の詳細

JANetはアクション発生時と認証時の**2回**ポストバックを送信する：

```
Phase 1（アクション発生時）:
  ?user_id={token}&commission=300&action_id=ACT001
  ※ attestation_flag なし → 内部で "pending" として記録

Phase 2（認証時）:
  ?user_id={token}&commission=300&action_id=ACT001&attestation_flag=0  → approved
  ?user_id={token}&commission=300&action_id=ACT001&attestation_flag=1  → rejected

冪等性: action_id が同一であれば同一CVとして処理
```

---

### JANet

**実装状況: 🔧 仕様定義済み・実連携未実施**

#### ポストバックパラメータ（JANet仕様）

| パラメータ | 内容 |
|-----------|------|
| `thanks_id` | サンクスID（どのプロモーションか） |
| `user_id` | **弊社が渡した user_token** |
| `attestation_flag` | 空=アクション発生、0=認証、1=否認証 |
| `action_time` | アクション発生時刻 |
| `attestation_time` | 成果認証時刻 |
| `commission` | **報酬額**（円） |
| `order_amount` | 注文金額（物販時） |
| `action_id` | JANetが管理するCV固有ID（冪等性キー） |

#### 弊社ポストバック受信URL（JANet管理画面に登録する）

```
https://your-server.com/mdm/affiliate/postback/janet
  ?user_id={user_id}
  &commission={commission}
  &action_id={action_id}
  &attestation_flag={attestation_flag}
  &thanks_id={thanks_id}
```

#### クリックURL形式（JANet仕様: パス形式）

```
https://click.j-a-net.jp/{janet_media_id}/{janet_original_id}/{user_token}
```

#### キャンペーン登録

```http
POST /mdm/admin/affiliate/campaigns
X-Admin-Key: {admin_key}
Content-Type: application/json

{
  "name": "JANet案件名",
  "category": "app",
  "destination_url": "https://example.com/lp",
  "reward_type": "cpi",
  "reward_amount": 500,
  "janet_media_id": "12345",
  "janet_original_id": "67890",
  "enable_points": false
}
```

#### DBフィールド

| フィールド | テーブル | 役割 |
|-----------|--------|------|
| `janet_media_id` | `affiliate_campaigns` | JANetメディアID |
| `janet_original_id` | `affiliate_campaigns` | JANet原稿ID |
| `user_token` | `android_devices` | ASPに渡す不透明ID |
| `asp_action_id` | `affiliate_conversions` | action_id（冪等性） |
| `attestation_status` | `affiliate_conversions` | pending/approved/rejected |

---

### SKYFLAG

**実装状況: 📋 仕様定義済み・実連携未実施**

> JANetと同じ立ち位置のインバウンド型ASP。パラメータ名が異なるだけでロジックは同一。

#### ポストバックパラメータ（SKYFLAG仕様）

SKYFLAGは **2種類のポストバック** を送信する：

**① アプリインストールCV（install=1）**

| パラメータ | 内容 |
|-----------|------|
| `suid` | **弊社が渡した user_token** |
| `install` | `1` = CV確定 |
| `price` | 報酬額 |
| `cv_id` | SKYFLAGのCV固有ID（冪等性キー） |

**② ステップアップCV（pt=SKYFLAG）**

| パラメータ | 内容 |
|-----------|------|
| `suid` | **弊社が渡した user_token** |
| `pt` | `SKYFLAG` 固定 |
| `mcv` | ステップ番号（空 = 最終CV） |
| `price` | 報酬額 |
| `cv_id` | SKYFLAGのCV固有ID（冪等性キー） |
| `spram1`, `spram2` | カスタムパラメータ（クリック時に設定可） |

#### 弊社ポストバック受信URL（SKYFLAG管理画面に登録する）

```
https://your-server.com/mdm/affiliate/postback/skyflag
  ?suid={suid}
  &install={install}
  &price={price}
  &cv_id={cv_id}
  &pt={pt}
  &mcv={mcv}
```

#### クリックURL形式

```
{skyflag_click_url}?suid={user_token}
```
※ `click_url_template` に `{user_token}` プレースホルダで設定

#### キャンペーン登録

```http
POST /mdm/admin/affiliate/campaigns
X-Admin-Key: {admin_key}
Content-Type: application/json

{
  "name": "SKYFLAG案件名",
  "category": "app",
  "destination_url": "https://example.com/lp",
  "reward_type": "cpi",
  "reward_amount": 400,
  "click_url_template": "https://click.skyflag.jp/xxxxx?suid={user_token}",
  "enable_points": false
}
```

---

### smaad

**実装状況: ✅ 完了**

#### 計測ロジック

1. クリック時に `AffiliateClickDB` へ `user_token` を記録
2. `click_url_template` の `{user_token}` を置換してリダイレクト
   ```
   https://tr.smaad.net/redirect?zo={ゾーンID}&ad={広告ID}&uid={user_token}
   ```
3. CV発生時、smaadが下記URLに GET リクエストを送信
   ```
   https://your-server.com/mdm/affiliate/postback/smaad?uid={uid}&price={price}
   ```
4. `uid`（= user_token）で照合 → CV記録 → 即ポイント付与（設定時）

#### キャンペーン登録

```http
POST /mdm/admin/affiliate/campaigns
{
  "name": "smaad案件名",
  "click_url_template": "https://tr.smaad.net/redirect?zo=745468462&ad=198337123&uid={user_token}",
  "enable_points": false
}
```

#### ポストバック受信URL（smaad管理画面に登録）

```
https://your-server.com/mdm/affiliate/postback/smaad?uid={uid}&price={price}
```

---

### A8.net

**実装状況: ✅ 完了**

#### 計測ロジック

smaadと同一スキーム。クリックURLの形式のみ異なる。

```
https://px.a8.net/a8fly/earnings?a8mat={a8matコード}&uid={user_token}
```

#### ポストバック受信URL（A8.net管理画面に登録）

```
https://your-server.com/mdm/affiliate/postback/a8?uid={uid}&price={price}
```

---

### インバウンド型 共通仕様

#### ポストバック受信エンドポイント

```
GET /mdm/affiliate/postback/{source}
```

**source**: `janet` / `skyflag` / `smaad` / `a8`

#### 内部処理フロー

```
1. パラメータ正規化
   └ ASPごとのパラメータ名を内部名（user_id/revenue/asp_action_id/attestation_status）に変換

2. user_token → device_id 逆引き（AndroidDeviceDB）
   └ 未設定の場合 → 200 {"status":"ok"} を返して終了（ASPリトライ防止）

3. Phase2チェック（JANet/SKYFLAG のみ）
   └ asp_action_id が既存レコードに一致 + approved/rejected
   → AffiliateConversionDB.attestation_status を更新
   → approved の場合はポイント付与（enable_points=true 時）
   → return

4. 冪等性チェック
   └ asp_action_id あり → asp_action_id で重複確認（JANet/SKYFLAG）
   └ asp_action_id なし → click_token + source で重複確認（smaad/A8.net）
   └ 重複あり → 200 {"status":"ok"} を返して終了

5. AffiliateClickDB を検索
   └ WHERE user_token = {user_token} AND converted = false
   └ ORDER BY clicked_at DESC LIMIT 1

6. AffiliateConversionDB を作成
   - click_token, campaign_id, source, revenue_jpy
   - asp_action_id（JANet: action_id, SKYFLAG: cv_id）
   - attestation_status（JANet/SKYFLAG: "pending", smaad/A8: null）
   - raw_payload（クエリパラメータ全体をJSON保存）

7. AffiliateClickDB.converted = true に更新

8. InstallEventDB を更新（billing_status = "billable"）

9. ポイント付与（attestation_status が null or "approved" かつ enable_points=true 時）

10. DB commit → 200 {"status":"ok"}
```

---

## アウトバウンド型

> **共通スキーム**: DPCがインストールを報告 → 弊社サーバーが計測パートナーまたはASPへ通知を送信

```
DPC（Android端末）
  │
  └─ POST /mdm/install_confirmed { device_id, package_name, ... }
            ↓ InstallEventDB 作成
            ↓ cv_trigger に従いポストバック発火
            ├─ AppsFlyer S2S（appsflyer_dev_key 設定時）
            ├─ Adjust S2S（adjust_app_token 設定時）
            └─ 直接ASPポストバック（postback_url_template 設定時）
```

---

### AppsFlyer S2S

**実装状況: ✅ 完了**

#### エンドポイント
```
POST https://s2s.appsflyer.com/api/v2/installs?devkey={appsflyer_dev_key}
```

#### 送信データ
```json
{
  "advertising_id": "{gaid}",
  "app_id": "{destination_url（パッケージ名）}",
  "af_events_api": "true",
  "eventName": "install",
  "af_customer_user_id": "{device_id}",
  "timestamp": "{install_ts}"
}
```

#### キャンペーン設定
```json
{ "appsflyer_dev_key": "AppsFlyerコンソールのdev key" }
```

---

### Adjust S2S

**実装状況: ✅ 完了**

#### エンドポイント
```
POST https://s2s.adjust.com/event
```

#### 送信データ（クエリパラメータ）
```
app_token={adjust_app_token}
event_token={adjust_event_token}
gps_adid={gaid}
s2s=1
created_at={install_ts}
partner_params[device_id]={device_id}
```

#### キャンペーン設定
```json
{
  "adjust_app_token": "Adjustコンソールのapp token",
  "adjust_event_token": "イベントトークン（任意）"
}
```

---

### postback_url_template（直接ASPポストバック）

**実装状況: ✅ 完了**

#### 対象

Felmat / ValueCommerce など、クリック計測なしでDPCインストール完了をトリガーとしてCV通知するASP。

#### テンプレート変数一覧

| 変数 | 内容 | 例 |
|------|------|-----|
| `{device_id}` | Android ID（URLエンコード済） | `a1b2c3d4e5f6a7b8` |
| `{user_token}` | 不透明ユーザーID（URLエンコード済） | `f8e2-xxxx-yyyy` |
| `{enrollment_token}` | エンロールトークン（URLエンコード済） | `abc123xyz` |
| `{dealer_id}` | 代理店ID | `uuid-xxxx` |
| `{store_id}` | 店舗ID | `uuid-yyyy` |
| `{amount}` | CV単価（円・整数） | `500` |
| `{install_ts}` | インストール時刻 | `2026-03-20 12:00:00+00:00` |
| `{package_name}` | APKパッケージ名（URLエンコード済） | `com.example.app` |
| `{event_type}` | イベント種別（URLエンコード済） | `install` または `app_open` |

#### 設定例（Felmat）
```json
{
  "postback_url_template": "https://t.felmat.net/fmcv?ak=XXXXX&ev=install&price={amount}&uid={user_token}"
}
```

---

### アウトバウンド型 共通仕様

#### cv_trigger（CV発火タイミング）

| cv_trigger | 発火タイミング | 用途 |
|-----------|--------------|------|
| `install` | DPCがインストール完了を報告した時点（Method 1） | CPI（アプリ導入数課金）|
| `app_open` | プッシュ通知タップ → アプリ起動後（Method 2） | CPE（起動確認型課金）|

#### cv_trigger 優先順位（高→低）

```
① DealerDB.default_cv_trigger   — 代理店レベルで全キャンペーンに強制適用
② AffiliateCampaignDB.cv_trigger — キャンペーンごとの設定
③ "install"（デフォルト）
```

---

## ポイント付与設計

### 基本方針

- **デフォルトはポイント付与なし**（`enable_points = false`）
- キャンペーン単位でON/OFFを設定する
- 還元率もキャンペーン単位で調整可能

### キャンペーン設定フィールド

| フィールド | 型 | デフォルト | 説明 |
|-----------|---|---------|------|
| `enable_points` | bool | `false` | ポイント付与を有効にするか |
| `point_rate` | float | `1.0` | 1円あたりのポイント数（例: 1.0 = 1円=1pt） |

### 付与タイミング

| ASP | 付与条件 |
|-----|---------|
| smaad / A8.net | ポストバック受信時に即付与（2段階通知なし） |
| JANet | `attestation_flag=0`（approved）受信後のみ付与 |
| SKYFLAG | `install=1` または最終ステップ（`pt=SKYFLAG&mcv=空`）受信後のみ付与 |

### DBスキーマ（未実装 / 将来実装）

```sql
-- affiliate_conversions に追加予定
attestation_status  TEXT    -- NULL / "pending" / "approved" / "rejected"
asp_action_id       TEXT    -- ASP固有のCV ID（冪等性キー）

-- user_points テーブル（新規作成予定）
CREATE TABLE user_points (
  id              TEXT PRIMARY KEY,
  device_id       TEXT NOT NULL,
  conversion_id   TEXT UNIQUE REFERENCES affiliate_conversions(id),
  points          INTEGER NOT NULL DEFAULT 0,
  source          TEXT,    -- "janet" / "skyflag" / "smaad" / "a8"
  awarded_at      DATETIME NOT NULL
);
```

---

## 進捗サマリー

| 機能 | 状態 | 備考 |
|------|------|------|
| JANet クリック追跡 | 🔧 仕様定義済み | パス形式リダイレクト・user_token対応は未実装 |
| JANet ポストバック受信 | 🔧 仕様定義済み | 実際のパラメータ（user_id/commission/action_id）対応は未実装 |
| SKYFLAG クリック追跡 | 📋 仕様定義済み | 実連携未実施 |
| SKYFLAG ポストバック受信 | 📋 仕様定義済み | 実連携未実施 |
| smaad クリック追跡 | ✅ 完了 | `click_url_template` で置換 |
| smaad ポストバック受信 | ✅ 完了 | `/affiliate/postback/smaad` |
| A8.net クリック追跡 | ✅ 完了 | `click_url_template` で置換 |
| A8.net ポストバック受信 | ✅ 完了 | `/affiliate/postback/a8` |
| AppsFlyer S2S | ✅ 完了 | install_confirmed 時に自動送信 |
| Adjust S2S | ✅ 完了 | install_confirmed 時に自動送信 |
| postback_url_template（汎用） | ✅ 完了 | Felmat / ValueCommerce 等に対応 |
| Method 1（install CV） | ✅ 完了 | install_confirmed で即ポストバック |
| Method 2（app_open CV） | ✅ 完了 | `/android/app_open` 受信でポストバック |
| 店舗別CVレポート | ✅ 完了 | `/admin/affiliate/report/store/{store_id}` |
| E2Eテスト（JANet） | ✅ 完了 | 14/14全通過 |

### 未対応 / 今後の実装課題

| 項目 | 概要 |
|------|------|
| **user_token 実装** | `AndroidDeviceDB` に `user_token` カラム追加・クリックURL生成を device_id → user_token に変更 |
| **ASPパラメータ正規化実装** | `_ASP_PARAM_MAP` + `_normalize_asp_params()` をrouter.pyに追加 |
| **2段階通知対応実装** | `attestation_status` / `asp_action_id` カラム追加・Phase2更新ロジック実装 |
| **ポイント付与実装** | `user_points` テーブル作成・`enable_points` キャンペーン設定・`_award_points()` 実装 |
| **JANet 実連携** | 管理画面でポストバックURL登録・実案件でのE2E確認 |
| **SKYFLAG 実連携** | 管理画面でポストバックURL登録・実案件でのE2E確認 |
| **smaad / A8.net E2E テスト** | `click_url_template` 系のテストを追加 |
| **ポストバック失敗リトライ** | `postback_status="failed"` のイベントを定期リトライするCronを追加 |
