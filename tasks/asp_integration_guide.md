# ASP連携 計測設定ガイド

本ドキュメントは、SSPプラットフォームにおけるASP（アフィリエイトサービスプロバイダー）との計測連携の設定・運用手順をまとめたものです。

---

## 概要

本プラットフォームは、トラッキングURLにマクロを埋め込む方式でASPへクリックデータを渡し、コンバージョン発生時にASPからポストバック通知を受け取ります。

- クリック時に `AffiliateClickDB` へレコードを作成し、`click_token` を発行
- ポストバック受信時に `click_token` でクリックレコードを照合し、`AffiliateConversionDB` へ記録
- 重複計上はASPアクションID + キャンペーンIDの複合ユニーク制約で防止

対応ASP: **SKYFLAG**, **JANet**, **smaad**, **A8.net**

---

## 計測フロー

```
1. ユーザーが広告をクリック
   GET /mdm/affiliate/click/{campaign_id}?token={enrollment_token}

2. システムが AffiliateClickDB にレコードを作成
   - dealer_id, click_token (UUIDv4), enrollment_token を記録

3. システムがASPのトラッキングURLへリダイレクト
   - SESSIONID マクロを click_token に置換して転送

4. ASPがインストール・購入・リードなどのコンバージョンを検知

5. ASPが本プラットフォームへポストバックを送信
   GET https://{BASE_URL}/mdm/affiliate/cv?id={click_token}

6. システムが AffiliateClickDB から click_token でクリックレコードを検索
   → AffiliateConversionDB へコンバージョンを記録

7. 必要に応じてユーザーへポイントを付与
```

**アトリビューションチェーン:**

```
AffiliateClickDB (dealer_id, click_token)
        ↓  JOIN (click_token)
AffiliateConversionDB
        ↓
収益レポート（代理店収益・ユーザーポイント）
```

---

## ASP別ポストバック設定

### SKYFLAG

- ポストバックに `install` パラメータが含まれる場合、ソース = `"skyflag"` と判定
- `suid` パラメータが含まれる場合も `"skyflag"` と判定（フォールバック）
- トラッキングURL例:

```
https://ad.skyflag.jp/ad/p/r?_cprm=...&suid=SESSIONID&_media=HIMSITE&spram1=USERID&spram2=CAMPAIGNID
```

- `suid=SESSIONID` に click_token が入り、ポストバック時に `id={click_token}` として返送される

### JANet

- ポストバックに `attestation_flag` パラメータが含まれる場合、ソース = `"janet"` と判定
- 案件設定で `janet_media_id` と `janet_original_id` を設定することでダイレクトリンク形式に対応

### smaad

- 上記パラメータがいずれも存在しない場合のデフォルト判定
- ソース = `"smaad"`

### A8.net

- デフォルト検出（smaadと同様の判定フロー）
- 案件のトラッキングURLにA8.netのテンプレートを使用

**ソース判定ロジック（優先順位）:**

| 条件 | 判定ソース |
|------|-----------|
| `install` パラメータあり | `skyflag` |
| `attestation_flag` パラメータあり | `janet` |
| `suid` パラメータあり | `skyflag` |
| 上記いずれも該当なし | `smaad` |

---

## Tracking URL マクロ仕様

トラッキングURLテンプレートに以下のマクロを記述すると、クリック時に自動で実値へ置換されます。
**フォーマット: 中括弧なし（Ruby互換）**

| マクロ名 | 置換値 | 説明 |
|----------|--------|------|
| `SESSIONID` | `click_token` | クリック識別子（UUID）。ポストバックの `id` パラメータと対応 |
| `HIMSITE` | `dealer.store_code` | 店舗コード |
| `USERID` | `user_token` | デバイス・ユーザートークン |
| `CAMPAIGNID` | `campaign_id` | キャンペーンID |
| `CLICK_URL` | URLエンコード済みのリンク先URL | 遷移先URL |
| `NWCLKID` | ネットワーククリックID | 互換用（通常は空文字） |
| `NWSITEID` | ネットワークサイトID | 互換用（通常は空文字） |

**置換例（SKYFLAG）:**

```
テンプレート:
https://ad.skyflag.jp/ad/p/r?_cprm=...&suid=SESSIONID&_media=HIMSITE&spram1=USERID&spram2=CAMPAIGNID

置換後:
https://ad.skyflag.jp/ad/p/r?_cprm=...&suid=a1b2c3d4-xxxx&_media=SHOP001&spram1=u_token_xxx&spram2=42
```

---

## ポストバックURL

ASP側に登録するポストバックURLは以下の形式で統一されています。

```
https://{BASE_URL}/mdm/affiliate/cv?id=SESSIONID
```

