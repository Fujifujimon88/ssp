# MDMプロファイル消失防止 開発仕様書（リスクヘッジ版）

作成日: 2026-03-20

## 背景・目的

店頭スタッフが端末にMDMプロファイルを設定してユーザーに渡した後、ユーザーが**データ移行・iCloudバックアップ復元・機種変更**を行うとMDMプロファイルが意図せず消失する問題を解決する。

**設計方針**:
- ユーザーによる**手動削除はOK**（意図的なoptoutは許容）
- Device Ownerモードは**不採用**（手動削除を許容する設計方針と矛盾、個人端末の法的リスク）
- APNs使用（Apple IDのみ必要・無料）
- SIM入れ替えはMDMに影響なし（対応不要）
- 再エンロールURLは管理者が**手動発行のみ**（自動送付しない）
- 方針: **消失検知 + 自動復旧** をメインとし、確実に消えるケースは手動復旧フローを整備

---

## OS別 対策結果サマリー

### iOS

| シナリオ | 対策後の結果 |
|---|---|
| 設定アプリから手動削除 | 消える（仕様通りOK） |
| iOS更新後プロファイル無効化 | **消えない** ✅（APNs + ProfileList監視で自動再push） |
| iCloudバックアップ復元・クイックスタート | **消える** ❌（管理者が手動で再エンロールURL発行） |
| optout（UI操作） | **正しく処理される** ✅（RemoveProfile送信） |
| SIM入れ替え | **影響なし** ✅（UDIDはハードウェア固有値） |

**前提作業**: Apple IDで https://identity.apple.com/pushcert にアクセスしAPNs証明書を取得してサーバーに設定（無料・手動作業）

### Android

| シナリオ | 対策後の結果 |
|---|---|
| DPCアンインストール（手動） | 消える（仕様通りOK） |
| **同一Googleアカウントで機種変更** | **消えない** ✅（BackupAgent + 平文tokenファイル設計） |
| FCMトークン変更 | **消えない** ✅（次回ポーリングで自動更新） |
| Smart Switch / factory reset | **消える** ❌（管理者が手動で再エンロールURL発行） |
| optout（UI操作） | **正しく処理される** ✅（DPCへコマンドキュー） |
| SIM入れ替え | **影響なし** ✅（ANDROID_IDはハードウェア固有値） |

### 共通

- 管理画面でデバイスごとにプロファイル状態（🟢present / 🔴missing / 🟡re_installing）を可視化
- デバイスごとに再エンロールURL発行ボタン（管理者手動操作のみ）

---

## リスクとヘッジ方針

### 🔴 EncryptedSharedPreferences + BackupAgent の相性問題 → 設計変更で解決

**問題**: EncryptedSharedPreferences はAndroidのKeyStore（端末固有）で暗号化されるため、別端末に復元すると復号できない。

**解決策**: enrollment_token だけを**平文の専用ファイル** `mdm_token_backup.xml` に切り出し、そのファイルのみバックアップ対象にする。EncryptedSharedPreferences 自体はバックアップ対象外にする。

```
バックアップ対象:     mdm_token_backup.xml（平文、enrollment_tokenのみ）
バックアップ対象外:   mdm_prefs_encrypted.xml（KeyStore依存で復元不可のため除外）
```

復元後の `onRestoreFinished()` で平文tokenを読み取り → EncryptedSharedPreferences に書き直す。

### 🔴 既存ユーザーの移行問題 → 起動時マイグレーションで解決

**問題**: 既存ユーザーの `mdm_prefs`（平文）から `mdm_prefs_encrypted` への自動移行がない場合、アップデート時にenrollment_tokenが消える。

**解決策**: MainActivity 起動時に一回限りのマイグレーション処理を追加する。
- 旧 `mdm_prefs` が存在し、新 `mdm_prefs_encrypted` に未移行の場合 → コピーして旧ファイル削除

### 🟡 enrollment_token 乗っ取りリスク → fingerprint チェックで難易度上昇

**問題**: enrollment_token を入手した第三者が別端末で register を叩いて乗っ取れる。

**解決策**: register 時に `device_fingerprint`（manufacturer + model + brand のハッシュ）を送信し、初回登録と大きく異なる場合は管理画面に ⚠️ バッジ表示（完全防止ではないが乗っ取りの難易度が上がる）。

### 🟡 一括再pushの負荷 → バッチ送信で解決

**解決策**: bulk-restore はキューに積んで **100件/分** のバッチ送信にする。

### 🟡 enrollment_token の永続化リスク → 論理削除 + token失効（フェーズ1）

