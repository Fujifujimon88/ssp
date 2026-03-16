# Mobile Ad Platform — Development Requirements Document

**Version:** 1.0
**Date:** 2026-03-17
**Status:** Approved for execution
**Scope:** Digital Turbine Ignite + Glance (InMobi) capability parity on Japanese MDM-enrolled device fleet

---

## Executive Summary

This document defines all remaining development items required to reach feature parity with Digital Turbine Ignite (silent APK install / CPI) and Glance by InMobi (lock screen content / CPM). The platform already has a working FastAPI + PostgreSQL backend, Android DPC with LockscreenActivity, FCM push, consent UI, eCPM ranking, A/B test framework, and basic impression/click tracking.

Target outcome after P0+P1 completion: **¥280–300万/month per 10,000 enrolled devices** (vs ¥60–70万 today).

---

## Priority Legend

| Level | Definition |
|-------|------------|
| P0 | Revenue-blocking. Must ship before any monetization pitch. |
| P1 | Major revenue uplift. Required for competitive positioning. |
| P2 | Long-term differentiation. Required for scale / ML flywheel. |

---

## Section 1 — Android DPC (Kotlin)

### DPC-01 — Silent APK Install via PackageInstaller.Session API
**Priority:** P0
**Category:** Android DPC
**Revenue impact:** Unlocks CPI channel. CVR: 5% (manual) → 15–20% (silent). CPI unit price: ¥100 → ¥300–500.
**Effort:** L
**Dependencies:** Device Owner enrollment (already done), backend CPI billing trigger (BKD-03)

**Requirements:**
- Replace current `installApk()` in `CommandExecutor.kt` which opens the browser/ACTION_VIEW with a proper `PackageInstaller.Session` flow.
- The DPC runs as Device Owner and holds `INSTALL_PACKAGES` permission — no user confirmation dialog should appear.
- Implementation flow:
  1. `PackageInstaller.createSession(SessionParams(MODE_FULL_INSTALL))`
  2. Stream APK bytes from local cache (see DPC-02) into the session via `openWrite()`
  3. Commit the session; register a `BroadcastReceiver` for `PackageInstaller.ACTION_SESSION_COMMITTED`
  4. On `STATUS_SUCCESS`, call `MdmApiClient.reportInstall(packageName, campaignId)`
- Must handle: insufficient storage, corrupted APK checksum (SHA-256 pre-verified), session timeout.
- Silent install must work on Android 9–14 (API 28–34). Test on emulator and physical device.

---

### DPC-02 — Background APK Pre-download Manager
**Priority:** P0
**Category:** Android DPC
**Revenue impact:** Prerequisite for DPC-01. Enables instant install trigger on FCM command.
**Effort:** M
**Dependencies:** DPC-01

**Requirements:**
- Implement `ApkDownloadManager` using Android `DownloadManager` API or `WorkManager` with `CoroutineWorker`.
- Constraints: `NetworkType.UNMETERED` (Wi-Fi only) AND `requiresCharging(true)` — zero battery/data impact.
- Store APK to `context.getExternalFilesDir("apk_cache")`. Max cache: 50 MB total, LRU eviction.
- Compute SHA-256 of downloaded file; compare against server-provided checksum before install.
- Backend sends `apk_url`, `apk_sha256`, `package_name`, `campaign_id` in the `install_apk` command payload.
- On download complete, trigger `CommandExecutor.installApk()` silently without user interaction.
- Expose `DownloadStatus` (PENDING / DOWNLOADING / READY / INSTALLED / FAILED) to `CommandPoller` for server sync.

---

### DPC-03 — Install Confirmation Report + S2S Postback Trigger
**Priority:** P0
**Category:** Android DPC
**Revenue impact:** Deterministic attribution = zero fraud = justification for premium CPI rates.
**Effort:** S
**Dependencies:** DPC-01, BKD-03

**Requirements:**
- After `PackageInstaller.STATUS_SUCCESS`, call `POST /mdm/install_confirmed` with:
  ```json
  {
    "device_id": "...",
    "package_name": "com.example.app",
    "campaign_id": "...",
    "install_ts": 1234567890,
    "apk_sha256": "..."
  }
  ```
- Include retry logic (3 attempts, exponential backoff) to guarantee delivery.
- Backend must validate the install report before triggering CPI billing (see BKD-03).

---

### DPC-04 — Persistent Foreground Service (BOOT_COMPLETED)
**Priority:** P0
**Category:** Android DPC
**Revenue impact:** Required for all DPC features to function reliably after reboot.
**Effort:** M
**Dependencies:** None (standalone)

**Requirements:**
- Implement `MdmForegroundService` as an Android `Service` with `startForeground()`.
- Register `BOOT_COMPLETED` + `QUICKBOOT_POWERON` broadcast receivers in `AndroidManifest.xml`.
- On boot: start `CommandPoller`, register `ScreenOnReceiver`, enqueue `WorkManager` jobs.
- Persistent notification: "サービス稼働中 — お得な情報をお届けします" (non-dismissible, low priority channel).
- Handle Android 8+ background execution limits: use `startForegroundService()` from receiver.
- Handle Doze mode: register for `ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS` guidance in enrollment flow.
- Test: kill app → reboot → verify service restarts within 5 seconds.

