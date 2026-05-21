# dsp_engine 計測連携（MMP / 購入CV）設定・検証手順

dsp_engine の ROAS 計測を実広告主で成立させるための設定手順と検証チェックリスト。
購入CV（売上）を当社へ届ける経路を 2 パターン用意している。

---

## 1. 計測ループの全体像

```
入札 → 落札 → 広告表示
  広告内リンク = https://<当社>/dsp-engine/click?ct=<click_token>
        ↓ ユーザーがクリック
  /dsp-engine/click … クリックを記録（CTR）→ 広告主LPへ302
        ↓ リダイレクト先 = <広告主LP>?dsp_ct=<click_token>
  広告主のLP/アプリが dsp_ct を受け取り保持
        ↓ ユーザーが購入
  購入イベント → /dsp-engine/conversion へポストバック（dsp_ct と売上額を含む）
        ↓
  当社が click_token から campaign を特定 → 売上を記録 → ROAS 集計
```

要は **`dsp_ct`（click_token）を、クリック → LP → 購入ポストバック まで運び続ける**ことが計測成立の条件。

---

## 2. パターンA: 広告主サーバーからの直接ポストバック（推奨・最小構成）

MMP を介さず、広告主自身のサーバーから購入時に当社へ通知する。最も確実で、最初の 1〜2 社はこれを推奨。

### 広告主側の作業
1. LP 着地時、URL の `dsp_ct` パラメータを取得し、セッション/ユーザーに紐付けて保存する。
2. 購入完了時、サーバーから以下へ HTTP リクエスト（GET でも POST でも可）:

```
GET https://<当社ドメイン>/dsp-engine/conversion
      ?dsp_ct=<保存したclick_token>
      &revenue_jpy=<購入金額・円>
      &dedup_key=<注文ID等の一意な値>
      &event_type=purchase
```

- `dedup_key` に注文ID等の一意値を入れると、再送されても二重計上されない。
- `campaign_id` は `dsp_ct` から自動解決されるため不要。

---

## 3. パターンB: AppsFlyer / Adjust 経由

広告主が MMP で購入を計測しており、そのイベントを当社へ転送する場合。

### 3-1. AppsFlyer
1. AppsFlyer 管理画面で購入イベント（例 `af_purchase`）のポストバック送信先を追加。
2. ポストバック URL を以下の形にする（`{ }` は AppsFlyer のマクロに置換）:

```
https://<当社ドメイン>/dsp-engine/conversion
   ?dsp_ct={click id を保持したパラメータ}
   &event_revenue={revenue}
   &event_revenue_currency={currency}
   &event_name={event name}
   &event_id={event 一意ID}
```

3. クリック → 購入の紐付けは、当社のクリック URL（`/dsp-engine/click`）が LP へ渡す `dsp_ct` を、広告主アプリ/AppsFlyer SDK 側で計測パラメータとして保持する必要がある。

### 3-2. Adjust
Adjust のパートナーコールバック設定で、購入イベントのコールバック URL を以下に:

```
https://<当社ドメイン>/dsp-engine/conversion
   ?dsp_ct={clickid}&revenue={revenue}&currency={currency}
   &event={event}&transaction_id={event 一意ID}
```

---

## 4. 受信パラメータの仕様

`/dsp-engine/conversion` は GET / POST 両対応。各 MMP のパラメータ名を自動正規化する
（`dsp_engine/attribution.py` の `normalize_conversion_payload`）。

| 用途 | 当社標準名 | 受け付ける別名 |
|---|---|---|
| クリックトークン | `dsp_ct` | `click_token` / `click_id` / `clickid` / `af_click_id` |
| 売上額 | `revenue_jpy` | `revenue` / `event_revenue` / `af_revenue` |
| 通貨（USDならJPY換算） | `revenue_currency` | `currency` / `event_revenue_currency` |
| 冪等キー（重複排除） | `dedup_key` | `event_id` / `transaction_id` / `appsflyer_event_id` |
| イベント種別 | `event_type` | `event_name` / `af_event_name` / `event` |
| キャンペーンID（任意） | `campaign_id` | `cid` |

- 通貨が `USD` の場合は `dsp_engine/currency.py` のレートで円換算する。
- AppsFlyer/Adjust 固有キーの有無で `source`（s2s_appsflyer / s2s_adjust / direct）を自動判定する。
- `asp_postback_secret` を設定した場合は `secret` パラメータ一致を要求する。

---

## 5. 往復検証チェックリスト

ローカル（uvicorn）または本番で、以下を順に確認する。`<base>` は対象環境のURL。

1. **クリック計測**: 広告マークアップのリンク（`<base>/dsp-engine/click?ct=<token>`）を開く
   → 302 で広告主LPへ遷移し、URL に `?dsp_ct=<token>` が付くこと。
   → レポート画面でクリック数 / CTR が増えること。

2. **購入CV受信（パターンA形式）**:
   ```
   <base>/dsp-engine/conversion?dsp_ct=<token>&revenue_jpy=5000&dedup_key=test-1
   ```
   → レスポンスが `{"status":"ok","created":true}` であること。

3. **冪等性**: 同じ `dedup_key=test-1` でもう一度叩く
   → `{"created":false}` が返り、二重計上されないこと。

4. **AppsFlyer形式**:
   ```
   <base>/dsp-engine/conversion?click_id=<token>&event_revenue=8000&event_name=af_purchase&event_id=af-1
   ```
   → `created:true`、レポートの source が `s2s_appsflyer` になること。

5. **集計反映**: 広告主ダッシュボード（`/dsp-engine/advertiser/dashboard`）と
   レポート（`/dsp-engine/admin/report`）で、売上・ROAS・CTR が更新されること。

自動検証は `tests/_smoke_dsp_engine.py`（クリック→CV→集計のE2E）と
`tests/test_dsp_engine.py`（`normalize_conversion_payload` のMMP形式テスト）でカバー。

---

## 6. 既知の制約（今後の課題）

- アトリビューションはラストクリックのみ。計測ウィンドウ・ビュースルー・複数MMP重複排除は未実装。
- iOS の SKAdNetwork / ATT 非対応。iOS 実トラフィックの計測は別途対応が必要。
- 実 AppsFlyer / Adjust アカウントとの本番往復は、広告主アカウントでの実機検証が必要
  （本手順書はその検証手順を提供するもので、実アカウント検証自体は未実施）。