- `id` パラメータ = `SESSIONID` = `click_token`（クリック時に払い出したUUID）
- 全ASP共通エンドポイント。ソースの判別はポストバックに含まれるパラメータで自動判定
- ASP側のマクロ設定で `SESSIONID` 部分をクリックIDに差し替えてポストバックするよう設定すること

**各ASPでの設定例:**

| ASP | ポストバック設定値 |
|-----|-----------------|
| SKYFLAG | `https://{BASE_URL}/mdm/affiliate/cv?id={suid}` |
| JANet | `https://{BASE_URL}/mdm/affiliate/cv?id={click_id}&attestation_flag={flag}` |
| smaad | `https://{BASE_URL}/mdm/affiliate/cv?id={click_id}` |
| A8.net | `https://{BASE_URL}/mdm/affiliate/cv?id={click_id}` |

---

## 案件設定方法（管理画面）

管理画面のキャンペーン設定から以下のフィールドを入力します。

### 基本設定

| フィールド | 内容 |
|-----------|------|
| `tracking_url` | ASPが提供するトラッキングURLテンプレート。マクロ（`SESSIONID` 等）をそのまま記述 |
| `dealer_revenue_rate` | 代理店（店舗）に付与する収益の割合（%） |
| `user_point_rate` | ユーザーに付与するポイントの割合（%） |

### JANet専用設定

| フィールド | 内容 |
|-----------|------|
| `janet_media_id` | JANetのメディアID（ダイレクトリンク形式で必要） |
| `janet_original_id` | JANetのオリジナルID（ダイレクトリンク形式で必要） |

### 設定手順

1. 管理画面 > キャンペーン管理 > 対象キャンペーンを選択
2. `tracking_url` にASPから発行されたURLテンプレートを貼り付け（マクロはそのまま）
3. `dealer_revenue_rate` と `user_point_rate` を設定
4. JANetの場合は `janet_media_id` / `janet_original_id` を追加入力
5. 保存後、ASP管理画面にポストバックURLを登録

---

## 計測確認方法

### 1. クリック確認

```
GET /mdm/affiliate/click/{campaign_id}?token={enrollment_token}
```

レスポンスが302リダイレクトになっていること、および `AffiliateClickDB` にレコードが作成されていることを確認。

### 2. ポストバック疎通確認

```
GET https://{BASE_URL}/mdm/affiliate/cv?id={click_token}
```

テスト用のポストバックをcurlまたはASP管理画面のテスト機能で送信し、200レスポンスを確認。

```bash
curl "https://{BASE_URL}/mdm/affiliate/cv?id={click_token}&install=1"
```

### 3. コンバージョン記録確認

管理画面 > コンバージョン履歴、または直接 `AffiliateConversionDB` を参照し、レコードが作成されていることを確認。

### 4. 冪等性確認

同一の `asp_action_id` + `campaign_id` でポストバックを2回送信し、2件目が無視されること（重複登録されないこと）を確認。

---

## トラブルシューティング

### ポストバックが届いているが計測されない

| 確認項目 | 対処 |
|---------|------|
| `id` パラメータが空 | ASP側のポストバックURL設定でSESSIONIDマクロが正しく差し替えられているか確認 |
| `click_token` に一致するクリックレコードがない | クリック計測が先行して動作しているか確認。クリックURLのキャンペーンIDが正しいか確認 |
| 400/500エラーが返る | サーバーログを確認。`id` パラメータの形式（UUID形式）を確認 |

### マクロが置換されない

| 確認項目 | 対処 |
|---------|------|
| トラッキングURLのマクロ名に誤字がある | `SESSIONID`・`HIMSITE` 等は大文字・スペルを厳密に一致させること |
| 中括弧が入っている | 本プラットフォームのマクロは中括弧なし形式（例: `SESSIONID` ○ / `{SESSIONID}` ×） |

### ソースが正しく判定されない

| 確認項目 | 対処 |
|---------|------|
| JANetなのに `smaad` と判定される | ポストバックに `attestation_flag` パラメータが含まれているか確認 |
| SKYFLAGなのに `smaad` と判定される | ポストバックに `install` または `suid` パラメータが含まれているか確認 |

### 重複コンバージョンが記録される

- `asp_action_id` がポストバックに含まれているか確認
- 含まれていない場合、ASP側の設定でアクションIDマクロを追加するか、ASPサポートへ問い合わせ

### クリックからポストバックまでのデバッグ手順

1. クリックURLにアクセスし、レスポンスヘッダーの `Location` でマクロ置換後のURLを確認
2. リダイレクト先URLの `suid` / `click_id` 等にUUIDが入っているか確認
3. ASP管理画面でポストバック送信ログを確認
4. サーバーログで `/mdm/affiliate/cv` のアクセスログを確認