**フェーズ1（初期実装）**: 論理削除 + token失効
- optout時に `token_revoked_at` を記録 → 再エンロールURL無効化
- デバイス情報は `status=opted_out` で保持（物理削除しない）
- 利用規約に「管理目的で一定期間保持」を明記することで個人情報保護法に対応

**フェーズ2（将来）**: A（物理削除）またはC（匿名化）に切り替え可能な設計
- `cleanup.py` のバッチを実装しておくが、初期は**無効化**しておく
- 切り替え時は `cleanup.py` をスケジューラーに登録するだけで移行完了

---

## 実装内容

### Android DPCアプリ

#### 1. `build.gradle` に依存追加
- ファイル: `android-dpc/app/build.gradle`
- `implementation 'androidx.security:security-crypto:1.1.0-alpha06'` を追加

#### 2. 起動時マイグレーション（既存ユーザー対応）
- ファイル: `android-dpc/app/src/main/java/com/platform/dpc/MainActivity.kt`
- 旧 `mdm_prefs`（平文）が存在すれば `mdm_prefs_encrypted` へ移行し、旧ファイルを削除

#### 3. `EncryptedSharedPreferences` への移行
- ファイル: `android-dpc/app/src/main/java/com/platform/dpc/MainActivity.kt`
- ファイル: `android-dpc/app/src/main/java/com/platform/dpc/CommandPoller.kt`
- `getSharedPreferences("mdm_prefs", ...)` → `EncryptedSharedPreferences("mdm_prefs_encrypted")` に変更（保存キーは変更なし）

#### 4. `MdmBackupAgent.kt` 新規作成（平文tokenファイル設計）
- 新規ファイル: `android-dpc/app/src/main/java/com/platform/dpc/MdmBackupAgent.kt`
- バックアップ: `mdm_token_backup.xml`（enrollment_tokenのみ平文）
- 復元後: 平文tokenを読み取り → EncryptedSharedPreferences に書き直し → device_id を新機種IDに更新 → `registered = false` にリセット

#### 5. `data_extraction_rules.xml` 新規作成
- 新規ファイル: `android-dpc/app/src/main/res/xml/data_extraction_rules.xml`
- `mdm_token_backup.xml` のみバックアップ対象（`mdm_prefs_encrypted` は除外）

#### 6. `AndroidManifest.xml` 更新
- ファイル: `android-dpc/app/src/main/AndroidManifest.xml`
- `android:backupAgent=".MdmBackupAgent"` 追加
- `android:dataExtractionRules="@xml/data_extraction_rules"` 追加

### サーバー側

#### 7. `/mdm/android/register` 機種変更対応 + fingerprint チェック
- ファイル: `mdm/router.py`
- enrollment_token 付きで来た場合: 旧デバイスを `migrated` に更新 → 新 device_id で引き継ぎ
- device_fingerprint が初回登録時と不一致 → `migration_suspicious = true` を記録・⚠️表示

#### 8. iOS ProfileList 監視 → 自動再push
- ファイル: `mdm/router.py` → `/mdm/ios/checkin`
- 既存の `get_profile_list()` + `push_command()` を流用
- プロファイル消失検知 → `install_configuration_profile()` 即時再push
- `iOSDeviceDB.profile_status` を `present` / `missing` / `re_installing` で管理

#### 9. 定期ヘルスチェック
- 新規ファイル: `mdm/tasks/health_check.py`
- 毎時: `last_checkin_at` 24時間以上前 → APNs再push

#### 10. 再エンロールエンドポイント
- `GET /mdm/re-enroll?token={enrollment_token}`
- token の有効期限 + `token_revoked_at` チェック
- 管理画面から管理者が手動でURLをコピーして案内（自動送付なし）

#### 11. migrate-restore エンドポイント
- `POST /mdm/device/migrate-restore`（Smart Switch等の手動復旧）

#### 12. optout 正式実装
- iOS: RemoveProfile → NanoMDMキュー + APNs送信
- Android: `remove_mdm_profile` コマンドをDPCキュー + FCMトークン無効化
- `token_revoked_at` を記録（再エンロール不可）

#### 13. クリーンアップバッチ（初期は無効化）
- 新規ファイル: `mdm/tasks/cleanup.py`
- 実装はしておくが、初期はスケジューラーに登録しない
- フェーズ2でA（物理削除）またはC（匿名化）に切り替える際に有効化するだけでOK

#### 14. bulk-restore レート制限
- `POST /mdm/admin/bulk-restore` はキューに積んで 100件/分 でバッチ送信