---

### DPC-05 — Screen-On Event → Instant Render from Cache
**Priority:** P0
**Category:** Android DPC
**Revenue impact:** Ad display success rate: 70% → 98%. CTR uplift: +20–40% (Glance core insight).
**Effort:** M
**Dependencies:** DPC-06 (prefetch cache must exist), DPC-04

**Requirements:**
- Register `ScreenOnReceiver` for `Intent.ACTION_SCREEN_ON` in the foreground service.
- On screen-on: read `SharedPreferences("mdm_lockscreen")` synchronously (main thread safe — it's a memory read).
- If cache is valid (not expired, `updated_at` within 4 hours): launch `LockscreenActivity` directly without any network call.
- If cache is stale or empty: launch `LockscreenActivity` with a fallback placeholder, then fetch fresh content in background and update.
- Cache key fields: `title`, `cta_url`, `image_url`, `impression_id`, `campaign_id`, `updated_at`.
- Add `screen_on_count_today` counter in `SharedPreferences` (reset at midnight via `AlarmManager`).

---

### DPC-06 — WorkManager Content Prefetch
**Priority:** P0
**Category:** Android DPC
**Revenue impact:** Prerequisite for DPC-05. Enables 0ms latency display.
**Effort:** M
**Dependencies:** BKD-01 (prefetch API)

**Requirements:**
- Implement `PrefetchWorker : CoroutineWorker` scheduled every 30 minutes.
- Constraints: `NetworkType.CONNECTED` (any network), `requiresBatteryNotLow(true)`.
- Call `GET /mdm/prefetch/{device_id}` — receives next 3 ad slots as a JSON array.
- Write each creative to `SharedPreferences` as a JSON blob: `prefetch_slot_0`, `prefetch_slot_1`, `prefetch_slot_2`.
- For image creatives: pre-download image to `context.cacheDir/ad_images/` using `OkHttp`.
- On prefetch success: update `prefetch_refreshed_at` timestamp.
- Periodic schedule: `PeriodicWorkRequest` with `flexTimeInterval` of 15 min to avoid exact-time battery drain.

---

### DPC-07 — Lock Screen KPI Instrumentation
**Priority:** P1
**Category:** Android DPC
**Revenue impact:** Provides data to sell premium morning slots at CPM ¥1,500–3,000 (3x normal).
**Effort:** M
**Dependencies:** DPC-05, BKD-02

**Requirements:**
- In `LockscreenActivity`, instrument the following events:
  - `onResume()`: record `display_start_ts = SystemClock.elapsedRealtime()`
  - `onPause()`: compute `dwell_time_ms = now - display_start_ts`
  - CTA tap: `dismiss_type = "cta_tap"`
  - Swipe dismiss: override `onBackPressed()` / gesture detector → `dismiss_type = "swipe_dismiss"`
  - Auto-dismiss timer: `dismiss_type = "auto_dismiss"` (existing 8-second logic)
- Report to `POST /mdm/lockscreen_kpi`:
  ```json
  {
    "impression_id": "...",
    "device_id": "...",
    "dwell_time_ms": 4200,
    "dismiss_type": "cta_tap",
    "hour_of_day": 7,
    "screen_on_count_today": 1
  }
  ```
- Send report asynchronously in `onStop()` to avoid blocking UI thread.

---

### DPC-08 — Device Profile Targeting Metadata
**Priority:** P1
**Category:** Android DPC
**Revenue impact:** Enables carrier/model/region targeting — required for DSP bid request enrichment.
**Effort:** S
**Dependencies:** BKD-04 (device profile API)

**Requirements:**
- On enrollment and on each prefetch call, send device profile:
  ```json
  {
    "device_id": "...",
    "manufacturer": "Samsung",
    "model": "Galaxy A54",
    "os_version": "14",
    "carrier": "NTT DOCOMO",
    "mcc_mnc": "44010",
    "region": "JP-13",
    "screen_width": 1080,
    "screen_height": 2340,
    "ram_gb": 6,
    "storage_free_mb": 12000
  }
  ```
- Use `TelephonyManager` for carrier/MCC-MNC. Use `Build` constants for device info.
- Store locally and re-send on change (compare hash of previous payload).

---

### DPC-09 — Video Ad Pre-cache + ExoPlayer Playback
**Priority:** P1
**Category:** Android DPC
**Revenue impact:** Video CPM ¥2,000–5,000 vs banner CPM ¥500. 4–10x revenue per impression.
**Effort:** L
**Dependencies:** DPC-02 (download infrastructure), BKD-05 (VAST endpoint)

**Requirements:**
- Extend `PrefetchWorker` to also download video creative when `creative_type = "video"`.
- Parse VAST 3.0 XML to extract `MediaFile` URL. Support `video/mp4` and `application/x-mpegURL` (HLS).
- Use `ExoPlayer 2.x` (now Media3) with `SimpleCache` for pre-buffering. Max video cache: 50 MB.
- Video ad displayed as full-screen interstitial after lock screen dismiss (not during — UX constraint).
- Tracking beacons: fire VAST `<Impression>`, `<Tracking event="start">`, `<Tracking event="firstQuartile">`, `<Tracking event="midpoint">`, `<Tracking event="thirdQuartile">`, `<Tracking event="complete">`.
- Skip button appears at 5 seconds. If skipped, fire `<Tracking event="skip">`.
- Max video storage per device: 50 MB. Oldest-first eviction.

---

### DPC-10 — Home Screen Shortcut Placement (Silent, Device Owner)
**Priority:** P1
**Category:** Android DPC
**Revenue impact:** WebClip placement revenue. Complements CPI channel.
**Effort:** S
**Dependencies:** DPC-04

**Requirements:**
- Current `addWebClip()` in `CommandExecutor.kt` uses `requestPinShortcut()` which shows a user confirmation dialog.
- As Device Owner, use `DevicePolicyManager.addPersistentPreferredActivity()` or `ShortcutManager` with Device Owner privileges to place shortcuts without confirmation.
- If silent placement is not available for the target API level, fall back to current `requestPinShortcut()` with a branded dialog.
- Shortcut must survive app uninstall and reinstall (persistent pinned shortcut vs dynamic shortcut).
- Support custom icon URL: download and convert to `Icon.createWithBitmap()`.

---

## Section 2 — Backend (FastAPI + PostgreSQL + Redis)

### BKD-01 — Content Prefetch API
**Priority:** P0
**Category:** Backend
**Revenue impact:** Prerequisite for DPC-05/DPC-06. Without this, 0ms screen-on render is impossible.
**Effort:** M
**Dependencies:** Existing eCPM ranking logic, existing creative DB

**Requirements:**
- `GET /mdm/prefetch/{device_id}` — returns next 3 ranked ad creatives for the device.
- Response includes pre-signed image URLs (or CDN URLs), `impression_id` (pre-allocated UUID), TTL hint.
- Pre-allocate `impression_id` at prefetch time and store in Redis with TTL = 4 hours.
- On actual impression event, mark the pre-allocated `impression_id` as delivered.
- Include device profile in query params to enable targeting: `?carrier=44010&model=GalaxyA54&hour=7`.
- Apply existing frequency cap (3/day) at prefetch time, not at display time.
- Cache response in Redis for 5 minutes per device to prevent thundering-herd on mass wake events.

---

### BKD-02 — Lock Screen KPI Schema + Ingestion API
**Priority:** P0
**Category:** Backend
**Revenue impact:** Required data foundation for premium slot pricing.
**Effort:** M
**Dependencies:** Alembic migration (existing)

**Requirements:**
- Alembic migration: add columns to `mdm_impressions`:
  ```sql
  screen_on_count_today  SMALLINT,
  dwell_time_ms          INTEGER,
  dismiss_type           VARCHAR(20),  -- 'cta_tap' | 'swipe_dismiss' | 'auto_dismiss'
  hour_of_day            SMALLINT,
  day_of_week            SMALLINT,
  creative_type          VARCHAR(20)   -- 'banner' | 'video' | 'html5'
  ```
- `POST /mdm/lockscreen_kpi` — accepts payload from DPC-07, upserts into `mdm_impressions`.
- Validate `impression_id` exists in DB before accepting KPI report (prevent spoofing).
- Add index on `(hour_of_day, dismiss_type)` for dashboard queries.
- Admin dashboard endpoint: `GET /admin/lockscreen_analytics` — returns:
  - CTR by hour of day (0–23)
  - Average dwell time by hour
  - Dismiss type breakdown (pie chart data)
  - Screen-on count N vs CTR (first impression of day = highest CTR)

---

### BKD-03 — CPI Billing Trigger
**Priority:** P0
**Category:** Backend
**Revenue impact:** Core CPI revenue. Each confirmed install = ¥300–500 billable event.
**Effort:** M
**Dependencies:** BKD-03a S2S postback (BKD-04), campaign DB (existing)

**Requirements:**
- `POST /mdm/install_confirmed` — receives install confirmation from DPC-03.
- Validation: verify `device_id` is enrolled, `campaign_id` exists, no duplicate install for same `(device_id, package_name)`.
- On valid confirmation:
  1. Insert into `install_events` table: `(device_id, package_name, campaign_id, install_ts, apk_sha256, billing_status='pending')`
  2. Trigger S2S postback task (async Celery/BackgroundTasks): call AppsFlyer and/or Adjust (BKD-04).
  3. Update `billing_status = 'billable'` after postback success.
  4. Increment campaign `install_count`, decrement `remaining_budget`.
- Idempotency: second confirmation for same `(device_id, package_name, campaign_id)` within 24h returns 200 with `already_recorded: true`.
- Alert if install rate on a campaign exceeds statistical fraud threshold (>3σ from baseline).

---

### BKD-04 — S2S Postback: AppsFlyer + Adjust
**Priority:** P0
**Category:** Backend
**Revenue impact:** Required for advertiser trust. Without postback, advertisers cannot verify installs = no budget.
**Effort:** M
**Dependencies:** BKD-03, advertiser campaign config (new fields)

**Requirements:**
- Add to campaign config: `appsflyer_dev_key`, `adjust_app_token`, `gtm_container_id` (nullable).
- **AppsFlyer S2S:** `POST https://s2s.appsflyer.com/api/v2/installs`
  - Required params: `app_id`, `appsflyer_dev_key`, `advertising_id`, `timestamp`, `af_events_api=true`
  - Map `device_id` → `advertising_id` (stored at enrollment from DPC)
- **Adjust S2S:** `POST https://s2s.adjust.com/event`
  - Required params: `app_token`, `event_token`, `gps_adid`, `created_at`
- Retry policy: 3 attempts with exponential backoff (1s, 4s, 16s). Dead-letter queue after 3 failures.
- Log all postback attempts + HTTP response codes to `postback_log` table.
- Admin UI: postback status per install event (pending / success / failed).

---

### BKD-05 — VAST 3.0 Video Ad Endpoint
**Priority:** P1
**Category:** Backend
**Revenue impact:** Video CPM ¥2,000–5,000 (4–10x banner). Unlock new advertiser category.
**Effort:** M
**Dependencies:** Existing creative DB, BKD-01

**Requirements:**
- Add `creative_type = 'video'` to creative schema.
- Store: `video_url` (MPEG-4/HLS), `video_duration_sec`, `vast_xml` (optional override), `skip_after_sec` (default 5).
- `GET /ad/vast/{impression_id}` — returns VAST 3.0 compliant XML with:
  - `<Impression>` tracking URL
  - `<Linear>` with `<MediaFiles>`, `<Duration>`, `<SkipOffset>`
  - `<TrackingEvents>`: start, firstQuartile, midpoint, thirdQuartile, complete, skip
  - `<VideoClicks>` → `<ClickThrough>`, `<ClickTracking>`
- Video tracking ingest: `POST /ad/video_event` with `{impression_id, event, timestamp}`.
- Video completion rate and quartile data added to admin dashboard.

---

### BKD-06 — OpenRTB 2.5 Outbound DSP Connection
**Priority:** P1
**Category:** Backend
**Revenue impact:** Monetizes unsold inventory. Revenue increases automatically during peak seasons.
**Effort:** L
**Dependencies:** BKD-02 (device profile), existing `dsp/` module (has OpenRTB models)

**Requirements:**
- Extend existing `auction/openrtb.py` `Device` model: add `carrier`, `model`, `os`, `geo` (lat/lon/country/region), `mcc`, `mnc`.
- Extend `BidRequest` with `app` object (replace `site` for in-app context): `bundle`, `ver`, `publisher`.
- `BidRequestBuilder` class: constructs RTB request from `(impression_event, device_profile, floor_price)`.
- DSP integration priority order:
  1. i-mobile (`https://spd.i-mobile.co.jp/bidder/bid`) — Japan's largest affiliate DSP
  2. CyberAgent DSP (Ameba Ads) — contract-dependent endpoint
  3. Google ADX via AdMob Mediation (separate SDK path — see iOS section)
- Parallel fan-out: send to all configured DSPs simultaneously using `asyncio.gather()` with `tmax=250ms` timeout.
- Floor price enforcement: winning bid must exceed `floor_price_jpy`. Convert JPY ↔ USD using cached FX rate (update hourly).
- Take rate: deduct 15–20% from DSP clearing price before recording as platform revenue.
- Fallback: if no DSP bid exceeds floor, serve from direct-sold creative inventory.
- DSP performance report: `GET /admin/dsp_performance` — win rate, avg CPM, revenue per DSP.

---

### BKD-07 — Device Profile Store
**Priority:** P1
**Category:** Backend
**Revenue impact:** Enables device targeting for DSP bids and direct campaigns.
**Effort:** S
**Dependencies:** DPC-08

**Requirements:**
- `POST /mdm/device_profile` — upsert device metadata from DPC-08.
- New table `device_profiles(device_id PK, manufacturer, model, os_version, carrier, mcc_mnc, region, screen_width, screen_height, ram_gb, storage_free_mb, updated_at)`.
- Alembic migration required.
- Expose profile in prefetch API (BKD-01) for targeting decisions.
- Admin: filter enrolled devices by carrier/model/region for campaign targeting.

---

### BKD-08 — Premium Time-Slot Pricing Engine
**Priority:** P1
**Category:** Backend
**Revenue impact:** Morning slot (07:00–08:59) first-impression = CPM ¥1,500–3,000 vs standard ¥500.
**Effort:** S
**Dependencies:** BKD-02 (KPI data), existing eCPM ranking

**Requirements:**
- Add `time_slot_multiplier` table: `(hour_start, hour_end, day_of_week, multiplier)`.
- Seed data: `07:00–08:59` → `3.0x`, `12:00–12:59` → `1.5x`, `21:00–22:59` → `2.0x`, default → `1.0x`.
- Modify eCPM ranking: `effective_ecpm = base_ecpm * time_slot_multiplier * (1 / screen_on_count_today_factor)`.
  - `screen_on_count_today_factor`: 1st impression of day = 1.0x, 2nd = 0.9x, 3rd = 0.8x (premium degrades).
- Admin UI: editable time-slot multiplier table. Changes take effect within 5 minutes (Redis TTL).
- Advertiser-facing: display "朝プレミアム枠" as a purchasable targeting option in campaign setup.

---

### BKD-09 — View-Through Attribution (VTA)
**Priority:** P1
**Category:** Backend
**Revenue impact:** Captures conversions that happen post-lock screen without a click. Increases attributed revenue.
**Effort:** M
**Dependencies:** BKD-03, existing impression tracking

**Requirements:**
- VTA window config per campaign: 24h, 72h, or 7 days (advertiser-selectable).
- When a conversion postback arrives (from S2S or pixel), check if the `advertising_id` had a lock screen impression within the VTA window.
- If match found and no click attribution exists, attribute as view-through conversion.
- Mark conversion as `attribution_type = 'view_through'` in `install_events`.
- VTA revenue counted at 50% of CPI rate (configurable per advertiser).
- Report VTA separately from click-through in advertiser dashboard.

---

### BKD-10 — Advertiser Management Portal API
**Priority:** P1
**Category:** Backend
**Revenue impact:** Self-serve reduces sales overhead. Required for scale.
**Effort:** L
**Dependencies:** Existing campaign DB, BKD-03, BKD-04

**Requirements:**
- `POST /advertiser/campaigns` — create campaign with: name, budget, CPI/CPM rate, targeting (carrier, OS, region), creative assets, AppsFlyer/Adjust keys, VTA window.
- `GET /advertiser/campaigns/{id}/report` — returns: impressions, clicks, installs, CTR, CVR, spend, remaining budget, VTA conversions.
- `POST /advertiser/campaigns/{id}/creative` — upload banner (320x50, 300x250, 320x480), video (MP4 ≤30s), or HTML5 ZIP.
- Authentication: API key per advertiser (existing auth system extension).
- Rate limit: 100 req/min per advertiser key.

---

### BKD-11 — Agency Portal API
**Priority:** P2
**Category:** Backend
**Revenue impact:** Enables multi-tenant dealer management at scale.
**Effort:** L
**Dependencies:** Existing MDM enrollment DB

**Requirements:**
- Agency can view/manage only their enrolled devices.
- `GET /agency/devices` — enrolled device count, last-seen, OS, carrier.
- `GET /agency/revenue` — monthly revenue breakdown by campaign type (CPM, CPI, video).
- `POST /agency/broadcast` — send campaign to all agency devices (wraps existing broadcast API).
- Monthly report PDF generation (Japanese locale, ¥ denomination).

---

### BKD-12 — Revenue Auto-Settlement Engine
**Priority:** P2
**Category:** Backend
**Revenue impact:** Operational efficiency. Required before reaching 100+ advertisers.
**Effort:** M
**Dependencies:** BKD-03, BKD-09

**Requirements:**
- Monthly cron: aggregate CPI events + CPM impressions + video completions per campaign.
- Compute: gross revenue, take rate deduction, net payable to agency, net revenue to platform.
- Generate invoice records in `invoices` table.
- Send summary email to advertiser with PDF breakdown.
- Flag campaigns that exceed budget cap — pause automatically.

---

## Section 3 — iOS

### iOS-01 — iOS Native App (WidgetKit Extension)
**Priority:** P1
**Category:** iOS
**Revenue impact:** Extends platform to iOS users (~50% of Japanese smartphone market).
**Effort:** L
**Dependencies:** App Store developer account, backend widget API

**Requirements:**
- SwiftUI app with `Widget Extension` target.
- Home screen widget (all sizes): shows current points balance + today's coupon + ad banner (static image).
- Lock screen widget (iOS 16+, `accessoryRectangular`): points balance + coupon count. No video (Apple guideline).
- `TimelineProvider`: fetches from `GET /ios/widget_content/{device_id}` every 15–30 minutes (OS-controlled).
- Background App Refresh: `BGAppRefreshTaskScheduler` for more frequent updates when app is foregrounded.
- Points incentive: first widget placement = 500 points bonus. Triggered via deep link after setup confirmation.
- App Store compliance: widget content is "personalized deals and rewards", not classified as advertising SDK.

---

### iOS-02 — NanoMDM + APNs Setup
**Priority:** P1
**Category:** iOS
**Revenue impact:** Required for reliable iOS push delivery and OTA profile updates.
**Effort:** M
**Dependencies:** Apple Developer account (manual step ~2 hours), NanoMDM Go binary

**Requirements:**
- Deploy NanoMDM Go binary alongside FastAPI (Docker Compose service addition).
- Obtain APNs MDM certificate from Apple Developer Portal (`.p12` format).
- Configure APNs push channel for MDM commands (separate from FCM — iOS MDM uses APNs exclusively).
- `POST /mdm/apple/checkin` — handles MDM `Authenticate`, `TokenUpdate`, `UserAuthenticate` messages.
- Command queue: `InstallApplication`, `InstallProfile`, `RemoveProfile`.
- OTA profile update flow: push `InstallProfile` command → device fetches new `.mobileconfig` → VPN config updated remotely.
- Test: enroll test iOS device → send `InstallApplication` command → verify App Clips launch.

---

### iOS-03 — App Clips (NFC/QR Launch)
**Priority:** P2
**Category:** iOS
**Revenue impact:** Frictionless enrollment entry point at dealer locations. Increases enrollment conversion.
**Effort:** M
**Dependencies:** iOS-01, iOS-02

**Requirements:**
- App Clip experience: `appclip.platform.jp/enroll?dealer_id=XXX`.
- On launch: show coupon/points teaser → consent summary → QR/NFC enrollment flow (reuse existing MDM portal).
- App Clip → full app install CTA after enrollment.
- Register App Clip domain in `.well-known/apple-app-site-association` on backend.

---

## Section 4 — ML / Recommendation

### ML-01 — User Feature Collection Pipeline
**Priority:** P1
**Category:** ML
**Revenue impact:** Required data foundation for Two-Tower model (ML-02). Without it CTR stays at 2%.
**Effort:** M
**Dependencies:** BKD-02, BKD-07

**Requirements:**
- `user_features` table: `device_id`, `age_bracket`, `carrier`, `model`, `region`, `click_history` (JSONB), `avg_dwell_ms`, `preferred_hours` (int array), `dominant_dismiss_type`, `feature_updated_at`.
- Batch job (daily, 02:00 JST): aggregate last 30 days of `mdm_impressions` per device → compute features → upsert into `user_features`.
- Feature schema must be stable (versioned). Breaking changes require model retrain.
- Privacy: no PII stored. `device_id` is a pseudonymous UUID. Feature aggregation is irreversible by design.

---

### ML-02 — Two-Tower Recommendation Model
**Priority:** P2
**Category:** ML
**Revenue impact:** CTR 2x–5x uplift → eCPM increase → advertiser ROI improvement → budget expansion flywheel.
**Effort:** L
**Dependencies:** ML-01 (minimum 30 days of feature data), TFLite toolchain

**Requirements:**
- **User Tower:** Input: `[age_bracket_emb, carrier_emb, model_emb, hour_sin, hour_cos, click_rate_7d, avg_dwell_ms_norm]`. Output: 64-dim embedding.
- **Item Tower:** Input: `[category_emb, historical_ctr, cpm_norm, creative_type_emb, target_carrier_match]`. Output: 64-dim embedding.
- Scoring: dot product of user and item embeddings → sigmoid → predicted CTR.
- Final ranking: `final_score = predicted_ctr * ecpm * time_slot_multiplier`.
- Training: Python/TensorFlow, trained on `mdm_impressions` data. Retrain weekly.
- Deployment: convert to TFLite flatbuffer. Ship to Android device via FCM `update_model` command. Max model size: 5 MB.
- On-device inference in `PrefetchWorker`: load TFLite model → score candidates → rank → cache top-3.
- Fallback: if model file absent or corrupt, use server-side eCPM ranking.
- MLflow experiment tracking: log train/val AUC, offline CTR lift vs baseline per run.

---

### ML-03 — Behavioral Cohort Segmentation
**Priority:** P2
**Category:** ML
**Revenue impact:** Enables DSP bid enrichment and premium segment targeting (increases DSP bid prices).
**Effort:** M
**Dependencies:** ML-01

**Requirements:**
- K-Means clustering (k=8–12) on `user_features` to produce behavioral cohorts.
- Cohort labels: e.g., "Morning Commuter", "Late Night Gamer", "Weekend Shopper".
- Map `device_id` → `cohort_id` in `device_profiles`.
- Include `cohort_id` in OpenRTB `BidRequest.user.data` (IAB segments extension) — increases DSP bid prices.
- Retrain monthly. Cohort stability metric: >70% of devices stay in same cohort across retrains.

---

## Section 5 — Ad Tech

### ADT-01 — IAB OM SDK Viewability
**Priority:** P1
**Category:** Ad Tech
**Revenue impact:** Required by premium DSPs and brand advertisers. Without it, brand CPMs are unavailable.
**Effort:** M
**Dependencies:** DPC-05, Android WebView (for HTML5 creatives)

**Requirements:**
- Integrate `omsdk-android` (OM SDK v1.4+) into the DPC app.
- Initialize `OmidAdSession` when `LockscreenActivity` displays an ad.
- Report: `AdEvents.impressionOccurred()` on first pixel render, `AdEvents.loaded()`.
- For WebView creatives: inject OMID JS service script into WebView HTML.
- For native banner: use `NativeAdSession` with geometry change tracking.
- Viewability definition: 50% pixels visible for 1 continuous second (IAB standard).
- Report viewability score per impression to admin dashboard.

---

### ADT-02 — HTML5 Instant Games in WebView Sandbox
**Priority:** P2
**Category:** Ad Tech
**Revenue impact:** Engagement time 30s+ (vs 4s banner). Unlocks "playable ad" CPM category (¥3,000–8,000).
**Effort:** L
**Dependencies:** DPC-05, ADT-01

**Requirements:**
- `GameAdActivity` with `WebView` configured as a sandbox:
  - `setJavaScriptEnabled(true)`, `setAllowFileAccess(false)`, `setAllowContentAccess(false)`.
  - Block navigation to external URLs (override `shouldOverrideUrlLoading`).
  - Content-Security-Policy header enforced server-side on game HTML5 bundle.
- Game bundle: ZIP (HTML + JS + assets), max 2 MB, served from CDN.
- Launch: after lock screen dismiss (not during — avoids frustration).
- JS Bridge: `Android.onGameComplete(score)` → fires conversion event.
- Playable ad tracking: `game_start`, `game_complete`, `game_converted` events.
- Admin: upload HTML5 game ZIP as creative type. Preview in iframe.

---

### ADT-03 — OpenRTB Inbound (SSP Sells to External Demand)
**Priority:** P2
**Category:** Ad Tech
**Revenue impact:** Platform becomes a publisher node in the programmatic ecosystem. Revenue from external DSPs.
**Effort:** L
**Dependencies:** BKD-06, ADT-01

**Requirements:**
- Expose `POST /openrtb/bid` as a standard OpenRTB 2.5 endpoint for external DSPs to connect to.
- This is the inverse of BKD-06 (outbound). Here the platform acts as an SSP/exchange.
- Authenticate DSPs via API key in `x-openrtb-apikey` header.
- Handle incoming `BidRequest` → run second-price auction against direct-sold floor → return `BidResponse`.
- Win notice: `GET /openrtb/win/{auction_id}?price={clearing_price}`.
- Register on SSP aggregators (e.g., Prebid Server Japan) to attract DSP demand automatically.

---

## Summary Table

| ID | Item | Category | Priority | Revenue Impact | Effort | Dependencies |
|----|------|----------|----------|----------------|--------|--------------|
| DPC-01 | Silent APK Install (PackageInstaller) | Android DPC | **P0** | CPI CVR 5%→20% | L | DPC-02, BKD-03 |
| DPC-02 | Background APK Pre-download | Android DPC | **P0** | Prerequisite DPC-01 | M | — |
| DPC-03 | Install Confirmation Report | Android DPC | **P0** | Deterministic attribution | S | DPC-01, BKD-03 |
| DPC-04 | Persistent Foreground Service | Android DPC | **P0** | Platform stability | M | — |
| DPC-05 | Screen-On → Cache Render | Android DPC | **P0** | Display rate 70%→98% | M | DPC-06, DPC-04 |
| DPC-06 | WorkManager Prefetch | Android DPC | **P0** | 0ms latency render | M | BKD-01 |
| DPC-07 | Lock Screen KPI Instrumentation | Android DPC | P1 | Premium slot pricing | M | DPC-05, BKD-02 |
| DPC-08 | Device Profile Metadata | Android DPC | P1 | DSP bid enrichment | S | BKD-04 |
| DPC-09 | Video Pre-cache + ExoPlayer | Android DPC | P1 | Video CPM ¥2k–5k | L | DPC-02, BKD-05 |
| DPC-10 | Silent Home Screen Shortcut | Android DPC | P1 | WebClip revenue | S | DPC-04 |
| BKD-01 | Content Prefetch API | Backend | **P0** | Prerequisite DPC-05/06 | M | — |
| BKD-02 | Lock Screen KPI Schema + API | Backend | **P0** | Premium slot data | M | — |
| BKD-03 | CPI Billing Trigger | Backend | **P0** | ¥300–500/install | M | — |
| BKD-04 | S2S Postback AppsFlyer/Adjust | Backend | **P0** | Advertiser trust | M | BKD-03 |
| BKD-05 | VAST 3.0 Video Endpoint | Backend | P1 | Video CPM unlock | M | — |
| BKD-06 | OpenRTB 2.5 Outbound DSP | Backend | P1 | Unsold inventory fill | L | BKD-02, BKD-07 |
| BKD-07 | Device Profile Store | Backend | P1 | Targeting precision | S | DPC-08 |
| BKD-08 | Premium Time-Slot Pricing | Backend | P1 | Morning CPM 3x | S | BKD-02 |
| BKD-09 | View-Through Attribution | Backend | P1 | Incremental CPI rev | M | BKD-03 |
| BKD-10 | Advertiser Portal API | Backend | P1 | Self-serve scale | L | BKD-03/04 |
| BKD-11 | Agency Portal API | Backend | P2 | Multi-tenant ops | L | — |
| BKD-12 | Revenue Auto-Settlement | Backend | P2 | Operational scale | M | BKD-03/09 |
| iOS-01 | iOS WidgetKit App | iOS | P1 | iOS market coverage | L | — |
| iOS-02 | NanoMDM + APNs | iOS | P1 | iOS push reliability | M | Apple Dev account |
| iOS-03 | App Clips | iOS | P2 | Enrollment conversion | M | iOS-01/02 |
| ML-01 | User Feature Collection | ML | P1 | Data foundation | M | BKD-02/07 |
| ML-02 | Two-Tower Recommendation | ML | P2 | CTR 2–5x uplift | L | ML-01 |
| ML-03 | Behavioral Cohort Segmentation | ML | P2 | DSP bid uplift | M | ML-01 |
| ADT-01 | IAB OM SDK Viewability | Ad Tech | P1 | Brand DSP access | M | DPC-05 |
| ADT-02 | HTML5 Instant Games | Ad Tech | P2 | Playable CPM ¥3k–8k | L | DPC-05, ADT-01 |
| ADT-03 | OpenRTB Inbound (SSP node) | Ad Tech | P2 | Exchange revenue | L | BKD-06, ADT-01 |

---

## Execution Phases

### Phase 3A — Core CPI + Display Reliability (Weeks 1–4)
**Goal:** Ship silent install + prefetch. This is the platform's primary competitive moat.

| Sprint | Items | Exit Criteria |
|--------|-------|---------------|
| Week 1–2 | DPC-04, DPC-02, BKD-01, BKD-03 | Foreground service survives reboot. APK downloads on Wi-Fi/charging. Prefetch API returns 3 creatives. |
| Week 3–4 | DPC-01, DPC-03, BKD-04, DPC-05, DPC-06 | Silent APK installs without dialog. Install confirmed via postback. Lock screen renders from cache in <50ms. |

**Phase 3A revenue unlock:** CPI channel at ¥300–500/install + display rate 98%.

---

### Phase 3B — Premium Inventory + Video (Weeks 5–8)
**Goal:** Data-driven slot pricing and video CPM.

| Sprint | Items | Exit Criteria |
|--------|-------|---------------|
| Week 5–6 | DPC-07, BKD-02, BKD-07, BKD-08, DPC-08 | Lock screen KPI data flowing. Morning slot priced at 3x multiplier. |
| Week 7–8 | DPC-09, BKD-05, ADT-01, BKD-06 | Video ad pre-cached and plays post-unlock. First DSP (i-mobile) connected via OpenRTB. |

**Phase 3B revenue unlock:** Video CPM + premium morning slots + first programmatic fill.

---

### Phase 3C — iOS + Attribution Depth (Weeks 9–12)
**Goal:** Expand to iOS market and improve attribution accuracy.

| Sprint | Items | Exit Criteria |
|--------|-------|---------------|
| Week 9–10 | iOS-01, iOS-02, BKD-09 | WidgetKit app live on TestFlight. APNs MDM working. VTA attributing conversions. |
| Week 11–12 | BKD-10, ML-01, DPC-10 | Advertiser self-serve portal live. User feature pipeline running nightly. |

---

### Phase 4 — ML + Scale (Weeks 13–20)
**Goal:** Personalization flywheel and operational scale.

Items: ML-02, ML-03, ADT-02, ADT-03, BKD-11, BKD-12, iOS-03.

---

## Constraints and Known Blockers

| Constraint | Impact | Mitigation |
|------------|--------|------------|
| OpenRTB DSP contracts (i-mobile, CyberAgent) | BKD-06 blocked until approved | Start application process in Week 1 (2–4 week review) |
| Apple Developer account + APNs certificate | iOS-02 blocked | Obtain in parallel with Phase 3A (manual, ~2 hours) |
| SYSTEM_ALERT_WINDOW (Android 13+) | Lock screen overlay may require user permission dialog | Use `TYPE_APPLICATION_OVERLAY` with DPC Device Owner workaround |
| iOS WidgetKit background refresh rate | OS-controlled (few times/day max) | Use APNs background push to trigger refresh on demand |
| iOS lock screen widget — no video | Apple guideline violation | Lock screen widget = static image only. Video only in home screen widget or full app. |
| TFLite model size on Android | Max 5 MB to keep APK delta small | Use model quantization (int8) during TFLite conversion |
| APPI compliance for ML feature collection | Cannot use PII in model training | Feature pipeline uses only pseudonymous `device_id` aggregates — no name, phone, email |

---

## Definition of Done

A development item is complete when:
1. Unit tests pass (`npm test` / `pytest`).
2. Manual verification on physical device (Android DPC items) or emulator.
3. Admin dashboard reflects the new data/feature.
4. No regression in existing impression/click tracking metrics.
5. For billing items (BKD-03, BKD-04): end-to-end test with a real install event in staging, postback logged in `postback_log` table.