### DBスキーマ追加
- ファイル: `db_models.py` + `alembic/versions/add_profile_resilience.py`

```
iOSDeviceDB に追加:
  - profile_status: String(20), default="unknown"
  - last_profile_check_at: DateTime, nullable=True

DeviceDB に追加:
  - re_enroll_count: Integer, default=0
  - token_revoked_at: DateTime, nullable=True
  - token_expires_at: DateTime, nullable=True  （登録日+2年）

AndroidDeviceDB に追加:
  - previous_device_id: String(64), nullable=True
  - migrated_at: DateTime, nullable=True
  - device_fingerprint: String(64), nullable=True
  - migration_suspicious: Boolean, default=False
```

### 管理画面UI
- ファイル: `dashboard/templates/admin.html`
- プロファイル状態バッジ: 🟢present / 🔴missing / 🟡re_installing / 🟠inactive / 🔵migrated
- デバイスごとに再エンロールURL発行ボタン（URLコピーのみ）
- 個別「再push」ボタン → `POST /mdm/admin/device/{device_id}/restore-profile`
- 「一括再push」ボタン → `POST /mdm/admin/bulk-restore`（100件/分バッチ）
- `migration_suspicious = true` のデバイスに ⚠️ バッジ表示

---

## 変更ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `android-dpc/app/src/main/java/com/platform/dpc/MainActivity.kt` | 起動時マイグレーション + EncryptedSharedPreferences移行 + 再接続フロー |
| `android-dpc/app/build.gradle` | `androidx.security:security-crypto` 追加 |
| `android-dpc/app/src/main/AndroidManifest.xml` | `backupAgent`, `dataExtractionRules` 追加 |
| `android-dpc/app/src/main/java/com/platform/dpc/CommandPoller.kt` | SharedPreferences参照を `mdm_prefs_encrypted` に統一 |
| `android-dpc/app/src/main/java/com/platform/dpc/MdmBackupAgent.kt` | **新規**: 平文tokenファイル設計のBackupAgent |
| `android-dpc/app/src/main/res/xml/data_extraction_rules.xml` | **新規**: `mdm_token_backup.xml` のみバックアップ対象 |
| `mdm/router.py` | /android/register fingerprint+token引き継ぎ, re-enroll, migrate-restore, optout改善, iOS checkin強化, admin restore, bulk-restoreバッチ化 |
| `mdm/android/commands.py` | `remove_mdm_profile` コマンド追加 |
| `mdm/tasks/health_check.py` | **新規**: 定期ヘルスチェック（iOS APNs再push） |
| `mdm/tasks/cleanup.py` | **新規**: optout後90日でdevice情報物理削除バッチ |
| `db_models.py` | フィールド追加（token_revoked_at, token_expires_at, device_fingerprint, migration_suspicious 含む） |
| `alembic/versions/add_profile_resilience.py` | **新規**: マイグレーション |
| `dashboard/templates/admin.html` | プロファイル状態列・操作ボタン・⚠️バッジ |
| `main.py` | startup にヘルスチェック・クリーンアップタスク登録 |

---

## 検証方法

1. **Unit**: `tests/test_mdm_profile_resilience.py`
   - 既存ユーザー移行: `mdm_prefs`（平文）→ `mdm_prefs_encrypted` 移行確認
   - re-enroll: 有効tokenで再取得できること、失効tokenで拒否されること
   - android register + enrollment_token: 新device_idに引き継がれること
   - fingerprint不一致: `migration_suspicious = true` が記録されること
   - optout: token_revoked_at が記録され再エンロール不可になること
   - ProfileList消失 → InstallProfile再キューイング

2. **E2E**: `e2e/mdm_profile_recovery.spec.js`
   - `GET /mdm/re-enroll?token=XXX` → mobileconfig再取得
   - `POST /mdm/android/register` with token → migrate確認
   - `POST /mdm/admin/bulk-restore` → 100件/分バッチ確認

3. **Android Backup テスト**:
   - `adb shell bmgr backup com.platform.dpc` でバックアップ実行
   - 端末ワイプ後に `adb shell bmgr restore com.platform.dpc` で復元
   - enrollment_token が復元され device_id が新しいIDになっていることを確認
   - `mdm_prefs_encrypted` が復元されないことを確認（KeyStore問題の回避確認）

---

## 前提作業（実装とは別で必要な手動作業）

- Apple IDで https://identity.apple.com/pushcert にアクセスし、NanoMDMサーバーのCSRからAPNs証明書を取得
- 取得した証明書をサーバーに配置し `mdm/nanomdm/apns.py` の設定を更新
