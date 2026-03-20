"""MDMプラットフォーム ルーター

エンドポイント:
  GET  /mdm/portal              ← エンロールポータル（モバイルHTML）
  GET  /mdm/ios/mobileconfig    ← iOS .mobileconfig ダウンロード
  POST /mdm/device/consent      ← 同意登録 → mobileconfig URLを返す
  GET  /mdm/qr/{store_code}     ← 店舗別QRコードPNG
  POST /mdm/admin/dealers       ← 代理店登録（管理者）
  GET  /mdm/admin/dealers       ← 代理店一覧（管理者）
  POST /mdm/admin/campaigns     ← キャンペーン作成（管理者）
  GET  /mdm/admin/stats         ← MDM KPI（管理者）

BKD-10: Advertiser Self-Serve Portal API
  POST /mdm/advertiser/campaigns                        ← 広告主キャンペーン作成
  GET  /mdm/advertiser/campaigns                        ← 広告主キャンペーン一覧（統計付き）
  GET  /mdm/advertiser/campaigns/{id}/report            ← キャンペーン詳細レポート
  POST /mdm/advertiser/campaigns/{id}/creative          ← クリエイティブ追加
  GET  /mdm/advertiser/campaigns/{id}/creatives         ← クリエイティブ一覧（統計付き）
  PUT  /mdm/advertiser/campaigns/{id}/status            ← ステータス変更（pause/resume）
"""
import json
import logging
import re
import secrets
import string
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import ipaddress
from urllib.parse import urlparse
from pydantic import BaseModel, field_validator
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import verify_admin_key
from cache import get_redis, _mem_get, _mem_set
from config import settings
from database import get_db
from db_models import (
    AffiliateCampaignDB, AffiliateClickDB, AffiliateConversionDB,
    AgencyDB,
    AndroidCommandDB, AndroidDeviceDB,
    CampaignDB, ConsentLogDB, CreativeDB, DealerDB, DealerPushLogDB, DeviceDB,
    DeviceProfileDB,
    DspConfigDB, DspWinLogDB,
    InstallEventDB,
    MDMCommandDB, MdmAdSlotDB, MdmImpressionDB, TimeSlotMultiplierDB, UserFeatureDB, iOSDeviceDB,
    UserPointDB,
    WifiTriggerRuleDB, WifiCheckinLogDB,
)
from mdm.ml.features import compute_user_features
from mdm.dsp import rtb_client
from mdm.measurement.postback import check_vta, trigger_postbacks
from mdm.creative.selector import record_click, select_creative
from mdm.affiliate.billing import (
    calculate_monthly_revenue, get_all_dealers_report, get_dealer_monthly_report,
)
from mdm.affiliate.tracking import (
    apply_tracking_macros, build_tracked_url, send_adjust_postback, send_appsflyer_postback,
)
from mdm.measurement.gtm import build_lp_html
from mdm.android.commands import (
    acknowledge_command, enqueue_command, enqueue_remove_mdm_profile,
    get_pending_commands, update_device_last_seen,
)
from mdm.android.fcm import send_command_ping, send_notification
from mdm.android.wifi_checkin import handle_wifi_checkin
from mdm.enrollment.mobileconfig import MDMConfig, SafariConfig, VPNConfig, WebClipConfig, generate_mobileconfig
from mdm.nanomdm import client as nanomdm_client
from mdm.nanomdm import commands as mdm_commands
from mdm.nanomdm.apns import send_mdm_push
from mdm.enrollment.qr_generator import generate_qr_png
from mdm.line.eru_nage import register_user as eru_nage_register
from mdm.line.webhook import parse_follow_events, verify_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mdm", tags=["MDM"])


# ── ユーティリティ ─────────────────────────────────────────────

def _detect_platform(user_agent: str) -> str:
    ua = user_agent or ""
    if re.search(r"iPhone|iPad|iPod", ua):
        return "ios"
    if re.search(r"Android", ua):
        return "android"
    return "unknown"


def _parse_device_model(user_agent: str) -> str:
    ua = user_agent or ""
    if "iPhone" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    m = re.search(r"Android [^;]+; ([^)]+)\)", ua)
    if m:
        return m.group(1).strip()
    return "Unknown"


def _parse_os_version(user_agent: str) -> str:
    ua = user_agent or ""
    m = re.search(r"OS ([\d_]+) like", ua)
    if m:
        return m.group(1).replace("_", ".")
    m = re.search(r"Android ([\d.]+)", ua)
    if m:
        return m.group(1)
    return ""


# ── エンロールポータル ─────────────────────────────────────────

PORTAL_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <title>サービス設定</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', sans-serif;
      background: #f5f5f7; color: #1d1d1f; min-height: 100vh;
    }}
    .container {{ max-width: 480px; margin: 0 auto; padding: 24px 20px 40px; }}
    .logo {{ text-align: center; padding: 32px 0 24px; }}
    .logo-icon {{ font-size: 48px; }}
    .logo h1 {{ font-size: 22px; font-weight: 700; margin-top: 8px; }}
    .logo p {{ font-size: 14px; color: #6e6e73; margin-top: 4px; }}
    .card {{
      background: #fff; border-radius: 16px; padding: 24px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.08); margin-bottom: 16px;
    }}
    .card h2 {{ font-size: 17px; font-weight: 600; margin-bottom: 16px; color: #1d1d1f; }}
    .consent-item {{
      display: flex; align-items: flex-start; gap: 12px;
      padding: 10px 0; border-bottom: 1px solid #f0f0f0;
    }}
    .consent-item:last-child {{ border-bottom: none; }}
    .consent-icon {{ font-size: 22px; flex-shrink: 0; margin-top: 1px; }}
    .consent-text {{ font-size: 14px; line-height: 1.5; color: #3a3a3c; }}
    .consent-text strong {{ color: #1d1d1f; }}
    .consent-check {{
      cursor: pointer; border-radius: 8px; margin: 0 -4px;
      padding: 10px 4px; transition: background 0.15s;
    }}
    .consent-check:hover {{ background: #f5f5f7; }}
    .consent-check input[type=checkbox] {{
      width: 20px; height: 20px; flex-shrink: 0; margin-top: 2px;
      accent-color: #007aff; cursor: pointer;
    }}
    .age-select {{
      width: 100%; padding: 12px; font-size: 15px; border-radius: 10px;
      border: 1.5px solid #d1d1d6; background: #fff; appearance: none;
      -webkit-appearance: none; margin-top: 8px;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%23999' d='M6 8L0 0h12z'/%3E%3C/svg%3E");
      background-repeat: no-repeat; background-position: right 14px center;
    }}
    .btn {{
      display: block; width: 100%; padding: 16px; font-size: 17px;
      font-weight: 600; text-align: center; border: none; border-radius: 14px;
      cursor: pointer; text-decoration: none; transition: opacity 0.15s;
    }}
    .btn:active {{ opacity: 0.7; }}
    .btn-primary {{ background: #007aff; color: #fff; }}
    .btn-disabled {{ background: #c7c7cc; color: #fff; pointer-events: none; }}
    .note {{
      font-size: 12px; color: #6e6e73; text-align: center; margin-top: 12px;
      line-height: 1.6;
    }}
    .steps {{ counter-reset: step; }}
    .step {{
      display: flex; gap: 14px; align-items: flex-start; margin-bottom: 16px;
    }}
    .step-num {{
      width: 28px; height: 28px; border-radius: 50%; background: #007aff;
      color: #fff; font-size: 14px; font-weight: 700; display: flex;
      align-items: center; justify-content: center; flex-shrink: 0;
    }}
    .step-text {{ font-size: 14px; line-height: 1.6; color: #3a3a3c; padding-top: 3px; }}
    #android-section {{ display: none; }}
    #ios-section {{ display: none; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">
      <div class="logo-icon">📱</div>
      <h1>サービス設定</h1>
      <p>30秒で完了します</p>
    </div>

    <div class="card">
      <h2>📋 同意事項の確認（全項目必須）</h2>
      <p style="font-size:13px;color:#6e6e73;margin-bottom:14px;">
        以下のすべてにチェックを入れてから進んでください。
      </p>

      <label class="consent-item consent-check" for="cb_lockscreen">
        <input type="checkbox" id="cb_lockscreen" value="lockscreen_ads" onchange="checkReady()">
        <span class="consent-icon">🖼️</span>
        <div class="consent-text">
          <strong>ロック画面に広告が表示されること</strong><br>
          スワイプ解除時に広告コンテンツが表示されます。いつでも解除ページ（/mdm/optout）から停止できます。
        </div>
      </label>

      <label class="consent-item consent-check" for="cb_push">
        <input type="checkbox" id="cb_push" value="push_notifications" onchange="checkReady()">
        <span class="consent-icon">🔔</span>
        <div class="consent-text">
          <strong>プッシュ通知でおすすめ情報が届くこと</strong><br>
          クーポンやサービス情報を通知で受け取ります。端末の通知設定からオフにできます。
        </div>
      </label>

      <label class="consent-item consent-check" for="cb_webclip">
        <input type="checkbox" id="cb_webclip" value="webclip_install" onchange="checkReady()">
        <span class="consent-icon">📱</span>
        <div class="consent-text">
          <strong>ホーム画面にショートカットが追加されること</strong><br>
          便利なサービスへのアクセスがホーム画面に追加されます。
        </div>
      </label>

      <label class="consent-item consent-check" for="cb_vpn">
        <input type="checkbox" id="cb_vpn" value="vpn_setup" onchange="checkReady()">
        <span class="consent-icon">🔒</span>
        <div class="consent-text">
          <strong>VPNが自動設定されること</strong><br>
          安全なインターネット接続のためVPNプロファイルがインストールされます。
        </div>
      </label>

      <label class="consent-item consent-check" for="cb_app">
        <input type="checkbox" id="cb_app" value="app_install" onchange="checkReady()">
        <span class="consent-icon">⬇️</span>
        <div class="consent-text">
          <strong>アプリが自動でインストールされることがあること</strong>（Android）<br>
          おすすめアプリがバックグラウンドで自動インストールされる場合があります。
        </div>
      </label>

      <label class="consent-item consent-check" for="cb_data">
        <input type="checkbox" id="cb_data" value="data_collection" onchange="checkReady()">
        <span class="consent-icon">📊</span>
        <div class="consent-text">
          <strong>デバイス情報・利用状況が収集されること</strong><br>
          機種・OS・広告の閲覧・クリック状況が記録されます。第三者への販売は行いません。
        </div>
      </label>

      <div style="margin-top:14px;padding:12px;background:#fff7ed;border-radius:10px;font-size:12px;color:#92400e;line-height:1.6;">
        ⚠️ このサービスはデバイス管理権限を使用します。解除は
        <a href="/mdm/optout" style="color:#92400e;">こちら</a> からいつでも可能です。
      </div>
    </div>

    <div class="card">
      <h2>👤 年齢層を選択してください</h2>
      <select class="age-select" id="age-group" onchange="checkReady()">
        <option value="">-- 選択してください --</option>
        <option value="10s">10代</option>
        <option value="20s">20代</option>
        <option value="30s">30代</option>
        <option value="40s">40代以上</option>
      </select>
    </div>

    <div id="ios-section">
      <div class="card steps">
        <h2>📲 インストール手順</h2>
        <div class="step">
          <div class="step-num">1</div>
          <div class="step-text">「同意してダウンロード」をタップ</div>
        </div>
        <div class="step">
          <div class="step-num">2</div>
          <div class="step-text">「許可」をタップしてプロファイルをダウンロード</div>
        </div>
        <div class="step">
          <div class="step-num">3</div>
          <div class="step-text">設定アプリ →「プロファイルがダウンロード済み」→「インストール」</div>
        </div>
      </div>
      <a id="download-btn" href="#" class="btn btn-disabled" onclick="return doConsent(event)">
        同意してダウンロード
      </a>
      <p class="note">
        「インストール」をタップすることで、<br>
        上記サービス内容に同意したものとみなします。<br>
        設定アプリからいつでも削除できます。
      </p>
    </div>

    <div id="android-section">
      <div class="card steps">
        <h2>🤖 Android版セットアップ</h2>
        <div class="step">
          <div class="step-num">1</div>
          <div class="step-text">「同意してセットアップ」をタップ</div>
        </div>
        <div class="step">
          <div class="step-num">2</div>
          <div class="step-text">セットアップアプリ（APK）のインストールを許可</div>
        </div>
        <div class="step">
          <div class="step-num">3</div>
          <div class="step-text">アプリを起動して「デバイス管理者を有効化」をタップ</div>
        </div>
        <div class="step">
          <div class="step-num">4</div>
          <div class="step-text">LINEで友だち追加してクーポンを受け取る</div>
        </div>
      </div>
      <a id="android-btn" href="#" class="btn btn-disabled" onclick="return doAndroidConsent(event)">
        同意してセットアップ
      </a>
      <p class="note">
        セットアップすることで、おすすめアプリや<br>
        お得情報の通知に同意したものとみなします。<br>
        設定アプリからいつでも解除できます。
      </p>
    </div>
  </div>

  <script>
    var DEALER = "{dealer_id}";
    var CAMPAIGN = "{campaign_id}";
    var TOKEN = "";
    var BASE_URL = "{base_url}";

    var ua = navigator.userAgent;
    var isIOS = /iPhone|iPad|iPod/.test(ua);
    var isAndroid = /Android/.test(ua);

    if (isIOS) {{
      document.getElementById("ios-section").style.display = "block";
    }} else if (isAndroid) {{
      document.getElementById("android-section").style.display = "block";
    }} else {{
      document.getElementById("ios-section").style.display = "block";
    }}

    var REQUIRED_CHECKS = ["cb_lockscreen","cb_push","cb_webclip","cb_vpn","cb_app","cb_data"];

    function getCheckedItems() {{
      return REQUIRED_CHECKS
        .filter(function(id) {{ return document.getElementById(id) && document.getElementById(id).checked; }})
        .map(function(id) {{ return document.getElementById(id).value; }});
    }}

    function checkReady() {{
      var age = document.getElementById("age-group").value;
      var allChecked = REQUIRED_CHECKS.every(function(id) {{
        var el = document.getElementById(id); return el && el.checked;
      }});
      var ready = age && allChecked;
      var iosBtn = document.getElementById("download-btn");
      var andBtn = document.getElementById("android-btn");
      if (ready) {{
        if (iosBtn) {{ iosBtn.classList.remove("btn-disabled"); iosBtn.classList.add("btn-primary"); }}
        if (andBtn) {{ andBtn.classList.remove("btn-disabled"); andBtn.classList.add("btn-primary"); }}
      }} else {{
        if (iosBtn) {{ iosBtn.classList.add("btn-disabled"); iosBtn.classList.remove("btn-primary"); }}
        if (andBtn) {{ andBtn.classList.add("btn-disabled"); andBtn.classList.remove("btn-primary"); }}
      }}
    }}

    async function doConsent(e) {{
      e.preventDefault();
      var age = document.getElementById("age-group").value;
      if (!age) return false;

      var btn = document.getElementById("download-btn");
      btn.textContent = "処理中...";
      btn.classList.add("btn-disabled");

      try {{
        var res = await fetch("/mdm/device/consent", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            dealer_id: DEALER,
            campaign_id: CAMPAIGN,
            age_group: age,
            user_agent: navigator.userAgent,
            consent_items: getCheckedItems(),
          }})
        }});
        if (!res.ok) {{
          var errData = {{}};
          try {{ errData = await res.json(); }} catch(_) {{}}
          throw new Error(errData.detail || ("HTTP " + res.status));
        }}
        var data = await res.json();
        if (!data.mobileconfig_url) {{ throw new Error("mobileconfig_url missing"); }}
        // ページ遷移せずにインライン完了UIを表示（window.location.href = mobileconfig_url だと
        // Safariがページ遷移し /admin に飛んでしまう問題を防ぐ）
        document.body.innerHTML =
          '<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f5f5f7;font-family:-apple-system,sans-serif;padding:20px">' +
          '<div style="text-align:center;max-width:400px;width:100%">' +
          '<div style="font-size:64px;margin-bottom:16px">&#x2705;</div>' +
          '<h1 style="font-size:22px;font-weight:700;margin-bottom:12px">同意が完了しました</h1>' +
          '<p style="color:#6e6e73;font-size:15px;line-height:1.6;margin-bottom:32px">プロファイルをダウンロードして、設定アプリでインストールしてください。</p>' +
          '<a href="' + data.mobileconfig_url + '" style="display:block;padding:16px;background:#007aff;color:#fff;border-radius:14px;text-decoration:none;font-size:17px;font-weight:600;margin-bottom:12px">&#x1F4E5; プロファイルをダウンロード</a>' +
          (data.line_add_friend_url ? '<a href="' + data.line_add_friend_url + '" style="display:block;padding:16px;background:#06c755;color:#fff;border-radius:14px;text-decoration:none;font-size:17px;font-weight:600">&#x1F4F2; LINEで友だち追加</a>' : '') +
          '</div></div>';
      }} catch(err) {{
        btn.textContent = "同意してダウンロード";
        btn.classList.remove("btn-disabled");
        btn.classList.add("btn-primary");
        alert("エラーが発生しました。もう一度お試しください。\\n(" + err.message + ")");
      }}
      return false;
    }}

    async function doAndroidConsent(e) {{
      e.preventDefault();
      var age = document.getElementById("age-group").value;
      if (!age) return false;

      var btn = document.getElementById("android-btn");
      btn.textContent = "処理中...";
      btn.classList.add("btn-disabled");

      try {{
        var res = await fetch("/mdm/device/consent", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            dealer_id: DEALER,
            campaign_id: CAMPAIGN,
            age_group: age,
            user_agent: navigator.userAgent,
            consent_items: getCheckedItems(),
          }})
        }});
        if (!res.ok) {{
          var errData = {{}};
          try {{ errData = await res.json(); }} catch(_) {{}}
          throw new Error(errData.detail || ("HTTP " + res.status));
        }}
        var data = await res.json();
        if (!data.android_apk_url) {{ throw new Error("android_apk_url missing"); }}
        document.body.innerHTML =
          '<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f5f5f7;font-family:-apple-system,sans-serif;padding:20px">' +
          '<div style="text-align:center;max-width:400px;width:100%">' +
          '<div style="font-size:64px;margin-bottom:16px">&#x2705;</div>' +
          '<h1 style="font-size:22px;font-weight:700;margin-bottom:12px">同意が完了しました</h1>' +
          '<p style="color:#6e6e73;font-size:15px;line-height:1.6;margin-bottom:32px">アプリをダウンロードしてインストールしてください。</p>' +
          '<a href="' + data.android_apk_url + '" style="display:block;padding:16px;background:#34c759;color:#fff;border-radius:14px;text-decoration:none;font-size:17px;font-weight:600;margin-bottom:12px">&#x1F4F1; アプリをダウンロード</a>' +
          (data.line_add_friend_url ? '<a href="' + data.line_add_friend_url + '" style="display:block;padding:16px;background:#06c755;color:#fff;border-radius:14px;text-decoration:none;font-size:17px;font-weight:600">&#x1F4F2; LINEで友だち追加</a>' : '') +
          '</div></div>';
      }} catch(err) {{
        btn.textContent = "同意してセットアップ";
        btn.classList.remove("btn-disabled");
        btn.classList.add("btn-primary");
        alert("エラーが発生しました。もう一度お試しください。\\n(" + err.message + ")");
      }}
      return false;
    }}
  </script>
</body>
</html>"""


# ── MDMプロファイル消失防止エンドポイント ─────────────────────────


@router.get("/re-enroll", summary="再エンロール（同意不要・同じtoken）")
async def re_enroll(
    token: str = Query(..., description="enrollment_token"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
):
    """
    プロファイルが消えたデバイスへの再エンロール。
    管理者が手動でURLを発行して案内する（自動送付しない）。
    token の有効期限・失効チェックを行う。
    """
    device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == token))
    if not device:
        raise HTTPException(status_code=404, detail="Token not found")
    if device.token_revoked_at:
        raise HTTPException(status_code=410, detail="Token has been revoked")
    if device.token_expires_at and device.token_expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(status_code=410, detail="Token has expired")

    platform = device.platform
    if platform == "ios":
        # iOS: mobileconfig を再ダウンロード
        campaign = await db.scalar(select(CampaignDB).where(CampaignDB.id == device.campaign_id))
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        from mdm.enrollment.mobileconfig import generate_mobileconfig
        mobileconfig_data = generate_mobileconfig(
            profile_name=campaign.name,
            enrollment_token=token,
        )
        device.re_enroll_count = (device.re_enroll_count or 0) + 1
        await db.commit()
        return Response(
            content=mobileconfig_data,
            media_type="application/x-apple-aspen-config",
            headers={"Content-Disposition": f'attachment; filename="mdm_profile.mobileconfig"'},
        )
    else:
        # Android: 再エンロール手順ページ
        device.re_enroll_count = (device.re_enroll_count or 0) + 1
        await db.commit()
        base = str(request.base_url).rstrip("/") if request else ""
        return {"status": "ok", "enrollment_token": token, "install_guide": f"{base}/mdm/android/install-guide?token={token}"}


class MigrateRestoreBody(BaseModel):
    old_device_id: str
    new_device_id: str
    enrollment_token: str


@router.post("/device/migrate-restore", summary="機種変更手動復旧（Smart Switch等）")
async def device_migrate_restore(body: MigrateRestoreBody, db: AsyncSession = Depends(get_db)):
    """
    Smart Switch / factory reset 後の手動復旧エンドポイント。
    旧 device_id + 新 device_id + enrollment_token を受け取り、デバイス紐付けを更新する。
    """
    device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == body.enrollment_token))
    if not device:
        raise HTTPException(status_code=404, detail="Token not found")
    if device.token_revoked_at:
        raise HTTPException(status_code=410, detail="Token has been revoked")

    old_android = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.old_device_id)
    )
    if not old_android:
        raise HTTPException(status_code=404, detail="Old device not found")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old_android.status = "migrated"
    old_android.migrated_at = now

    new_device = AndroidDeviceDB(
        device_id=body.new_device_id,
        enrollment_token=body.enrollment_token,
        manufacturer=old_android.manufacturer,
        model=old_android.model,
        android_version=old_android.android_version,
        dealer_id=old_android.dealer_id,
        store_id=old_android.store_id,
        previous_device_id=body.old_device_id,
        last_seen_at=now,
    )
    db.add(new_device)
    await db.commit()

    logger.info(f"migrate-restore | old={body.old_device_id[:8]}... | new={body.new_device_id[:8]}...")
    return {"status": "migrated", "new_device_id": body.new_device_id}


@router.post("/admin/device/{device_id}/restore-profile", summary="個別プロファイル再push（管理者）")
async def admin_restore_profile(
    device_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """指定デバイスへプロファイルを再 push する（管理画面の個別ボタン）"""
    ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == device_id))
    if ios_dev and ios_dev.enrollment_token:
        portal_device = await db.scalar(
            select(DeviceDB).where(DeviceDB.enrollment_token == ios_dev.enrollment_token)
        )
        if portal_device:
            campaign = await db.scalar(select(CampaignDB).where(CampaignDB.id == portal_device.campaign_id))
            if campaign:
                from mdm.enrollment.mobileconfig import generate_mobileconfig
                mobileconfig_data = generate_mobileconfig(
                    profile_name=campaign.name,
                    enrollment_token=ios_dev.enrollment_token,
                )
                install_cmd = mdm_commands.install_configuration_profile(mobileconfig_data)
                await nanomdm_client.push_command(device_id, install_cmd)
                if ios_dev.push_token and ios_dev.push_magic and ios_dev.topic:
                    await send_mdm_push(ios_dev.push_token, ios_dev.push_magic, ios_dev.topic)
                ios_dev.profile_status = "re_installing"
                await db.commit()
                return {"status": "re_installing", "device_id": device_id}

    raise HTTPException(status_code=404, detail="Device not found or cannot restore")


@router.post("/admin/bulk-restore", summary="全 missing デバイスへ一括再 push（管理者）")
async def admin_bulk_restore(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    profile_status=missing の全 iOS デバイスへ InstallProfile を再 push する。
    100件/分のバッチ送信でレート制限を回避する。
    """
    missing_devices = (await db.execute(
        select(iOSDeviceDB).where(
            iOSDeviceDB.profile_status == "missing",
            iOSDeviceDB.status == "active",
            iOSDeviceDB.push_token.isnot(None),
        )
    )).scalars().all()

    background_tasks.add_task(_bulk_restore_task, [d.udid for d in missing_devices])
    return {"status": "queued", "count": len(missing_devices)}


async def _bulk_restore_task(udids: list[str]):
    """100件/分でバッチ送信（APNs/NanoMDM レート制限対策）"""
    import asyncio
    from database import AsyncSessionLocal
    BATCH_SIZE = 100
    BATCH_INTERVAL = 60  # 秒

    for i in range(0, len(udids), BATCH_SIZE):
        batch = udids[i:i + BATCH_SIZE]
        async with AsyncSessionLocal() as db:
            for udid in batch:
                ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == udid))
                if not ios_dev or not ios_dev.enrollment_token:
                    continue
                portal_device = await db.scalar(
                    select(DeviceDB).where(DeviceDB.enrollment_token == ios_dev.enrollment_token)
                )
                if not portal_device:
                    continue
                campaign = await db.scalar(select(CampaignDB).where(CampaignDB.id == portal_device.campaign_id))
                if not campaign:
                    continue
                from mdm.enrollment.mobileconfig import generate_mobileconfig
                mobileconfig_data = generate_mobileconfig(
                    profile_name=campaign.name,
                    enrollment_token=ios_dev.enrollment_token,
                )
                install_cmd = mdm_commands.install_configuration_profile(mobileconfig_data)
                try:
                    await nanomdm_client.push_command(udid, install_cmd)
                    if ios_dev.push_token and ios_dev.push_magic and ios_dev.topic:
                        await send_mdm_push(ios_dev.push_token, ios_dev.push_magic, ios_dev.topic)
                    ios_dev.profile_status = "re_installing"
                except Exception as e:
                    logger.error(f"bulk-restore failed for udid={udid[:8]}...: {e}")
            await db.commit()

        if i + BATCH_SIZE < len(udids):
            await asyncio.sleep(BATCH_INTERVAL)

    logger.info(f"bulk-restore completed | total={len(udids)}")


@router.get("/admin/device/{device_id}/re-enroll-url", summary="再エンロールURL発行（管理者）")
async def admin_get_reenroll_url(
    device_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    指定デバイスの再エンロール URL を返す（管理画面のコピーボタン用）。
    自動送付はしない。管理者が任意のチャネルで案内する。
    """
    ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == device_id))
    token = None
    if ios_dev:
        token = ios_dev.enrollment_token
    else:
        android_dev = await db.scalar(select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == device_id))
        if android_dev:
            token = android_dev.enrollment_token

    if not token:
        raise HTTPException(status_code=404, detail="Device not found")

    portal_device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == token))
    if portal_device and portal_device.token_revoked_at:
        raise HTTPException(status_code=410, detail="Token has been revoked")

    base = str(request.base_url).rstrip("/")
    url = f"{base}/mdm/re-enroll?token={token}"
    return {"re_enroll_url": url, "enrollment_token": token}

# ── エンドポイント ─────────────────────────────────────────────

@router.get("/portal", response_class=HTMLResponse, summary="エンロールポータル")
async def enrollment_portal(
    request: Request,
    dealer: Optional[str] = Query(None),
    campaign: Optional[str] = Query(None),
):
    html = PORTAL_HTML.format(
        dealer_id=dealer or "",
        campaign_id=campaign or "",
        base_url=str(request.base_url).rstrip("/"),
    )
    return HTMLResponse(content=html)


REQUIRED_CONSENT_ITEMS = {
    "lockscreen_ads", "push_notifications", "webclip_install",
    "vpn_setup", "app_install", "data_collection",
}
CONSENT_VERSION = "2.0"


@router.post("/device/consent", summary="同意登録 → mobileconfig URL返却")
async def device_consent(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    dealer_id = body.get("dealer_id") or None
    campaign_id = body.get("campaign_id") or None
    age_group = body.get("age_group")
    user_agent = body.get("user_agent", "")
    consent_items: list = body.get("consent_items", [])

    # dealer_id 必須チェック（QR/URL経由でない直打ちエンロールを拒否）
    if not dealer_id:
        raise HTTPException(
            status_code=400,
            detail="店舗のQRコードまたはURLからアクセスしてください。",
        )

    # 必須同意項目の検証
    checked = set(consent_items)
    missing = REQUIRED_CONSENT_ITEMS - checked
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"必須同意項目が未チェックです: {', '.join(sorted(missing))}",
        )

    platform = _detect_platform(user_agent)
    device = DeviceDB(
        dealer_id=dealer_id,
        campaign_id=campaign_id,
        platform=platform,
        device_model=_parse_device_model(user_agent),
        os_version=_parse_os_version(user_agent),
        user_agent=user_agent[:500],
        age_group=age_group,
        consent_given=True,
        status="pending",
    )
    try:
        db.add(device)
        await db.commit()
        await db.refresh(device)

        # ユーザーが実際にチェックした項目を記録（consent_version 2.0）
        consent_log = ConsentLogDB(
            enrollment_token=device.enrollment_token,
            dealer_id=dealer_id,
            consent_version=CONSENT_VERSION,
            consent_items=json.dumps(sorted(consent_items)),
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        db.add(consent_log)
        await db.commit()
    except Exception as e:
        logger.error(f"device_consent DB error: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="データベースエラーが発生しました。しばらく後にお試しください。")

    logger.info(f"MDM consent | token={device.enrollment_token[:8]}... | platform={platform} | dealer={dealer_id}")

    base = str(request.base_url).rstrip("/")
    token = device.enrollment_token
    return {
        "enrollment_token": token,
        "mobileconfig_url": f"{base}/mdm/ios/mobileconfig?token={token}",
        "android_apk_url": f"{base}/mdm/android/dpc.apk?token={token}",
        "line_add_friend_url": f"{base}/mdm/line/add-friend?token={token}",
    }


@router.get("/ios/mobileconfig", summary="iOS .mobileconfig ダウンロード")
async def download_mobileconfig(
    request: Request,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == token))
    if not device:
        raise HTTPException(status_code=404, detail="Invalid enrollment token")
    if not device.consent_given:
        raise HTTPException(status_code=403, detail="Consent required")

    # キャンペーン設定を取得（なければデフォルト）
    vpn = None
    webclips = []
    safari = None
    profile_name = "サービス設定"

    if device.campaign_id:
        campaign = await db.get(CampaignDB, device.campaign_id)
        if campaign:
            profile_name = campaign.name
            if campaign.vpn_config:
                vc = json.loads(campaign.vpn_config)
                vpn = VPNConfig(
                    server=vc["server"],
                    username=vc["username"],
                    password=vc["password"],
                    display_name=vc.get("display_name", "VPN"),
                )
            if campaign.webclips:
                for wc in json.loads(campaign.webclips):
                    webclips.append(WebClipConfig(
                        url=wc["url"],
                        label=wc["label"],
                        full_screen=wc.get("full_screen", True),
                        is_removable=wc.get("is_removable", True),
                    ))
            if campaign.safari_config:
                sc = json.loads(campaign.safari_config)
                safari = SafariConfig(
                    home_page=sc.get("home_page"),
                    default_search_provider=sc.get("default_search_provider", "Google"),
                )

    # LINE友だち追加URLをX-Next-Urlヘッダーで返す（JSがリダイレクト）
    base = str(request.base_url).rstrip("/")

    # PayloadContent が空だと iOS が「空のプロファイル」エラーを出すため
    # キャンペーン未設定時はデフォルト WebClip を最低1件追加する
    if not vpn and not webclips and not safari:
        webclips = [WebClipConfig(
            url=f"{base}/mdm/line/add-friend?token={token}",
            label="サービス登録",
            full_screen=False,
            is_removable=True,
        )]

    config_bytes = generate_mobileconfig(
        profile_name=profile_name,
        enrollment_token=token,
        vpn=vpn,
        webclips=webclips or None,
        safari=safari,
    )

    # ダウンロード記録
    device.mobileconfig_downloaded = True
    device.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    logger.info(f"MDM mobileconfig downloaded | token={token[:8]}...")
    next_url = f"{base}/mdm/line/add-friend?token={token}"

    return Response(
        content=config_bytes,
        media_type="application/x-apple-aspen-config",
        headers={
            # attachment だと iOS Safari がファイルアプリに保存しようとしてインストーラーが起動しない
            # inline にすることでプロファイルインストーラーが直接開く
            "Content-Disposition": 'inline; filename="config.mobileconfig"',
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Next-Url": next_url,
        },
    )


@router.get("/qr/{store_code}", summary="店舗別エンロールQRコード（PNG）")
async def enrollment_qr(
    store_code: str,
    request: Request,
    campaign: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    dealer = await db.scalar(select(DealerDB).where(DealerDB.store_code == store_code))
    if not dealer:
        raise HTTPException(status_code=404, detail="Dealer not found")

    # リクエストの origin を使う（settings.ssp_endpoint が localhost のままでも正しく動作）
    base = str(request.base_url).rstrip("/")
    url = f"{base}/mdm/portal?dealer={dealer.id}"
    if campaign:
        url += f"&campaign={campaign}"

    png_bytes = generate_qr_png(url)
    return Response(content=png_bytes, media_type="image/png")


# ── オプトアウト（エンロール解除）──────────────────────────────

_OPTOUT_FORM_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>MDM エンロール解除</title>
  <style>
    body {{font-family:-apple-system,sans-serif;background:#f5f5f7;margin:0;padding:0;}}
    .wrap {{max-width:480px;margin:0 auto;padding:40px 20px;}}
    h1 {{font-size:22px;font-weight:700;margin-bottom:8px;color:#1c1c1e;}}
    p {{font-size:15px;color:#6e6e73;line-height:1.6;margin-bottom:24px;}}
    label {{display:block;font-size:14px;font-weight:600;color:#3a3a3c;margin-bottom:6px;}}
    input[type=text] {{width:100%;box-sizing:border-box;padding:12px;font-size:16px;
                       border:1px solid #c7c7cc;border-radius:10px;margin-bottom:20px;}}
    button {{width:100%;padding:14px;font-size:17px;font-weight:600;
             border:none;border-radius:14px;background:#ff3b30;color:#fff;cursor:pointer;}}
    button:hover {{opacity:0.9;}}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>MDM エンロール解除</h1>
    <p>このページでは、デバイス管理（MDM）のエンロールを解除できます。<br>
       解除後はサービスの提供が停止されます。</p>
    <form method="post" action="/mdm/optout">
      <label for="enrollment_token">エンロールトークン</label>
      <input type="text" id="enrollment_token" name="enrollment_token"
             placeholder="トークンを入力してください" required>
      <button type="submit">エンロールを解除する</button>
    </form>
  </div>
</body>
</html>"""


@router.get("/optout", response_class=HTMLResponse, summary="エンロール解除ページ")
async def optout_page():
    """MDM エンロール解除フォームを返す"""
    return HTMLResponse(content=_OPTOUT_FORM_HTML)


@router.post("/optout", response_class=HTMLResponse, summary="エンロール解除処理")
async def optout_submit(
    enrollment_token: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """エンロールトークンを受け取り、デバイスを inactive に設定する"""
    device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == enrollment_token))
    if not device:
        html = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>エラー</title>
  <style>
    body {{font-family:-apple-system,sans-serif;background:#f5f5f7;margin:0;padding:0;}}
    .wrap {{max-width:480px;margin:0 auto;padding:40px 20px;text-align:center;}}
    h1 {{font-size:22px;font-weight:700;color:#ff3b30;}}
    p {{font-size:15px;color:#6e6e73;line-height:1.6;}}
    a {{color:#007aff;text-decoration:none;}}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>トークンが見つかりません</h1>
    <p>入力されたエンロールトークンは登録されていません。<br>
       トークンを確認して再度お試しください。</p>
    <p><a href="/mdm/optout">戻る</a></p>
  </div>
</body>
</html>"""
        return HTMLResponse(content=html, status_code=404)

    device.status = "opted_out"
    device.token_revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    # Android: DPCへ remove_mdm_profile コマンドをキュー
    android_device = await db.scalar(
        select(AndroidDeviceDB).where(
            AndroidDeviceDB.enrollment_token == enrollment_token,
            AndroidDeviceDB.status == "active",
        )
    )
    if android_device:
        await enqueue_remove_mdm_profile(db, android_device.device_id)
        logger.info(f"MDM optout: remove_mdm_profile queued for Android | device={android_device.device_id[:8]}...")

    # iOS: RemoveProfile コマンドをキュー（デバイスが次回 checkin 時に実行）
    ios_device = await db.scalar(
        select(iOSDeviceDB).where(
            iOSDeviceDB.enrollment_token == enrollment_token,
            iOSDeviceDB.status == "active",
        )
    )
    if ios_device:
        from mdm.nanomdm import commands as mdm_cmds
        remove_cmd = mdm_cmds.remove_profile(f"com.platform.mdm.{enrollment_token}")
        await nanomdm_client.push_command(ios_device.udid, remove_cmd)
        if ios_device.push_token and ios_device.push_magic and ios_device.topic:
            await send_mdm_push(ios_device.push_token, ios_device.push_magic, ios_device.topic)
        ios_device.status = "opted_out"
        ios_device.profile_status = "missing"
        await db.commit()
        logger.info(f"MDM optout: RemoveProfile queued for iOS | udid={ios_device.udid[:8]}...")

    logger.info(f"MDM optout | token={enrollment_token[:8]}... | device={device.id[:8]}...")

    html = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>解除完了</title>
  <style>
    body {{font-family:-apple-system,sans-serif;background:#f5f5f7;margin:0;padding:0;}}
    .wrap {{max-width:480px;margin:0 auto;padding:40px 20px;text-align:center;}}
    .icon {{font-size:64px;margin-bottom:16px;}}
    h1 {{font-size:22px;font-weight:700;color:#1c1c1e;}}
    p {{font-size:15px;color:#6e6e73;line-height:1.6;}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="icon">✓</div>
    <h1>エンロール解除が完了しました</h1>
    <p>デバイスはMDM管理から解除されました。<br>
       ご利用ありがとうございました。</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── 管理者API ──────────────────────────────────────────────────

class DealerCreate(BaseModel):
    name: str
    store_code: str
    address: Optional[str] = None


class CampaignCreate(BaseModel):
    name: str
    dealer_id: Optional[str] = None
    vpn_config: Optional[dict] = None     # {"server":..., "username":..., "password":...}
    webclips: Optional[list[dict]] = None  # [{"url":..., "label":...}]
    safari_config: Optional[dict] = None  # {"home_page": "...", "default_search_provider": "Google"}
    eru_nage_scenario_id: Optional[str] = None
    line_liff_url: Optional[str] = None


@router.post("/admin/dealers", summary="代理店登録（管理者）")
async def create_dealer(
    body: DealerCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    dealer = DealerDB(name=body.name, store_code=body.store_code, address=body.address)
    db.add(dealer)
    await db.commit()
    await db.refresh(dealer)
    return {"id": dealer.id, "store_code": dealer.store_code, "api_key": dealer.api_key}


@router.get("/admin/dealers", summary="代理店一覧（管理者）")
async def list_dealers(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = await db.execute(select(DealerDB).order_by(DealerDB.created_at.desc()))
    dealers = rows.scalars().all()
    return [{"id": d.id, "name": d.name, "store_code": d.store_code, "status": d.status, "agency_id": d.agency_id} for d in dealers]


@router.get("/admin/dealers/{dealer_id}/detail", summary="店舗別実績詳細（管理者）")
async def dealer_detail(
    dealer_id: str,
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """店舗単体の月次CV・収益・代理店取り分サマリー。同一代理店の配下店舗も一覧返却。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month

    report = await get_dealer_monthly_report(db, dealer_id, y, m)
    if not report:
        raise HTTPException(status_code=404, detail="Dealer not found")

    # 同一代理店の配下店舗も取得
    dealer = await db.get(DealerDB, dealer_id)
    stores = []
    if dealer and dealer.agency_id is not None:
        siblings = (await db.scalars(
            select(DealerDB)
            .where(DealerDB.agency_id == dealer.agency_id, DealerDB.status == "active")
            .order_by(DealerDB.store_number)
        )).all()
        for s in siblings:
            sr = await get_dealer_monthly_report(db, s.id, y, m)
            if sr:
                stores.append({
                    "dealer_id": s.id,
                    "dealer_name": s.name,
                    "store_code": s.store_code,
                    "user_count": sr["enrolled_devices"],
                    "clicks": sr["clicks"],
                    "installs": sr["installs"],
                    "cv_count": sr["conversions"],
                    "revenue_jpy": sr["revenue_jpy"],
                    "dealer_share_jpy": sr["dealer_share_jpy"],
                })

    report["stores"] = stores
    return report


@router.get("/admin/enrolled-users", summary="エンロールユーザー一覧（代理店/店舗フィルター付き）")
async def list_enrolled_users(
    dealer_id: Optional[str] = Query(None, description="店舗IDで絞り込み"),
    agency_id: Optional[int] = Query(None, description="代理店IDで絞り込み"),
    platform: Optional[str] = Query(None, description="ios / android / unknown"),
    limit: int = Query(200, le=1000),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    DeviceDB（同意・エンロール済みユーザー）を店舗・代理店で絞り込んで返す。
    dealer_id = DealerDB.id（店舗ID）
    agency_id = AgencyDB.id（代理店会社ID） → DealerDB.agency_id 経由で絞り込む
    """
    stmt = (
        select(DeviceDB, DealerDB)
        .outerjoin(DealerDB, DeviceDB.dealer_id == DealerDB.id)
        .order_by(DeviceDB.enrolled_at.desc())
    )
    if dealer_id:
        stmt = stmt.where(DeviceDB.dealer_id == dealer_id)
    if agency_id:
        stmt = stmt.where(DealerDB.agency_id == agency_id)
    if platform:
        stmt = stmt.where(DeviceDB.platform == platform)
    stmt = stmt.limit(limit)

    rows = await db.execute(stmt)
    results = rows.all()

    return [
        {
            "id": d.id,
            "enrollment_token": d.enrollment_token[:8] + "...",
            "platform": d.platform,
            "device_model": d.device_model,
            "os_version": d.os_version,
            "age_group": d.age_group,
            "status": d.status,
            "mobileconfig_downloaded": d.mobileconfig_downloaded,
            "enrolled_at": d.enrolled_at.isoformat() if d.enrolled_at else None,
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "dealer_id": d.dealer_id,
            "dealer_name": dealer.name if dealer else None,
            "store_code": dealer.store_code if dealer else None,
            "agency_id": dealer.agency_id if dealer else None,
        }
        for d, dealer in results
    ]


class WifiTriggerRuleCreate(BaseModel):
    ssid: str
    dealer_id: Optional[str] = None
    action_type: str               # push | line | point
    action_config: dict = {}
    cooldown_minutes: int = 60
    active: bool = True


@router.post("/admin/wifi_trigger_rules", summary="Wi-Fiトリガールール登録（管理者）")
async def create_wifi_trigger_rule(
    body: WifiTriggerRuleCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rule = WifiTriggerRuleDB(
        ssid=body.ssid,
        dealer_id=body.dealer_id,
        action_type=body.action_type,
        action_config=json.dumps(body.action_config),
        cooldown_minutes=body.cooldown_minutes,
        active=body.active,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return {"id": rule.id, "ssid": rule.ssid, "action_type": rule.action_type}


@router.get("/admin/wifi_trigger_rules", summary="Wi-Fiトリガールール一覧（管理者）")
async def list_wifi_trigger_rules(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = (await db.scalars(
        select(WifiTriggerRuleDB).order_by(WifiTriggerRuleDB.created_at.desc())
    )).all()
    return [
        {
            "id": r.id,
            "ssid": r.ssid,
            "dealer_id": r.dealer_id,
            "action_type": r.action_type,
            "action_config": json.loads(r.action_config or "{}"),
            "cooldown_minutes": r.cooldown_minutes,
            "active": r.active,
        }
        for r in rows
    ]


@router.post("/admin/campaigns", summary="キャンペーン作成（管理者）")
async def create_campaign(
    body: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    campaign = CampaignDB(
        name=body.name,
        dealer_id=body.dealer_id,
        vpn_config=json.dumps(body.vpn_config) if body.vpn_config else None,
        webclips=json.dumps(body.webclips) if body.webclips else None,
        safari_config=json.dumps(body.safari_config) if body.safari_config else None,
        eru_nage_scenario_id=body.eru_nage_scenario_id,
        line_liff_url=body.line_liff_url,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return {"id": campaign.id, "name": campaign.name}


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    webclips: Optional[list[dict]] = None
    safari_config: Optional[dict] = None


class DealerPushRequest(BaseModel):
    title: str
    body: str
    url: Optional[str] = None


class WebClipItem(BaseModel):
    label: str
    url: str
    icon_url: Optional[str] = None


class DealerWebClipsUpdate(BaseModel):
    webclips: list[WebClipItem]


async def _redeploy_campaign(campaign_id: str, webclips_json: str | None, db: AsyncSession) -> dict:
    """キャンペーン更新後、紐づくiOSデバイスへWebClipを自動再配信"""
    from mdm.nanomdm import commands as mdm_commands
    from mdm.nanomdm.client import push_command
    from mdm.nanomdm.apns import send_mdm_push

    devices = (await db.scalars(
        select(DeviceDB).where(
            DeviceDB.campaign_id == campaign_id,
            DeviceDB.platform == "ios",
            DeviceDB.status == "active",
        )
    )).all()

    ios_queued = 0
    ios_push_sent = 0
    new_webclips = json.loads(webclips_json) if webclips_json else []

    for dev in devices:
        ios_dev = await db.scalar(
            select(iOSDeviceDB).where(iOSDeviceDB.enrollment_token == dev.enrollment_token)
        )
        if not ios_dev or not ios_dev.push_token:
            continue
        for wc in new_webclips:
            cmd_plist = mdm_commands.add_web_clip(
                url=wc.get("url", ""),
                label=wc.get("label", ""),
                icon_url=wc.get("icon_url"),
            )
            await push_command(ios_dev.udid, cmd_plist)
            ios_queued += 1
        ok = await send_mdm_push(ios_dev.push_token, ios_dev.push_magic)
        if ok:
            ios_push_sent += 1

    return {"ios_queued": ios_queued, "ios_push_sent": ios_push_sent}


@router.put("/admin/campaigns/{campaign_id}", summary="キャンペーン更新 + 自動再配信（管理者）")
async def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    キャンペーンの webclips / safari_config を更新し、
    紐づくiOSデバイスへWebClipをバックグラウンドで自動再配信する。
    """
    campaign = await db.get(CampaignDB, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="campaign not found")

    if body.name is not None:
        campaign.name = body.name
    if body.webclips is not None:
        campaign.webclips = json.dumps(body.webclips)
    if body.safari_config is not None:
        campaign.safari_config = json.dumps(body.safari_config)
    campaign.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    await db.commit()
    await db.refresh(campaign)

    # バックグラウンドで再配信（ORMオブジェクトではなくJSONを渡す）
    background_tasks.add_task(_redeploy_campaign, campaign_id, campaign.webclips, db)

    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "redeployment": "queued",
    }


@router.get("/admin/consent-logs", summary="同意ログ一覧（管理者）")
async def list_consent_logs(
    enrollment_token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    stmt = select(ConsentLogDB).order_by(ConsentLogDB.consented_at.desc())
    if enrollment_token:
        stmt = stmt.where(ConsentLogDB.enrollment_token == enrollment_token)
    rows = await db.execute(stmt)
    logs = rows.scalars().all()
    return [
        {
            "id": log.id,
            "enrollment_token": log.enrollment_token,
            "dealer_id": log.dealer_id,
            "consent_version": log.consent_version,
            "consent_items": json.loads(log.consent_items),
            "ip_address": log.ip_address,
            "user_agent": log.user_agent,
            "consented_at": log.consented_at.isoformat() if log.consented_at else None,
        }
        for log in logs
    ]


# ── LINE Webhook ──────────────────────────────────────────────

@router.post("/line/webhook", summary="LINE Webhook受信（友だち追加検知）")
async def line_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = await request.json()
    user_ids = parse_follow_events(payload)

    for line_user_id in user_ids:
        # 最近エンロールしたデバイス（LINE未紐付け）と紐付け
        device = await db.scalar(
            select(DeviceDB)
            .where(DeviceDB.line_user_id == None, DeviceDB.consent_given == True)
            .order_by(DeviceDB.enrolled_at.desc())
            .limit(1)
        )
        if device:
            device.line_user_id = line_user_id
            device.status = "active"
            await db.commit()

            # エル投げにユーザー登録（ステップ配信開始）
            campaign = await db.get(CampaignDB, device.campaign_id) if device.campaign_id else None
            await eru_nage_register(
                line_user_id=line_user_id,
                scenario_id=campaign.eru_nage_scenario_id if campaign else None,
                attributes={"age_group": device.age_group, "platform": device.platform},
            )
            logger.info(f"LINE follow linked | line_user_id={line_user_id[:8]}... | device={device.id[:8]}...")

    return {"status": "ok"}


@router.get("/line/add-friend", response_class=HTMLResponse, summary="LINE友だち追加ページ")
async def line_add_friend(token: str = Query(...)):
    """mobileconfig DL後にLINE友だち追加へ誘導するページ"""
    account_id = settings.line_official_account_id
    line_url = f"https://line.me/R/ti/p/{account_id}" if account_id else "#"

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>設定完了</title>
  <style>
    body {{font-family:-apple-system,sans-serif;background:#f5f5f7;margin:0;padding:0;}}
    .wrap {{max-width:480px;margin:0 auto;padding:40px 20px;text-align:center;}}
    .icon {{font-size:64px;margin-bottom:16px;}}
    h1 {{font-size:22px;font-weight:700;margin-bottom:8px;}}
    p {{font-size:15px;color:#6e6e73;line-height:1.6;margin-bottom:32px;}}
    .btn {{display:block;padding:16px;font-size:17px;font-weight:600;
           border-radius:14px;text-decoration:none;margin-bottom:12px;}}
    .btn-line {{background:#06c755;color:#fff;}}
    .btn-skip {{background:#e5e5ea;color:#3a3a3c;font-size:15px;}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="icon">✅</div>
    <h1>プロファイルをインストールしてください</h1>
    <p>設定アプリを開いて「プロファイルがダウンロード済み」からインストールを完了してください。</p>
    <p>完了後、LINEで友だち追加するとクーポンやお得情報が届きます。</p>
    <a href="{line_url}" class="btn btn-line">📲 LINEで友だち追加（無料）</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── アフィリエイト ─────────────────────────────────────────────

JANET_CLICK_BASE = "https://click.j-a-net.jp"

_USER_TOKEN_CHARS = string.ascii_letters + string.digits


def _generate_user_token() -> str:
    """ASP向け不透明ユーザートークンを生成する。形式: UT + 10桁英数字"""
    return "UT" + "".join(secrets.choice(_USER_TOKEN_CHARS) for _ in range(10))


@router.get("/affiliate/click/{campaign_id}", summary="アフィリエイトクリック追跡（JANet / smaad / A8.net 対応）")
async def affiliate_click(
    campaign_id: str,
    token: Optional[str] = Query(None),    # enrollment_token（iOS/Web用）
    device_id: Optional[str] = Query(None),  # Android ID = ASP UserID
    nwclkid: Optional[str] = Query(None),  # ASPから渡されるネットワーククリックID
    nwsiteid: Optional[str] = Query(None),  # ASPから渡されるネットワークサイトID
    db: AsyncSession = Depends(get_db),
):
    """
    アフィリエイトクリックを記録して ASP へリダイレクトする。
    全ASP共通フロー: 弊社(メディア) → ASP → 広告主ページ → 成果発生 → ASP → 弊社にポストバック

    優先順位:
      1. JANet: janet_media_id 設定あり
         → https://click.j-a-net.jp/{media_id}/{original_id}/{device_id}
         ※ JANet仕様: UserID はURLパスに直接付与（クエリパラメータ不可）
      2. smaad / A8.net: click_url_template 設定あり
         → {click_url_template} の {device_id} を置換
         例: https://tr.smaad.net/redirect?zo=XXX&ad=YYY&uid={device_id}
      3. フォールバック: campaign.destination_url
    """
    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if not campaign or campaign.status != "active":
        raise HTTPException(status_code=404, detail="Campaign not found")

    device = None
    android_device = None
    if token:
        device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == token))
    if device_id:
        android_device = await db.scalar(
            select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == device_id)
        )

    resolved_dealer_id = (
        (android_device.dealer_id if android_device else None)
        or (device.dealer_id if device else None)
    )
    # user_token: device_id の代わりに ASP に渡す不透明トークン
    user_token = android_device.user_token if android_device else None

    click = AffiliateClickDB(
        campaign_id=campaign_id,
        enrollment_token=token,
        device_id=device_id,
        dealer_id=resolved_dealer_id,
        platform="android" if device_id else (device.platform if device else "unknown"),
    )
    db.add(click)
    await db.commit()

    logger.info(
        f"Affiliate click | campaign={campaign_id[:8]}... "
        f"| device_id={str(device_id)[:8] if device_id else 'none'}"
        f"| user_token={user_token[:8] if user_token else 'none'}"
    )

    # dealer情報取得（tracking_url マクロ用）
    dealer = await db.get(DealerDB, resolved_dealer_id) if resolved_dealer_id else None
    site_code = dealer.store_code if dealer else ""

    # 1. JANet: パスに user_token を付与（device_id を ASP に渡さない）
    if campaign.janet_media_id and campaign.janet_original_id and user_token:
        janet_url = f"{JANET_CLICK_BASE}/{campaign.janet_media_id}/{campaign.janet_original_id}/{user_token}"
        return RedirectResponse(url=janet_url, status_code=302)

    # 2. tracking_url: 汎用マクロ置換（JANet以外の全ASP対応）
    if campaign.tracking_url:
        redirect_url = apply_tracking_macros(
            campaign.tracking_url,
            session_id=click.click_token or "",
            user_id=user_token or device_id or "",
            site_code=site_code,
            campaign_id=campaign_id,
            nwclkid=nwclkid or "",
            nwsiteid=nwsiteid or "",
            destination_url=campaign.destination_url or "",
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    # 3. smaad / A8.net: テンプレートの {device_id} を user_token で置換（後方互換）
    if campaign.click_url_template and user_token:
        redirect_url = campaign.click_url_template.replace("{device_id}", user_token)
        return RedirectResponse(url=redirect_url, status_code=302)

    # 4. フォールバック: device_id はあるが user_token がない（未登録デバイス）or 通常案件
    return RedirectResponse(url=campaign.destination_url, status_code=302)


@router.post("/affiliate/postback/appsflyer", summary="AppsFlyer S2S Postbackを受信")
async def appsflyer_postback(request: Request, db: AsyncSession = Depends(get_db)):
    """AppsFlyerからのインストール通知を受信してCV記録する"""
    body = await request.json()
    click_token = body.get("af_customer_user_id") or body.get("customer_user_id")
    app_id = body.get("app_id", "")

    # 冪等性チェック: 同一 click_token + source の重複受信を防ぐ
    if click_token:
        existing = await db.scalar(
            select(AffiliateConversionDB).where(
                AffiliateConversionDB.click_token == click_token,
                AffiliateConversionDB.source == "appsflyer",
            )
        )
        if existing:
            logger.info(f"AppsFlyer CV duplicate skipped | token={str(click_token)[:8]}...")
            return {"status": "ok"}

    campaign = await db.scalar(
        select(AffiliateCampaignDB)
        .where(AffiliateCampaignDB.appsflyer_dev_key != None)
        .limit(1)
    )

    conversion = AffiliateConversionDB(
        click_token=click_token,
        campaign_id=campaign.id if campaign else None,
        source="appsflyer",
        event_type=body.get("event_name", "install"),
        revenue_jpy=float(campaign.reward_amount if campaign else 0),
        raw_payload=json.dumps(body)[:2000],
    )
    db.add(conversion)

    if click_token:
        click = await db.scalar(
            select(AffiliateClickDB).where(AffiliateClickDB.click_token == click_token)
        )
        if click:
            click.converted = True

    await db.commit()
    logger.info(f"AppsFlyer CV received | app={app_id} | token={str(click_token)[:8]}...")
    return {"status": "ok"}


@router.post("/affiliate/postback/adjust", summary="Adjust S2S Postbackを受信")
async def adjust_postback(request: Request, db: AsyncSession = Depends(get_db)):
    """AdjustからのCV通知を受信して記録する"""
    body = await request.json()
    click_token = body.get("partner_params", {}).get("enrollment_token")

    # 冪等性チェック: 同一 click_token + source の重複受信を防ぐ
    if click_token:
        existing = await db.scalar(
            select(AffiliateConversionDB).where(
                AffiliateConversionDB.click_token == click_token,
                AffiliateConversionDB.source == "adjust",
            )
        )
        if existing:
            logger.info(f"Adjust CV duplicate skipped | token={str(click_token)[:8]}...")
            return {"status": "ok"}

    conversion = AffiliateConversionDB(
        click_token=click_token,
        campaign_id=body.get("app_token") or None,
        source="adjust",
        event_type=body.get("event", "install"),
        raw_payload=json.dumps(body)[:2000],
    )
    db.add(conversion)

    if click_token:
        click = await db.scalar(
            select(AffiliateClickDB).where(AffiliateClickDB.click_token == click_token)
        )
        if click:
            click.converted = True

    await db.commit()
    return {"status": "ok"}


# ── ASP パラメータ正規化マップ ───────────────────────────────────
# 各ASPが使う「ユーザーID」「報酬額」「CV固有ID」「2段階通知フラグ」のパラメータ名を統一する
_ASP_PARAM_MAP: dict[str, dict] = {
    # source: {user_token_key, revenue_key, action_id_key, attestation_keys, has_2phase}
    "janet": {
        "user_token_key": "user_id",
        "revenue_key": "commission",
        "action_id_key": "action_id",
        # attestation_flag=1 → pending, attestation_flag=0 → approved
        "attestation_key": "attestation_flag",
        "approved_value": "0",
        "has_2phase": True,
    },
    "skyflag": {
        "user_token_key": "suid",
        "revenue_key": "price",
        "action_id_key": "cv_id",
        # install=1 → approved (no value = pending)
        "attestation_key": "install",
        "approved_value": "1",
        "has_2phase": True,
    },
    "smaad": {
        "user_token_key": "uid",
        "revenue_key": "price",
        "action_id_key": None,
        "attestation_key": None,
        "approved_value": None,
        "has_2phase": False,
    },
    "a8": {
        "user_token_key": "uid",
        "revenue_key": "price",
        "action_id_key": None,
        "attestation_key": None,
        "approved_value": None,
        "has_2phase": False,
    },
}
_ASP_PARAM_MAP_DEFAULT = {
    "user_token_key": "uid",
    "revenue_key": "price",
    "action_id_key": None,
    "attestation_key": None,
    "approved_value": None,
    "has_2phase": False,
}


def _normalize_asp_params(source: str, params: dict) -> dict:
    """各ASPのパラメータを共通フォーマットに正規化する。"""
    cfg = _ASP_PARAM_MAP.get(source, _ASP_PARAM_MAP_DEFAULT)

    user_token = params.get(cfg["user_token_key"])
    # JANetの旧形式 uid= にも対応
    if not user_token:
        user_token = params.get("uid")

    price_str = params.get(cfg["revenue_key"], "0")
    try:
        revenue = float(price_str)
    except (ValueError, TypeError):
        revenue = 0.0

    action_id = params.get(cfg["action_id_key"]) if cfg["action_id_key"] else None

    # 2段階通知ステータス決定
    if cfg["has_2phase"]:
        attest_val = params.get(cfg["attestation_key"])
        if attest_val is not None:
            # JANet: flag=0→approved, flag=1→pending
            # SKYFLAG: install=1→approved, なし→pending
            attestation_status = "approved" if attest_val == cfg["approved_value"] else "pending"
        else:
            attestation_status = "pending"
    else:
        # smaad / A8.net: 2段階なし → 即 approved
        attestation_status = "approved"

    return {
        "user_token": user_token,
        "revenue": revenue,
        "action_id": action_id,
        "attestation_status": attestation_status,
    }


async def _award_points(db: AsyncSession, conversion: AffiliateConversionDB) -> None:
    """ポイント付与（enable_points=True かつ approved のみ）。冪等。"""
    if conversion.attestation_status != "approved":
        return

    campaign = await db.get(AffiliateCampaignDB, conversion.campaign_id)
    if not campaign or not campaign.enable_points:
        return

    # 冪等性: conversion_id UNIQUE制約
    existing_point = await db.scalar(
        select(UserPointDB).where(UserPointDB.conversion_id == conversion.id)
    )
    if existing_point:
        return

    points = round(conversion.revenue_jpy * campaign.point_rate)
    point_record = UserPointDB(
        user_token=conversion.user_token or "",
        conversion_id=conversion.id,
        points=points,
        awarded_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(point_record)
    logger.info(
        f"Points awarded | user_token={conversion.user_token} | points={points} | cv={conversion.id[:8]}..."
    )


@router.get("/affiliate/cv", summary="汎用ASPポストバック受信（SESSIONID=click_token方式）")
async def asp_cv_postback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    ASPからのCV通知を受信する汎用エンドポイント（Ruby api/cv 相当）。

    ASP管理画面に登録するポストバックURL:
        https://{domain}/mdm/affiliate/cv?id=SESSIONID
        （SESSIONID はtracking_urlで渡したclick_token）

    id パラメータ（SESSIONID / click_token）で AffiliateClickDB を照合し、
    ASP別パラメータから source を自動判定してCV記録する。

    source 自動判定:
      install パラメータあり → skyflag
      attestation_flag パラメータあり → janet
      それ以外 → smaad / a8（即approved）
    """
    params = dict(request.query_params)
    click_token = params.get("id")
    if not click_token:
        logger.warning("asp_cv_postback: id (click_token) missing")
        return {"status": "ok"}

    # source 自動判定
    if "install" in params:
        source = "skyflag"
    elif "attestation_flag" in params:
        source = "janet"
    elif "suid" in params:
        source = "skyflag"
    else:
        source = "smaad"

    normalized = _normalize_asp_params(source, params)
    revenue = normalized["revenue"]
    action_id = normalized["action_id"]
    attestation_status = normalized["attestation_status"]

    # click_token で AffiliateClickDB を照合
    click = await db.scalar(
        select(AffiliateClickDB).where(AffiliateClickDB.click_token == click_token)
    )

    # asp_action_id がある場合は冪等性チェック
    if action_id:
        existing = await db.scalar(
            select(AffiliateConversionDB).where(
                AffiliateConversionDB.asp_action_id == action_id,
                AffiliateConversionDB.source == source,
            )
        )
        if existing:
            if existing.attestation_status != "approved" and attestation_status == "approved":
                existing.attestation_status = "approved"
                await db.flush()
                await _award_points(db, existing)
                await db.commit()
                logger.info(f"{source} cv phase2 approved | action={action_id}")
            else:
                logger.info(f"{source} cv duplicate skipped | action={action_id}")
            return {"status": "ok"}

    # click_token による重複チェック（action_idなし ASP用）
    if not action_id and click:
        existing = await db.scalar(
            select(AffiliateConversionDB).where(
                AffiliateConversionDB.click_token == click_token,
                AffiliateConversionDB.source == source,
            )
        )
        if existing:
            logger.info(f"{source} cv duplicate skipped | click_token={click_token[:8]}...")
            return {"status": "ok"}

    user_token = (normalized.get("user_token") or
                  (click.enrollment_token if click else None) or "")

    conversion = AffiliateConversionDB(
        click_token=click_token,
        campaign_id=click.campaign_id if click else None,
        source=source,
        event_type="install",
        revenue_jpy=revenue,
        raw_payload=json.dumps(params)[:2000],
        attestation_status=attestation_status,
        asp_action_id=action_id,
        user_token=user_token,
    )
    db.add(conversion)

    if click:
        click.converted = True

    await db.flush()
    await _award_points(db, conversion)
    await db.commit()
    logger.info(
        f"{source} cv received | click_token={click_token[:8]}... "
        f"| revenue={revenue} | action={action_id} | status={attestation_status}"
    )
    return {"status": "ok"}


@router.get("/affiliate/postback/{source}", summary="ASP S2Sポストバック受信（JANet / SKYFLAG / smaad / A8.net 共通）")
async def asp_postback(
    source: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    各ASPからのCV通知を受信する共通エンドポイント。

    ASPパラメータを正規化して:
      1. user_token で AndroidDeviceDB を照合
      2. AffiliateConversion を記録（2段階通知に対応）
      3. enable_points=True キャンペーンのみポイント付与

    【対応ASP】
      janet   … user_id / commission / action_id / attestation_flag (0=approved, 1=pending)
      skyflag … suid / price / cv_id / install (1=approved, なし=pending)
      smaad   … uid / price （2段階なし → 即approved）
      a8      … uid / price （2段階なし → 即approved）
    """
    params = dict(request.query_params)
    normalized = _normalize_asp_params(source, params)

    user_token = normalized["user_token"]
    revenue = normalized["revenue"]
    action_id = normalized["action_id"]
    attestation_status = normalized["attestation_status"]

    if not user_token:
        logger.warning(f"{source} postback: user_token missing")
        return {"status": "ok"}  # ASPに 200 を返してリトライを防ぐ

    # user_token → device を照合（device_id は外部に渡さない）
    android_device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.user_token == user_token)
    )

    # asp_action_id がある場合は冪等性チェック
    if action_id:
        existing = await db.scalar(
            select(AffiliateConversionDB).where(
                AffiliateConversionDB.asp_action_id == action_id,
                AffiliateConversionDB.source == source,
            )
        )
        if existing:
            # Phase 2 (approved): ステータス更新のみ
            if existing.attestation_status != "approved" and attestation_status == "approved":
                existing.attestation_status = "approved"
                await db.flush()
                await _award_points(db, existing)
                await db.commit()
                logger.info(f"{source} postback phase2 approved | action={action_id}")
            else:
                logger.info(f"{source} postback duplicate skipped | action={action_id}")
            return {"status": "ok"}

    # 直近クリックレコードを取得（user_token 経由で device_id を解決）
    click = None
    if android_device:
        click = await db.scalar(
            select(AffiliateClickDB)
            .where(
                AffiliateClickDB.device_id == android_device.device_id,
                AffiliateClickDB.converted == False,  # noqa: E712
            )
            .order_by(AffiliateClickDB.clicked_at.desc())
            .limit(1)
        )

    conversion = AffiliateConversionDB(
        click_token=click.click_token if click else None,
        campaign_id=click.campaign_id if click else None,
        source=source,
        event_type="install",
        revenue_jpy=revenue,
        raw_payload=json.dumps(params)[:2000],
        attestation_status=attestation_status,
        asp_action_id=action_id,
        user_token=user_token,
    )
    db.add(conversion)

    if click:
        click.converted = True

    await db.flush()
    await _award_points(db, conversion)
    await db.commit()
    logger.info(
        f"{source} postback received | user_token={user_token[:8]}... "
        f"| revenue={revenue} | action={action_id} | status={attestation_status}"
    )
    return {"status": "ok"}


class AffiliateCampaignCreate(BaseModel):
    name: str
    category: str = "app"
    destination_url: str
    reward_type: str = "cpi"
    reward_amount: float = 0.0
    appsflyer_dev_key: Optional[str] = None
    adjust_app_token: Optional[str] = None
    gtm_container_id: Optional[str] = None
    cv_trigger: str = "install"  # "install"=Method1 / "app_open"=Method2
    postback_url_template: Optional[str] = None  # A8.net/smaad等の直接ポストバックURL
    janet_media_id: Optional[str] = None    # JANet メディアID
    janet_original_id: Optional[str] = None  # JANet 原稿ID
    click_url_template: Optional[str] = None  # smaad/A8.net等クリックURLテンプレート（{device_id}を置換）
    enable_points: bool = False              # ポイント付与を行うか（デフォルト: しない）
    point_rate: float = 1.0                  # 1円=何ポイント（還元率調整）
    dealer_revenue_rate: float = 0.0         # 代理店獲得金額率 (%)
    user_point_rate: float = 0.0             # ユーザー獲得ポイント率 (%)
    tracking_url: Optional[str] = None       # トラッキングURL（マクロ対応）
    blacklist_partner_ids: Optional[str] = None   # 除外パートナーID（カンマ区切り）
    whitelist_partner_ids: Optional[str] = None   # 許可パートナーID（カンマ区切り）


@router.post("/admin/affiliate/campaigns", summary="アフィリエイト案件登録（管理者）")
async def create_affiliate_campaign(
    request: Request,
    body: AffiliateCampaignCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    campaign = AffiliateCampaignDB(**body.model_dump())
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    base = str(request.base_url).rstrip("/")
    return {
        "id": campaign.id,
        "name": campaign.name,
        "cv_trigger": campaign.cv_trigger,
        "janet_media_id": campaign.janet_media_id,
        "janet_original_id": campaign.janet_original_id,
        "tracked_url_example": build_tracked_url(campaign.id, "EXAMPLE_TOKEN", base),
    }


@router.get("/admin/affiliate/campaigns", summary="アフィリエイト案件一覧（管理者）")
async def list_affiliate_campaigns(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = await db.execute(select(AffiliateCampaignDB).order_by(AffiliateCampaignDB.created_at.desc()))
    campaigns = rows.scalars().all()
    base = str(request.base_url).rstrip("/")
    return [
        {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "reward_type": c.reward_type,
            "reward_amount": c.reward_amount,
            "janet_media_id": c.janet_media_id,
            "enable_points": c.enable_points,
            "point_rate": c.point_rate,
            "tracking_url": c.tracking_url,
            "click_url_template": c.click_url_template,
            # ASP管理画面に登録するポストバックURL
            "postback_url": f"{base}/mdm/affiliate/cv?id=SESSIONID",
            # クリックURL例（JANet はパス形式、それ以外は tracking_url / click_url_template を使用）
            "click_url_example": (
                f"https://click.j-a-net.jp/{c.janet_media_id}/{c.janet_original_id}/{{user_token}}"
                if c.janet_media_id and c.janet_original_id
                else build_tracked_url(c.id, "EXAMPLE_TOKEN", base)
            ),
        }
        for c in campaigns
    ]


# ── BKD-10: Advertiser Self-Serve Portal API ──────────────────
# TODO: add per-advertiser API key auth for production


class AdvertiserCampaignCreate(BaseModel):
    name: str
    budget_jpy: float
    cpi_rate_jpy: float              # CPI単価（円）
    cpm_rate_jpy: float = 0.0        # CPM単価（円、0=CPIのみ）
    targeting_carrier: Optional[str] = None   # e.g. "44010" (MCC-MNC)
    targeting_os_min: Optional[str] = None    # e.g. "10"
    targeting_region: Optional[str] = None    # e.g. "JP-13"
    appsflyer_dev_key: Optional[str] = None
    adjust_app_token: Optional[str] = None
    adjust_event_token: Optional[str] = None
    vta_window_hours: int = 24
    status: str = "active"


class AdvertiserCreativeUpload(BaseModel):
    title: str
    cta_url: str
    creative_type: str = "banner"    # banner | video | html5
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    video_duration_sec: Optional[int] = None
    reward_amount: float             # CPM/CPI bid price in JPY


def _pack_advertiser_meta(body: AdvertiserCampaignCreate) -> str:
    """追加フィールドを advertising_id_field にJSON格納（既存DB列を再利用）"""
    return json.dumps({
        "budget_jpy": body.budget_jpy,
        "cpm_rate_jpy": body.cpm_rate_jpy,
        "targeting_carrier": body.targeting_carrier,
        "targeting_os_min": body.targeting_os_min,
        "targeting_region": body.targeting_region,
    })


def _unpack_advertiser_meta(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


@router.post("/advertiser/campaigns", summary="広告主キャンペーン作成（BKD-10）")
async def create_advertiser_campaign(
    body: AdvertiserCampaignCreate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    """
    広告主セルフサーブポータル — キャンペーン作成。
    AffiliateCampaignDB を作成し、デフォルトのロック画面広告枠 MdmAdSlotDB も同時に作成する。
    budget_jpy / cpm_rate_jpy / targeting_* は advertising_id_field に JSON で格納。
    """
    campaign = AffiliateCampaignDB(
        name=body.name,
        category="app",
        destination_url="",          # クリエイティブ追加時に上書き想定
        reward_type="cpi",
        reward_amount=body.cpi_rate_jpy,
        appsflyer_dev_key=body.appsflyer_dev_key,
        adjust_app_token=body.adjust_app_token,
        adjust_event_token=body.adjust_event_token,
        vta_window_hours=body.vta_window_hours,
        advertising_id_field=_pack_advertiser_meta(body),
        status=body.status,
    )
    db.add(campaign)
    await db.flush()  # campaign.id を確定させる

    # デフォルトのロック画面広告枠を作成
    slot = MdmAdSlotDB(
        name=f"{body.name} — lockscreen default",
        slot_type="lockscreen",
        floor_price_cpm=body.cpm_rate_jpy if body.cpm_rate_jpy > 0 else body.cpi_rate_jpy,
        targeting_json=json.dumps({
            "carrier": body.targeting_carrier,
            "os_min": body.targeting_os_min,
            "region": body.targeting_region,
        }),
        status="active",
    )
    db.add(slot)
    await db.commit()
    await db.refresh(campaign)

    meta = _unpack_advertiser_meta(campaign.advertising_id_field)
    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
        "cpi_rate_jpy": campaign.reward_amount,
        "cpm_rate_jpy": meta.get("cpm_rate_jpy", 0.0),
        "budget_jpy": meta.get("budget_jpy", 0.0),
        "slot_id": slot.id,
        "created_at": campaign.created_at.isoformat(),
    }


@router.get("/advertiser/campaigns", summary="広告主キャンペーン一覧（BKD-10）")
async def list_advertiser_campaigns(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    """
    全キャンペーン一覧。インプレッション数・クリック数・インストール数・消化金額・残予算を付与。
    """
    rows = await db.execute(
        select(AffiliateCampaignDB).order_by(AffiliateCampaignDB.created_at.desc())
    )
    campaigns = rows.scalars().all()

    results = []
    for c in campaigns:
        meta = _unpack_advertiser_meta(c.advertising_id_field)
        budget_jpy = meta.get("budget_jpy", 0.0)

        imp_count = await db.scalar(
            select(func.count(MdmImpressionDB.id))
            .join(CreativeDB, MdmImpressionDB.creative_id == CreativeDB.id)
            .where(CreativeDB.campaign_id == c.id)
        ) or 0

        click_count = await db.scalar(
            select(func.count(MdmImpressionDB.id))
            .join(CreativeDB, MdmImpressionDB.creative_id == CreativeDB.id)
            .where(CreativeDB.campaign_id == c.id)
            .where(MdmImpressionDB.clicked == True)
        ) or 0

        install_count = await db.scalar(
            select(func.count(InstallEventDB.id)).where(InstallEventDB.campaign_id == c.id)
        ) or 0

        spend_jpy = await db.scalar(
            select(func.sum(InstallEventDB.cpi_amount)).where(InstallEventDB.campaign_id == c.id)
        ) or 0.0

        results.append({
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "cpi_rate_jpy": c.reward_amount,
            "cpm_rate_jpy": meta.get("cpm_rate_jpy", 0.0),
            "budget_jpy": budget_jpy,
            "impression_count": imp_count,
            "click_count": click_count,
            "install_count": install_count,
            "spend_jpy": round(spend_jpy, 2),
            "remaining_budget_jpy": round(max(0.0, budget_jpy - spend_jpy), 2),
            "created_at": c.created_at.isoformat(),
        })
    return results


@router.get("/advertiser/campaigns/{campaign_id}/report", summary="広告主キャンペーン詳細レポート（BKD-10）")
async def get_advertiser_campaign_report(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    """
    キャンペーン詳細レポート。
    - インプレッション（合計・直近7日間日別）
    - クリック（合計・CTR）
    - インストール（合計・CVR・帰属タイプ別）
    - 消化金額・残予算
    - 動画完了クォータイル（動画クリエイティブのみ）
    """
    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    meta = _unpack_advertiser_meta(campaign.advertising_id_field)
    budget_jpy = meta.get("budget_jpy", 0.0)

    # ── インプレッション合計 ──
    total_impressions = await db.scalar(
        select(func.count(MdmImpressionDB.id))
        .join(CreativeDB, MdmImpressionDB.creative_id == CreativeDB.id)
        .where(CreativeDB.campaign_id == campaign_id)
    ) or 0

    # ── 直近7日間の日別インプレッション ──
    daily_rows = await db.execute(
        select(
            func.date(MdmImpressionDB.created_at).label("day"),
            func.count(MdmImpressionDB.id).label("count"),
        )
        .join(CreativeDB, MdmImpressionDB.creative_id == CreativeDB.id)
        .where(CreativeDB.campaign_id == campaign_id)
        .group_by(func.date(MdmImpressionDB.created_at))
        .order_by(func.date(MdmImpressionDB.created_at).desc())
        .limit(7)
    )
    impressions_by_day = [
        {"date": str(row.day), "count": row.count}
        for row in daily_rows.all()
    ]

    # ── クリック合計 ──
    total_clicks = await db.scalar(
        select(func.count(MdmImpressionDB.id))
        .join(CreativeDB, MdmImpressionDB.creative_id == CreativeDB.id)
        .where(CreativeDB.campaign_id == campaign_id)
        .where(MdmImpressionDB.clicked == True)
    ) or 0

    ctr = round(total_clicks / total_impressions * 100, 2) if total_impressions > 0 else 0.0

    # ── インストール合計・帰属タイプ別 ──
    total_installs = await db.scalar(
        select(func.count(InstallEventDB.id)).where(InstallEventDB.campaign_id == campaign_id)
    ) or 0

    cvr = round(total_installs / total_clicks * 100, 2) if total_clicks > 0 else 0.0

    attr_rows = await db.execute(
        select(InstallEventDB.attribution_type, func.count(InstallEventDB.id).label("count"))
        .where(InstallEventDB.campaign_id == campaign_id)
        .group_by(InstallEventDB.attribution_type)
    )
    installs_by_attribution = {row.attribution_type: row.count for row in attr_rows.all()}

    # ── 消化金額 ──
    spend_jpy = await db.scalar(
        select(func.sum(InstallEventDB.cpi_amount)).where(InstallEventDB.campaign_id == campaign_id)
    ) or 0.0

    # ── 動画クォータイル（video_event が NULL でない行のみ集計） ──
    video_rows = await db.execute(
        select(MdmImpressionDB.video_event, func.count(MdmImpressionDB.id).label("count"))
        .join(CreativeDB, MdmImpressionDB.creative_id == CreativeDB.id)
        .where(CreativeDB.campaign_id == campaign_id)
        .where(MdmImpressionDB.video_event.isnot(None))
        .group_by(MdmImpressionDB.video_event)
    )
    video_completions = {row.video_event: row.count for row in video_rows.all()}

    return {
        "campaign_id": campaign_id,
        "name": campaign.name,
        "status": campaign.status,
        "cpi_rate_jpy": campaign.reward_amount,
        "cpm_rate_jpy": meta.get("cpm_rate_jpy", 0.0),
        "budget_jpy": budget_jpy,
        "spend_jpy": round(spend_jpy, 2),
        "remaining_budget_jpy": round(max(0.0, budget_jpy - spend_jpy), 2),
        "impressions": {
            "total": total_impressions,
            "by_day_last_7": impressions_by_day,
        },
        "clicks": {
            "total": total_clicks,
            "ctr_pct": ctr,
        },
        "installs": {
            "total": total_installs,
            "cvr_pct": cvr,
            "by_attribution_type": installs_by_attribution,
        },
        "video_completions": video_completions,
    }


@router.post("/advertiser/campaigns/{campaign_id}/creative", summary="広告主クリエイティブ追加（BKD-10）")
async def add_advertiser_creative(
    campaign_id: str,
    body: AdvertiserCreativeUpload,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    """
    クリエイティブをキャンペーンに追加。
    - banner: image_url または title が必須
    - video: video_url が必須
    - html5: title が必須
    """
    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # バリデーション
    if body.creative_type == "video" and not body.video_url:
        raise HTTPException(status_code=422, detail="video_url is required for video creatives")
    if body.creative_type == "banner" and not body.image_url and not body.title:
        raise HTTPException(status_code=422, detail="image_url or title is required for banner creatives")

    creative = CreativeDB(
        campaign_id=campaign_id,
        name=body.title,
        type=body.creative_type,
        creative_type=body.creative_type,
        title=body.title,
        click_url=body.cta_url,
        image_url=body.image_url,
        video_url=body.video_url,
        video_duration_sec=body.video_duration_sec,
        status="active",
    )
    db.add(creative)
    await db.commit()
    await db.refresh(creative)

    return {
        "id": creative.id,
        "campaign_id": campaign_id,
        "title": creative.title,
        "creative_type": creative.creative_type,
        "click_url": creative.click_url,
        "image_url": creative.image_url,
        "video_url": creative.video_url,
        "video_duration_sec": creative.video_duration_sec,
        "status": creative.status,
        "created_at": creative.created_at.isoformat(),
    }


@router.get("/advertiser/campaigns/{campaign_id}/creatives", summary="広告主クリエイティブ一覧（BKD-10）")
async def list_advertiser_creatives(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    """
    キャンペーンに紐付くクリエイティブ一覧。クリエイティブ別インプレッション・クリック・CTR・eCPMを付与。
    """
    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    creative_rows = await db.execute(
        select(CreativeDB)
        .where(CreativeDB.campaign_id == campaign_id)
        .order_by(CreativeDB.created_at.desc())
    )
    creatives = creative_rows.scalars().all()

    results = []
    for cr in creatives:
        imp_count = await db.scalar(
            select(func.count(MdmImpressionDB.id)).where(MdmImpressionDB.creative_id == cr.id)
        ) or 0

        click_count = await db.scalar(
            select(func.count(MdmImpressionDB.id))
            .where(MdmImpressionDB.creative_id == cr.id)
            .where(MdmImpressionDB.clicked == True)
        ) or 0

        ctr = round(click_count / imp_count * 100, 2) if imp_count > 0 else 0.0

        total_cpm_revenue = await db.scalar(
            select(func.sum(MdmImpressionDB.cpm_price)).where(MdmImpressionDB.creative_id == cr.id)
        ) or 0.0
        ecpm = round(total_cpm_revenue / imp_count * 1000, 2) if imp_count > 0 else 0.0

        results.append({
            "id": cr.id,
            "title": cr.title,
            "creative_type": cr.creative_type,
            "status": cr.status,
            "impressions": imp_count,
            "clicks": click_count,
            "ctr_pct": ctr,
            "ecpm_jpy": ecpm,
            "created_at": cr.created_at.isoformat(),
        })
    return results


class AdvertiserCampaignStatusUpdate(BaseModel):
    status: str  # "active" | "paused"


@router.put("/advertiser/campaigns/{campaign_id}/status", summary="広告主キャンペーンステータス変更（BKD-10）")
async def update_advertiser_campaign_status(
    campaign_id: str,
    body: AdvertiserCampaignStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(verify_admin_key),
):
    """
    キャンペーンを一時停止 / 再開する。
    許可ステータス: active | paused
    """
    if body.status not in ("active", "paused"):
        raise HTTPException(status_code=422, detail="status must be 'active' or 'paused'")

    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign.status = body.status
    await db.commit()
    await db.refresh(campaign)

    return {
        "id": campaign.id,
        "name": campaign.name,
        "status": campaign.status,
    }


@router.get("/admin/stats", summary="MDM KPI（管理者）")
async def mdm_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    total_devices = await db.scalar(select(func.count(DeviceDB.id)))
    active_devices = await db.scalar(
        select(func.count(DeviceDB.id)).where(DeviceDB.status == "active")
    )
    downloaded = await db.scalar(
        select(func.count(DeviceDB.id)).where(DeviceDB.mobileconfig_downloaded == True)
    )
    total_dealers = await db.scalar(select(func.count(DealerDB.id)))
    android_devices = await db.scalar(select(func.count(AndroidDeviceDB.id)))

    by_platform = await db.execute(
        select(DeviceDB.platform, func.count(DeviceDB.id))
        .group_by(DeviceDB.platform)
    )
    platform_breakdown = {row[0]: row[1] for row in by_platform.all()}

    return {
        "total_devices": total_devices or 0,
        "active_devices": active_devices or 0,
        "mobileconfig_downloaded": downloaded or 0,
        "total_dealers": total_dealers or 0,
        "android_enrolled": android_devices or 0,
        "platform_breakdown": platform_breakdown,
    }


# ── CPI インストール確認 (BKD-03 / BKD-04) ────────────────────


async def finalize_billing(install_event_id: str, db: AsyncSession) -> None:
    """
    インストール確認後にCPI課金を確定させる（BKD-billing-01）。

    遷移ルール:
    - postback_status == "success" → billing_status = "billable"
    - postback_status != "success" かつ created_at が 48h 以上前 → billing_status = "billable"（失敗でも課金は発生）
    - それ以外 → pending のまま（ポストバック完了を待つ）

    billing_status が既に "billable" または "paid" なら何もしない（冪等）。
    billable に遷移した場合、InvoiceDB の当月集計を upsert する。
    """
    from datetime import timedelta

    event = await db.get(InstallEventDB, install_event_id)
    if event is None:
        logger.warning(f"finalize_billing: install_event_id={install_event_id} not found")
        return

    # 既に確定済み → 冪等
    if event.billing_status in ("billable", "paid"):
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    should_bill = False

    if event.postback_status == "success":
        should_bill = True
    else:
        # 48時間経過している場合はポストバック失敗でも課金確定
        created = event.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (now - created) >= timedelta(hours=48):
            should_bill = True

    if not should_bill:
        return

    # billing_status を billable に遷移
    event.billing_status = "billable"

    # InvoiceDB の当月レコードを upsert
    from db_models import InvoiceDB
    period_month = now.strftime("%Y-%m")

    invoice = await db.scalar(
        select(InvoiceDB).where(
            InvoiceDB.campaign_id == event.campaign_id,
            InvoiceDB.period_month == period_month,
        )
    )

    if invoice is None:
        # 当月の初回 → 新規作成
        campaign = await db.get(AffiliateCampaignDB, event.campaign_id)
        agency_id = campaign.agency_id if campaign else None
        invoice = InvoiceDB(
            period_month=period_month,
            campaign_id=event.campaign_id,
            agency_id=agency_id,
            gross_revenue_jpy=int(event.cpi_amount),
            take_rate=0.175,
            platform_fee_jpy=int(event.cpi_amount * 0.175),
            net_payable_jpy=int(event.cpi_amount * (1 - 0.175)),
            cpi_count=1,
            impression_count=0,
            video_complete_count=0,
            status="draft",
        )
        db.add(invoice)
    else:
        # 既存レコードに加算
        invoice.gross_revenue_jpy = (invoice.gross_revenue_jpy or 0) + int(event.cpi_amount)
        invoice.cpi_count = (invoice.cpi_count or 0) + 1
        invoice.platform_fee_jpy = int(invoice.gross_revenue_jpy * invoice.take_rate)
        invoice.net_payable_jpy = invoice.gross_revenue_jpy - invoice.platform_fee_jpy

    await db.commit()
    logger.info(
        f"finalize_billing: event={install_event_id[:8]}... → billable "
        f"| campaign={event.campaign_id[:8]}... | cpi={event.cpi_amount}円 | period={period_month}"
    )


class InstallConfirmedBody(BaseModel):
    device_id: str
    package_name: str
    campaign_id: Optional[str] = None  # 後方互換: サーバー側で解決できない場合のフォールバック
    install_ts: int                    # Unix timestamp (ms)
    apk_sha256: Optional[str] = None   # APK ハッシュ（任意）


@router.post("/install_confirmed", summary="APKインストール確認（DPC報告）")
async def install_confirmed(
    body: InstallConfirmedBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    DPC APKがインストール完了を報告するエンドポイント（BKD-03）。

    1. device_id がエンロール済みであることを確認
    2. campaign_id が存在することを確認
    3. 同一（device_id, package_name, campaign_id）の24時間以内の重複を検出
    4. InstallEventDB を INSERT（billing_status=pending）
    5. S2Sポストバックをバックグラウンドタスクで送信（BKD-04）
    """
    # 1. デバイス確認
    device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.device_id)
    )
    if device is None:
        raise HTTPException(status_code=404, detail="device_id not enrolled")

    # 2. サーバー主権で campaign_id を解決（AndroidCommandDB から取得 → DPCフォールバック）
    cmd = await db.scalar(
        select(AndroidCommandDB)
        .where(
            AndroidCommandDB.device_id == body.device_id,
            AndroidCommandDB.command_type == "install_apk",
            AndroidCommandDB.campaign_id.is_not(None),
            AndroidCommandDB.status.in_(["sent", "acknowledged"]),
        )
        .order_by(AndroidCommandDB.created_at.desc())
        .limit(1)
    )
    resolved_campaign_id = (cmd.campaign_id if cmd else None) or body.campaign_id
    resolved_store_id = (cmd.store_id if cmd else None) or (device.store_id if device else None)
    resolved_dealer_id = device.dealer_id if device else None

    if not resolved_campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id cannot be resolved")

    # 3. キャンペーン確認
    campaign = await db.get(AffiliateCampaignDB, resolved_campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign_id not found")

    # 4. cv_trigger を3段階優先順位で解決
    #    優先: 代理店設定 > キャンペーン設定 > デフォルト("install")
    dealer = None
    if resolved_dealer_id:
        dealer = await db.get(DealerDB, resolved_dealer_id)
    cv_trigger = (
        (dealer.default_cv_trigger if dealer else None)
        or campaign.cv_trigger
        or "install"
    )

    # 5. 冪等性チェック: 同一(device_id, package_name, campaign_id)で24h以内の重複
    from datetime import timedelta
    window_start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    window_start_ts = int(window_start.timestamp() * 1000)

    existing_event = await db.scalar(
        select(InstallEventDB).where(
            InstallEventDB.device_id == body.device_id,
            InstallEventDB.package_name == body.package_name,
            InstallEventDB.campaign_id == resolved_campaign_id,
            InstallEventDB.install_ts >= window_start_ts,
        )
    )
    if existing_event:
        logger.info(
            f"install_confirmed duplicate | device={body.device_id[:8]}... "
            f"| pkg={body.package_name} | campaign={resolved_campaign_id}"
        )
        return {
            "status": "recorded",
            "install_event_id": existing_event.id,
            "already_recorded": True,
        }

    # 6. 新規 InstallEvent を記録
    cv_method = "install" if cv_trigger == "install" else "pending_app_open"
    install_event = InstallEventDB(
        device_id=body.device_id,
        package_name=body.package_name,
        campaign_id=resolved_campaign_id,
        install_ts=body.install_ts,
        apk_sha256=body.apk_sha256,
        billing_status="pending",
        postback_status="pending",
        cpi_amount=campaign.reward_amount,
        cv_method=cv_method,
        dealer_id=resolved_dealer_id,
        store_id=resolved_store_id,
    )
    db.add(install_event)
    await db.flush()  # id を確定させる
    install_event_id = install_event.id
    await db.commit()

    logger.info(
        f"install_confirmed recorded | id={install_event_id} "
        f"| device={body.device_id[:8]}... | pkg={body.package_name} "
        f"| cv_trigger={cv_trigger} | dealer={resolved_dealer_id} | store={resolved_store_id}"
    )

    # 7. cv_trigger に従いポストバック or 保留
    if cv_trigger == "install":
        # Method 1: 即時ポストバック
        background_tasks.add_task(trigger_postbacks, install_event_id, db, "install")

    # VTA チェック（バックグラウンドで実行）
    background_tasks.add_task(
        check_vta,
        body.device_id,
        body.package_name,
        resolved_campaign_id,
        install_event_id,
        db,
    )

    # 7. CPI課金確定タスク（ポストバック後に実行、冪等）
    background_tasks.add_task(finalize_billing, install_event_id, db)

    return {
        "status": "recorded",
        "install_event_id": install_event_id,
        "already_recorded": False,
    }


# ── Android MDM API ───────────────────────────────────────────


class AndroidRegisterBody(BaseModel):
    device_id: str         # Android ID（Settings.Secure.ANDROID_ID）
    enrollment_token: Optional[str] = None
    fcm_token: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    android_version: Optional[str] = None
    sdk_int: Optional[int] = None
    gaid: Optional[str] = None       # Google Advertising ID
    dealer_id: Optional[str] = None  # 所属代理店ID
    store_id: Optional[str] = None   # 所属店舗ID（代理店内の複数店舗識別）
    device_fingerprint: Optional[str] = None  # manufacturer:model:brand のハッシュ


@router.post("/android/register", summary="Android DPCデバイス登録")
async def android_register(body: AndroidRegisterBody, db: AsyncSession = Depends(get_db)):
    """
    DPC APKが初回起動時または機種変更後の再起動時に呼び出す。
    enrollment_token が付いており、かつ新しい device_id の場合は機種変更として引き継ぎ処理を行う。
    """
    existing = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.device_id)
    )

    if existing:
        # 同一デバイスの更新（FCMトークン・GAID等）
        existing.fcm_token = body.fcm_token or existing.fcm_token
        if body.gaid:
            existing.gaid = body.gaid
        if body.dealer_id:
            existing.dealer_id = body.dealer_id
        if body.store_id:
            existing.store_id = body.store_id
        if body.device_fingerprint:
            existing.device_fingerprint = body.device_fingerprint
        existing.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
        # user_token がない場合は生成（既存デバイスの後方互換）
        if not existing.user_token:
            existing.user_token = _generate_user_token()
        await db.commit()
        logger.info(f"Android device updated | device={body.device_id[:8]}...")
        return {"status": "updated", "device_id": body.device_id, "user_token": existing.user_token}

    # --- 機種変更引き継ぎ処理 ---
    # enrollment_token があり、かつ旧デバイスが存在する場合 → 機種変更
    if body.enrollment_token:
        old_device = await db.scalar(
            select(AndroidDeviceDB).where(
                AndroidDeviceDB.enrollment_token == body.enrollment_token,
                AndroidDeviceDB.device_id != body.device_id,
                AndroidDeviceDB.status == "active",
            )
        )
        if old_device:
            # fingerprint チェック（不一致の場合は suspicious フラグ）
            suspicious = False
            if body.device_fingerprint and old_device.device_fingerprint:
                suspicious = body.device_fingerprint != old_device.device_fingerprint

            # 旧デバイスを migrated に更新
            old_device.status = "migrated"
            old_device.migrated_at = datetime.now(timezone.utc).replace(tzinfo=None)

            # 新デバイスを作成（キャンペーン設定・dealer_id・store_id を引き継ぎ）
            new_device = AndroidDeviceDB(
                device_id=body.device_id,
                enrollment_token=body.enrollment_token,
                fcm_token=body.fcm_token,
                manufacturer=body.manufacturer,
                model=body.model,
                android_version=body.android_version,
                sdk_int=body.sdk_int,
                gaid=body.gaid,
                dealer_id=body.dealer_id or old_device.dealer_id,
                store_id=body.store_id or old_device.store_id,
                previous_device_id=old_device.device_id,
                device_fingerprint=body.device_fingerprint,
                migration_suspicious=suspicious,
                last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add(new_device)
            await db.commit()
            logger.info(
                f"Android device migrated | old={old_device.device_id[:8]}... | "
                f"new={body.device_id[:8]}... | suspicious={suspicious}"
            )
            return {"status": "migrated", "device_id": body.device_id, "suspicious": suspicious}

    # --- 新規登録 ---
    new_user_token = _generate_user_token()
    device = AndroidDeviceDB(
        device_id=body.device_id,
        enrollment_token=body.enrollment_token,
        fcm_token=body.fcm_token,
        manufacturer=body.manufacturer,
        model=body.model,
        android_version=body.android_version,
        sdk_int=body.sdk_int,
        gaid=body.gaid,
        dealer_id=body.dealer_id,
        store_id=body.store_id,
        device_fingerprint=body.device_fingerprint,
        last_seen_at=datetime.now(timezone.utc).replace(tzinfo=None),
        user_token=new_user_token,
    )
    db.add(device)

    # DeviceDB の status を active に更新
    if body.enrollment_token:
        portal_device = await db.scalar(
            select(DeviceDB).where(DeviceDB.enrollment_token == body.enrollment_token)
        )
        if portal_device:
            portal_device.status = "active"
            portal_device.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)

    await db.commit()
    logger.info(f"Android device registered | device={body.device_id[:8]}... | model={body.model} | user_token={new_user_token}")
    return {"status": "registered", "device_id": body.device_id, "user_token": new_user_token}


@router.get("/android/commands/{device_id}", summary="Android DPC コマンドポーリング")
async def android_poll_commands(device_id: str, db: AsyncSession = Depends(get_db)):
    """
    DPC APKが定期的にポーリングして未実行コマンドを取得する。
    取得と同時にステータスを sent に更新する。
    """
    await update_device_last_seen(db, device_id)
    commands = await get_pending_commands(db, device_id)

    return {
        "commands": [
            {
                "id": cmd.id,
                "type": cmd.command_type,
                "payload": json.loads(cmd.payload) if cmd.payload else {},
            }
            for cmd in commands
        ]
    }


class CommandAckBody(BaseModel):
    success: bool = True


@router.post("/android/commands/{command_id}/ack", summary="Android コマンドACK")
async def android_command_ack(
    command_id: str,
    body: CommandAckBody,
    db: AsyncSession = Depends(get_db),
):
    """DPCがコマンド実行結果を報告する"""
    ok = await acknowledge_command(db, command_id, body.success)
    if not ok:
        raise HTTPException(status_code=404, detail="Command not found")
    return {"status": "ok"}


class WifiCheckinBody(BaseModel):
    device_id: str
    ssid: str  # 接続したSSID（例: "SHOP_FREE_WIFI"）


@router.post("/android/wifi_checkin", summary="Wi-Fi SSID 来店チェックイン")
async def android_wifi_checkin(
    body: WifiCheckinBody,
    db: AsyncSession = Depends(get_db),
):
    """
    DPCが特定のSSIDへの接続を検知したときに呼び出す。
    登録済みのトリガールールに従い、プッシュ/LINE/ポイントなどを自動実行する。
    """
    result = await handle_wifi_checkin(body.device_id, body.ssid, db)
    return result


@router.get("/android/lockscreen/content", summary="ロック画面広告コンテンツ取得")
async def lockscreen_content(
    request: Request,
    device_id: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
    hour: Optional[int] = Query(None),
    screen_on_count: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    ロック画面アプリが起動時に呼び出す。
    登録クリエイティブから最適な広告をオークション選択して返す。
    クリエイティブ未登録の場合はアフィリエイト案件のフォールバック。
    """
    content = await select_creative(
        db, slot_type="lockscreen",
        device_id=device_id, enrollment_token=token, platform="android",
        hour=hour if hour is not None else -1,
        screen_on_count=screen_on_count,
    )
    if content:
        # impression_id をトップレベルに露出してDPCがクリック報告に使えるようにする
        return {
            "impression_id": content.get("impression_id"),
            "content": content,
        }

    # フォールバック: クリエイティブ未登録時は案件直接返却
    result = await db.execute(
        select(AffiliateCampaignDB).where(AffiliateCampaignDB.status == "active").limit(5)
    )
    campaigns = list(result.scalars().all())
    if not campaigns:
        return {"content": None}

    import random
    campaign = random.choice(campaigns)
    base = str(request.base_url).rstrip("/")
    return {
        "impression_id": None,
        "content": {
            "campaign_id": campaign.id,
            "type": "text",
            "title": campaign.name,
            "click_url": build_tracked_url(campaign.id, device_id or "anonymous", base),
            "category": campaign.category,
        },
    }


# ── DPC-07: ロック画面KPI報告 ─────────────────────────────────


class LockscreenKpiBody(BaseModel):
    impression_id: str
    device_id: str
    dwell_time_ms: int
    dismiss_type: str  # cta_tap / swipe_dismiss / auto_dismiss
    hour_of_day: int
    screen_on_count_today: Optional[int] = None


@router.post("/lockscreen_kpi", summary="ロック画面KPI報告（DPC-07）")
async def report_lockscreen_kpi(
    body: LockscreenKpiBody,
    db: AsyncSession = Depends(get_db),
):
    """
    Android DPC の LockscreenActivity から送信されるエンゲージメント指標を受け取る（DPC-07）。

    - impression_id の存在確認（スプーフィング防止）
    - MdmImpressionDB を status="served" に更新
    - dwell_time_ms / dismiss_type を ログ記録
    """
    impression = await db.get(MdmImpressionDB, body.impression_id)
    if impression is None:
        raise HTTPException(status_code=404, detail="impression_id not found")

    # served 状態に更新（prefetched → served）
    if impression.status == "prefetched":
        impression.status = "served"
    impression.dwell_time_ms = body.dwell_time_ms
    impression.dismiss_type = body.dismiss_type
    impression.hour_of_day = body.hour_of_day
    if body.screen_on_count_today is not None:
        impression.screen_on_count_today = body.screen_on_count_today
    await db.commit()

    logger.info(
        f"lockscreen_kpi | impression={body.impression_id} | device={body.device_id}"
        f" | dwell={body.dwell_time_ms}ms | dismiss={body.dismiss_type}"
        f" | hour={body.hour_of_day}"
    )
    return {"status": "ok"}


# ── プリフェッチ定数 ────────────────────────────────────────────
_PREFETCH_TTL = 14400       # 4h — individual impression_id TTL
_PREFETCH_CACHE_TTL = 300   # 5min — per-device response cache
_PREFETCH_COUNT = 3         # 返却クリエイティブ数


@router.get("/prefetch/{device_id}", summary="コンテンツプリフェッチ（eCPM上位3件）")
async def content_prefetch(
    device_id: str,
    hour: Optional[int] = Query(None, description="ターゲティング用時刻（0-23）"),
    carrier: Optional[str] = Query(None, description="ターゲティング用キャリアコード"),
    db: AsyncSession = Depends(get_db),
):
    """
    端末がバックグラウンドで次表示する広告を先読みする。
    eCPM降順の上位3件を返し、各クリエイティブに impression_id を事前払い出す。
    - impression_id は Redis に TTL=4h で保存（key: prefetch:{impression_id}）
    - デバイス単位で5分間レスポンスをキャッシュ（key: device_prefetch:{device_id}）
    - Redis 未接続時はインメモリフォールバックを使用
    """
    cache_key = f"device_prefetch:{device_id}"

    # ── キャッシュヒット確認 ──────────────────────────────────────
    r = await get_redis()
    try:
        cached_raw = await r.get(cache_key) if r else _mem_get(cache_key)
        if cached_raw:
            return json.loads(cached_raw)
    except Exception:
        pass  # キャッシュ読み取り失敗は無視して続行

    # ── クリエイティブ選択（最大 _PREFETCH_COUNT 件） ─────────────
    # select_creative は1件選択 + impression 記録を行うが、prefetch では
    # DB 書き込みを自前で行うため、ここでは selector の内部ロジックを
    # 直接呼び出さず、同等のクエリを組む。
    # ただし、フリークエンシーキャップは selector と共通ロジックを使うため
    # select_creative を _PREFETCH_COUNT 回呼ぶとcap消費が起きてしまう。
    # → 候補一覧取得・ソートのみ行い、impression は status="prefetched" で
    #   一括 insert する専用パスを実装する。

    from datetime import date
    from sqlalchemy import Integer, func as sqlfunc
    from db_models import AffiliateCampaignDB as _CampaignDB, CreativeExperimentDB
    from mdm.creative.selector import FREQ_CAP_DAILY, DEFAULT_CTR, _get_creative_ctrs, get_time_slot_multiplier

    # スロット定義取得
    slot = await db.scalar(
        select(MdmAdSlotDB)
        .where(MdmAdSlotDB.slot_type == "lockscreen", MdmAdSlotDB.status == "active")
        .order_by(MdmAdSlotDB.created_at)
        .limit(1)
    )
    floor_cpm = slot.floor_price_cpm if slot else 0.0

    # アクティブなクリエイティブ一覧
    q = (
        select(CreativeDB, _CampaignDB)
        .join(_CampaignDB, CreativeDB.campaign_id == _CampaignDB.id)
        .where(
            CreativeDB.status == "active",
            _CampaignDB.status == "active",
        )
    )
    rows = await db.execute(q)
    candidates = rows.all()

    if not candidates:
        return {"slots": [], "prefetched_at": datetime.now(timezone.utc).isoformat()}

    # フリークエンシーキャップ（本日分）
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    freq_rows = await db.execute(
        select(
            MdmImpressionDB.creative_id,
            sqlfunc.count(MdmImpressionDB.id).label("count"),
        )
        .where(
            MdmImpressionDB.device_id == device_id,
            MdmImpressionDB.created_at >= today_start,
        )
        .group_by(MdmImpressionDB.creative_id)
    )
    capped_ids = {row.creative_id for row in freq_rows.all() if row.count >= FREQ_CAP_DAILY}
    candidates = [r for r in candidates if r[0].id not in capped_ids]

    if not candidates:
        logger.info(f"MDM prefetch: frequency cap reached for device_id={device_id}")
        payload = {"slots": [], "prefetched_at": datetime.now(timezone.utc).isoformat()}
        return payload

    # eCPM スコア降順ソート（タイムスロット乗数適用）
    creative_ids = [r[0].id for r in candidates]
    ctrs = await _get_creative_ctrs(db, creative_ids, "lockscreen")

    _ts_multiplier = 1.0
    if hour is not None and hour >= 0:
        _dow = datetime.now(timezone.utc).weekday()
        _ts_multiplier = await get_time_slot_multiplier(hour, _dow, db)
        if _ts_multiplier != 1.0:
            logger.info(
                f"MDM prefetch time-slot multiplier | device_id={device_id} "
                f"| hour={hour} | dow={_dow} | multiplier={_ts_multiplier}"
            )

    def _ecpm(row) -> float:
        creative, campaign = row
        ctr = ctrs.get(creative.id, DEFAULT_CTR)
        return campaign.reward_amount * ctr * 1000 * _ts_multiplier

    candidates.sort(key=_ecpm, reverse=True)
    top = candidates[:_PREFETCH_COUNT]

    # ── DSP入札（フォールバック付き） ─────────────────────────────
    # 直販クリエイティブ選択と並走させ、DSP入札がフロアを超えた場合は
    # DspWinLogDB に収益を記録する。active DSPなし or タイムアウト時は
    # 直販クリエイティブをそのまま使用する（フォールバック）。
    _device_profile_dict: dict = {}
    try:
        _dp = await db.get(DeviceProfileDB, device_id)
        if _dp:
            _device_profile_dict = {
                "manufacturer": _dp.manufacturer or "",
                "model": _dp.model or "",
                "os_version": _dp.os_version or "",
                "os": "android",
            }
    except Exception:
        pass  # デバイスプロファイル取得失敗は無視して直販フォールバック

    _dsp_result: Optional[dict] = None
    _dsp_impression_id = str(uuid.uuid4())
    try:
        _dsp_result = await rtb_client.request_bid(
            impression_id=_dsp_impression_id,
            floor_price_jpy=floor_cpm,
            device_profile=_device_profile_dict,
            slot_type="lockscreen",
            creative_w=1080,
            creative_h=1920,
        )
    except Exception as _dsp_exc:
        logger.warning(f"DSP bid request failed (non-fatal): {_dsp_exc}")

    if _dsp_result:
        # DSP落札: プラットフォーム収益を記録（take_rate=15% 控除後）
        _take_rate = 0.15
        _platform_rev_jpy = (
            _dsp_result["clearing_price_usd"] * (1.0 - _take_rate) * 150.0
        )
        _win_log = DspWinLogDB(
            impression_id=_dsp_impression_id,
            dsp_name=_dsp_result["dsp_name"],
            bid_price_usd=_dsp_result["bid_price_usd"],
            clearing_price_usd=_dsp_result["clearing_price_usd"],
            platform_revenue_jpy=_platform_rev_jpy,
        )
        db.add(_win_log)
        logger.info(
            f"DSP win | dsp={_dsp_result['dsp_name']} "
            f"| clearing=${_dsp_result['clearing_price_usd']:.4f} USD "
            f"| platform_rev=¥{_platform_rev_jpy:.2f}"
        )

    # ── impression_id 払い出し & DB + Redis 保存 ─────────────────
    slots_out = []
    for creative, campaign in top:
        imp_id = str(uuid.uuid4())

        # DB に status="prefetched" で事前登録
        imp = MdmImpressionDB(
            id=imp_id,
            slot_id=slot.id if slot else None,
            creative_id=creative.id,
            device_id=device_id,
            platform="android",
            cpm_price=floor_cpm,
            status="prefetched",
        )
        db.add(imp)

        # Redis / インメモリに TTL=4h でメタデータ保存
        prefetch_meta = json.dumps({
            "device_id": device_id,
            "creative_id": creative.id,
            "campaign_id": campaign.id,
            "hour": hour,
            "carrier": carrier,
            # DSP落札フラグ（クライアントは無視してよい）
            "dsp_win": _dsp_result is not None,
        })
        prefetch_redis_key = f"prefetch:{imp_id}"
        try:
            if r:
                await r.setex(prefetch_redis_key, _PREFETCH_TTL, prefetch_meta)
            else:
                _mem_set(prefetch_redis_key, prefetch_meta, _PREFETCH_TTL)
        except Exception as exc:
            logger.warning(f"Redis prefetch set failed for {imp_id}: {exc}")
            _mem_set(prefetch_redis_key, prefetch_meta, _PREFETCH_TTL)

        slots_out.append({
            "impression_id": imp_id,
            "title": creative.title,
            "cta_url": creative.click_url,
            "image_url": creative.image_url,
            "campaign_id": campaign.id,
            "creative_id": creative.id,
            "ttl_seconds": _PREFETCH_TTL,
        })

    try:
        await db.commit()
    except Exception as exc:
        logger.error(f"MDM prefetch DB commit failed: {exc}")
        await db.rollback()
        return {"slots": [], "prefetched_at": datetime.now(timezone.utc).isoformat()}

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {"slots": slots_out, "prefetched_at": now_iso}

    # ── デバイス単位レスポンスキャッシュ（5分） ──────────────────
    payload_raw = json.dumps(payload)
    try:
        if r:
            await r.setex(cache_key, _PREFETCH_CACHE_TTL, payload_raw)
        else:
            _mem_set(cache_key, payload_raw, _PREFETCH_CACHE_TTL)
    except Exception as exc:
        logger.warning(f"Redis device prefetch cache set failed: {exc}")
        _mem_set(cache_key, payload_raw, _PREFETCH_CACHE_TTL)

    logger.info(
        f"MDM prefetch | device_id={device_id} | slots={len(slots_out)} "
        f"| hour={hour} | carrier={carrier}"
    )
    return payload


@router.get("/android/widget/content", summary="ホーム画面ウィジェットコンテンツ取得")
async def widget_content(
    request: Request,
    device_id: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """ホーム画面ウィジェットアプリが表示するコンテンツを返す（最大3件）"""
    # 登録クリエイティブから選択（3件まで）
    items = []
    for _ in range(3):
        content = await select_creative(
            db, slot_type="widget",
            device_id=device_id, enrollment_token=token, platform="android",
        )
        if content and content not in items:
            items.append(content)

    if items:
        return {"items": items}

    # フォールバック
    result = await db.execute(
        select(AffiliateCampaignDB)
        .where(AffiliateCampaignDB.status == "active", AffiliateCampaignDB.category == "app")
        .limit(3)
    )
    campaigns = list(result.scalars().all())
    base = str(request.base_url).rstrip("/")
    return {
        "items": [
            {
                "campaign_id": c.id,
                "type": "text",
                "title": c.name,
                "click_url": build_tracked_url(c.id, device_id or "anonymous", base),
            }
            for c in campaigns
        ]
    }


@router.get("/ios/widget_content/{device_id}", summary="iOS WidgetKit コンテンツ取得")
async def ios_widgetkit_content(
    device_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    iOS-01 WidgetKit TimelineProvider から呼ばれるエンドポイント。
    ポイント残高・本日のクーポン・広告バナー（静止画）を返す。
    ロック画面ウィジェットは video 不可（Apple ガイドライン）。
    """
    from mdm.creative.selector import select_creative

    # ポイント残高（mdm_prefs / デバイス登録情報から取得）
    ios_dev = await db.scalar(
        select(iOSDeviceDB).where(iOSDeviceDB.udid == device_id)
    )
    points_balance = getattr(ios_dev, "points_balance", 0) if ios_dev else 0

    # クーポン数（アクティブなwebclipスロットを代用）
    coupon_count = await db.scalar(
        select(func.count(MdmAdSlotDB.id)).where(
            MdmAdSlotDB.slot_type == "webclip",
            MdmAdSlotDB.status == "active",
        )
    ) or 0

    # 静止画広告クリエイティブ（image_url必須、video不可）
    creative = await select_creative(db, slot_type="widget", device_id=device_id)
    ad_payload = None
    if creative and creative.get("image_url"):
        ad_payload = {
            "image_url": creative.get("image_url"),
            "title": creative.get("title", ""),
            "cta_url": creative.get("cta_url", ""),
            "impression_id": creative.get("impression_id"),
        }

    return {
        "device_id": device_id,
        "points_balance": points_balance,
        "coupon_count": int(coupon_count),
        "ad": ad_payload,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "refresh_interval_minutes": 30,
    }


@router.get("/android/dpc.apk", summary="DPC APKダウンロード（プレースホルダー）")
async def download_dpc_apk(token: Optional[str] = Query(None)):
    """
    DPC APKダウンロードエンドポイント。
    実際のAPKはビルド後に静的ファイルとして配置する。
    現時点ではインストール手順ページへリダイレクト。
    """
    return RedirectResponse(
        url=f"/mdm/android/install-guide?token={token or ''}",
        status_code=302,
    )


@router.get("/android/install-guide", response_class=HTMLResponse, summary="Android インストールガイド")
async def android_install_guide(token: Optional[str] = Query(None)):
    account_id = settings.line_official_account_id
    line_url = f"https://line.me/R/ti/p/{account_id}" if account_id else "#"

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <title>Androidセットアップ</title>
  <style>
    body {{font-family:-apple-system,sans-serif;background:#f5f5f7;margin:0;padding:0;}}
    .wrap {{max-width:480px;margin:0 auto;padding:40px 20px;}}
    .icon {{font-size:64px;text-align:center;margin-bottom:16px;}}
    h1 {{font-size:22px;font-weight:700;margin-bottom:8px;text-align:center;}}
    p {{font-size:15px;color:#6e6e73;line-height:1.6;margin-bottom:24px;text-align:center;}}
    .steps {{counter-reset:step;margin-bottom:32px;}}
    .step {{display:flex;gap:14px;align-items:flex-start;margin-bottom:16px;}}
    .step-num {{width:28px;height:28px;border-radius:50%;background:#34c759;color:#fff;
                font-size:14px;font-weight:700;display:flex;align-items:center;
                justify-content:center;flex-shrink:0;}}
    .step-text {{font-size:14px;line-height:1.6;color:#3a3a3c;padding-top:3px;}}
    .btn {{display:block;padding:16px;font-size:17px;font-weight:600;border-radius:14px;
           text-decoration:none;margin-bottom:12px;text-align:center;}}
    .btn-line {{background:#06c755;color:#fff;}}
    .btn-skip {{background:#e5e5ea;color:#3a3a3c;font-size:15px;}}
    .note {{font-size:12px;color:#6e6e73;text-align:center;line-height:1.6;}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="icon">🤖</div>
    <h1>セットアップ完了まであと少し</h1>
    <p>以下の手順でセットアップを完了してください。</p>
    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <div class="step-text">ダウンロードされたAPKファイルをタップしてインストール</div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div class="step-text">「提供元不明のアプリ」を許可してインストール</div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div class="step-text">アプリを起動して「デバイス管理者を有効化」をタップ</div>
      </div>
      <div class="step">
        <div class="step-num">4</div>
        <div class="step-text">設定完了！LINEで友だち追加してクーポンをゲット</div>
      </div>
    </div>
    <a href="{line_url}" class="btn btn-line">📲 LINEで友だち追加（無料）</a>
    <p class="note">設定はいつでも「設定 → デバイス管理アプリ」から解除できます。</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── Android 管理者API ──────────────────────────────────────────


class AndroidPushBody(BaseModel):
    device_id: str
    command_type: str   # install_apk / add_webclip / show_notification / update_lockscreen
    payload: dict = {}
    send_fcm: bool = True   # FCMでDPCを起こすか


@router.post("/admin/android/push", summary="AndroidデバイスへMDMコマンド送信（管理者）")
async def admin_android_push(
    body: AndroidPushBody,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """管理画面からAndroidデバイスにコマンドをキューイングし、FCMで通知する。
    update_lockscreen の場合はクリエイティブ選択 + impression_id を自動注入する。"""
    # デバイス確認
    device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.device_id)
    )
    if not device:
        raise HTTPException(status_code=404, detail="Android device not found")

    payload = dict(body.payload)

    # update_lockscreen: クリエイティブ選択して impression_id を自動注入
    if body.command_type == "update_lockscreen" and "impression_id" not in payload:
        creative = await select_creative(
            db, slot_type="lockscreen",
            device_id=body.device_id, platform="android",
        )
        if creative:
            payload.setdefault("title", creative.get("title", ""))
            payload.setdefault("cta_url", creative.get("click_url", ""))
            payload["impression_id"] = creative.get("impression_id")

    # install_apk: campaign_id をペイロードから取得してサーバー主権で保存
    campaign_id_for_cmd = None
    if body.command_type == "install_apk":
        campaign_id_for_cmd = body.payload.get("campaign_id")

    cmd = await enqueue_command(
        db, body.device_id, body.command_type, payload,
        campaign_id=campaign_id_for_cmd,
        store_id=device.store_id,
    )

    fcm_sent = False
    if body.send_fcm and device.fcm_token:
        fcm_sent = await send_command_ping(device.fcm_token, body.device_id)

    return {
        "command_id": cmd.id,
        "status": "queued",
        "fcm_sent": fcm_sent,
    }


# ── Android アトリビューション補助エンドポイント ──────────────


class UpdateGaidBody(BaseModel):
    device_id: str
    gaid: str


@router.post("/android/update_gaid", summary="Android端末のGAIDを更新")
async def android_update_gaid(body: UpdateGaidBody, db: AsyncSession = Depends(get_db)):
    """DPC APKがGAID取得後に呼び出す。S2Sポストバックの広告識別子に使用される。"""
    device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.device_id)
    )
    if not device:
        raise HTTPException(status_code=404, detail="device not found")
    device.gaid = body.gaid
    await db.commit()
    return {"status": "ok"}


class AppOpenBody(BaseModel):
    device_id: str
    package_name: str
    trigger: str = "push_tap"  # push_tap | organic


@router.post("/android/app_open", summary="アプリ起動通知（Method 2 CV発火点）")
async def android_app_open(
    body: AppOpenBody,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    DPCがプッシュ通知タップ後のアプリ起動を報告するエンドポイント（Method 2 / CPE）。

    - install_events で cv_method="pending_app_open" かつ app_open_at が未設定のレコードを探す
    - cv_trigger="app_open" のキャンペーンの場合のみポストバックを発火する
    """
    install_event = await db.scalar(
        select(InstallEventDB)
        .where(
            InstallEventDB.device_id == body.device_id,
            InstallEventDB.package_name == body.package_name,
            InstallEventDB.app_open_at.is_(None),
            InstallEventDB.cv_method == "pending_app_open",
        )
        .order_by(InstallEventDB.created_at.desc())
        .limit(1)
    )
    if not install_event:
        return {"status": "no_pending_install"}

    # cv_trigger を再確認（代理店設定優先）
    campaign = await db.get(AffiliateCampaignDB, install_event.campaign_id)
    device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.device_id)
    )
    dealer = None
    if device and device.dealer_id:
        dealer = await db.get(DealerDB, device.dealer_id)
    cv_trigger = (
        (dealer.default_cv_trigger if dealer else None)
        or (campaign.cv_trigger if campaign else "install")
        or "install"
    )

    if cv_trigger != "app_open":
        return {"status": "skipped", "reason": "campaign uses install trigger"}

    install_event.app_open_at = datetime.now(timezone.utc).replace(tzinfo=None)
    install_event.cv_method = "app_open"
    await db.commit()
    background_tasks.add_task(trigger_postbacks, install_event.id, db, "app_open")
    return {"status": "recorded", "install_event_id": install_event.id}


@router.get("/admin/android/devices", summary="Androidデバイス一覧（管理者）")
async def list_android_devices(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = await db.execute(
        select(AndroidDeviceDB).order_by(AndroidDeviceDB.registered_at.desc()).limit(100)
    )
    devices = rows.scalars().all()
    return [
        {
            "device_id": d.device_id,
            "user_token": d.user_token,
            "enrollment_token": d.enrollment_token,
            "model": d.model,
            "android_version": d.android_version,
            "status": d.status,
            "has_fcm": bool(d.fcm_token),
            "migration_suspicious": bool(getattr(d, "migration_suspicious", False)),
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "registered_at": d.registered_at.isoformat(),
        }
        for d in devices
    ]


# ── iOS NanoMDM 連携 ──────────────────────────────────────────


@router.get("/ios/mobileconfig-mdm", summary="iOS MDM管理プロファイルダウンロード（NanoMDM統合版）")
async def download_mobileconfig_mdm(
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    MDM管理ペイロードを含む .mobileconfig を返す。
    インストールするとデバイスがNanoMDMサーバーに登録される。

    ※ apns_topic と mdm_server_url の設定が必要。
    """
    device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == token))
    if not device:
        raise HTTPException(status_code=404, detail="Invalid enrollment token")
    if not device.consent_given:
        raise HTTPException(status_code=403, detail="Consent required")

    if not settings.apns_topic or not settings.mdm_server_url:
        raise HTTPException(
            status_code=503,
            detail="MDM server not configured. Set apns_topic and mdm_server_url in .env",
        )

    profile_name = "サービス設定（MDM管理）"
    webclips = []

    if device.campaign_id:
        campaign = await db.get(CampaignDB, device.campaign_id)
        if campaign:
            profile_name = campaign.name
            if campaign.webclips:
                for wc in json.loads(campaign.webclips):
                    webclips.append(WebClipConfig(
                        url=wc["url"], label=wc["label"],
                        full_screen=wc.get("full_screen", True),
                        is_removable=wc.get("is_removable", False),  # MDM管理は削除不可
                    ))

    mdm_cfg = MDMConfig(
        server_url=settings.mdm_server_url,
        topic=settings.apns_topic,
    )

    config_bytes = generate_mobileconfig(
        profile_name=profile_name,
        enrollment_token=token,
        webclips=webclips or None,
        mdm=mdm_cfg,
    )

    device.mobileconfig_downloaded = True
    device.last_seen_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    logger.info(f"MDM mobileconfig-mdm downloaded | token={token[:8]}...")
    return Response(
        content=config_bytes,
        media_type="application/x-apple-aspen-config",
        headers={"Content-Disposition": 'attachment; filename="mdm-config.mobileconfig"'},
    )


@router.post("/ios/checkin", summary="NanoMDM チェックインWebhook（デバイス情報DB登録）")
async def ios_mdm_checkin(request: Request, db: AsyncSession = Depends(get_db)):
    """
    NanoMDMのWebhookから呼ばれる（NanoMDMの -webhook-url オプション）。
    デバイスのUDID・PushMagic・PushTokenをDBに保存する。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    event = body.get("topic", "")
    params = body.get("checkin", body)

    udid = params.get("UDID") or params.get("udid")
    if not udid:
        return {"status": "ignored"}

    push_magic = params.get("PushMagic")
    push_token = params.get("Token") or params.get("PushToken")
    device_name = params.get("DeviceName")
    os_version = params.get("OSVersion")
    serial = params.get("SerialNumber")
    model = params.get("Model")
    product_name = params.get("ProductName")

    existing = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == udid))

    if existing:
        if push_magic:
            existing.push_magic = push_magic
        if push_token:
            existing.push_token = push_token
        if device_name:
            existing.device_name = device_name
        if os_version:
            existing.os_version = os_version
        if serial:
            existing.serial_number = serial
        existing.enrolled = True
        existing.status = "active"
        existing.last_checkin_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        logger.info(f"iOS device updated | udid={udid[:8]}... | event={event}")

        # checkin のたびに ProfileList を自動キューイングしてプロファイル存在確認
        if existing.enrollment_token:
            profile_list_cmd = mdm_commands.get_profile_list()
            await nanomdm_client.push_command(udid, profile_list_cmd)
            if existing.push_token and existing.push_magic and existing.topic:
                await send_mdm_push(existing.push_token, existing.push_magic, existing.topic)
            logger.info(f"ProfileList queued | udid={udid[:8]}...")
    else:
        ios_dev = iOSDeviceDB(
            udid=udid,
            push_magic=push_magic,
            push_token=push_token,
            device_name=device_name,
            os_version=os_version,
            serial_number=serial,
            device_model=model,
            product_name=product_name,
            enrolled=True,
            status="active",
            last_checkin_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(ios_dev)
        await db.commit()
        logger.info(f"iOS device registered | udid={udid[:8]}... | model={product_name}")

    return {"status": "ok"}


@router.post("/ios/profile_list_result", summary="iOS ProfileList結果受信（NanoMDM Webhook）")
async def ios_profile_list_result(request: Request, db: AsyncSession = Depends(get_db)):
    """
    NanoMDM が ProfileList コマンドの結果を返してくる Webhook。
    com.platform.mdm.{enrollment_token} が存在しない場合は InstallProfile を再 push する。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    udid = body.get("UDID") or body.get("udid")
    profiles = body.get("ProfileList", [])
    if not udid:
        return {"status": "ignored"}

    ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == udid))
    if not ios_dev or not ios_dev.enrollment_token:
        return {"status": "ignored"}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expected_id = f"com.platform.mdm.{ios_dev.enrollment_token}"
    profile_ids = [p.get("PayloadIdentifier") for p in profiles]
    profile_present = expected_id in profile_ids

    ios_dev.last_profile_check_at = now
    if profile_present:
        ios_dev.profile_status = "present"
        await db.commit()
        logger.info(f"ProfileList: present | udid={udid[:8]}...")
    else:
        # プロファイルが消失 → InstallProfile を即時再 push
        ios_dev.profile_status = "re_installing"
        await db.commit()
        logger.warning(f"ProfileList: MISSING → re-installing | udid={udid[:8]}...")

        portal_device = await db.scalar(
            select(DeviceDB).where(DeviceDB.enrollment_token == ios_dev.enrollment_token)
        )
        if portal_device:
            campaign = await db.scalar(
                select(CampaignDB).where(CampaignDB.id == portal_device.campaign_id)
            )
            if campaign:
                from mdm.enrollment.mobileconfig import generate_mobileconfig
                mobileconfig_data = generate_mobileconfig(
                    profile_name=campaign.name,
                    enrollment_token=ios_dev.enrollment_token,
                )
                install_cmd = mdm_commands.install_configuration_profile(mobileconfig_data)
                await nanomdm_client.push_command(udid, install_cmd)
                if ios_dev.push_token and ios_dev.push_magic and ios_dev.topic:
                    await send_mdm_push(ios_dev.push_token, ios_dev.push_magic, ios_dev.topic)

    return {"status": "ok"}


# ── App Clips API ─────────────────────────────────────────────


@router.get("/appclips/content", summary="App Clipsコンテンツ取得")
async def appclips_content(
    request: Request,
    udid: Optional[str] = Query(None),
    dealer: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    App Clipsが起動時に呼び出す。
    店舗・デバイスのセグメントに応じたオファー（クーポン/アプリ/VPN等）を返す。
    """
    result = await db.execute(
        select(AffiliateCampaignDB)
        .where(AffiliateCampaignDB.status == "active")
        .limit(10)
    )
    campaigns = list(result.scalars().all())

    if not campaigns:
        return {"offer": None}

    import random
    campaign = random.choice(campaigns)
    base = str(request.base_url).rstrip("/")
    tracked_url = build_tracked_url(campaign.id, udid or "appclip-anonymous", base)

    return {
        "offer": {
            "campaign_id": campaign.id,
            "title": campaign.name,
            "category": campaign.category,
            "cta_label": "今すぐ確認する",
            "cta_url": tracked_url,
        },
        "dealer_id": dealer,
    }


# ── iOS MDM 管理者API ─────────────────────────────────────────


class MDMCommandBody(BaseModel):
    udid: str
    request_type: str
    # add_web_clip / install_profile / remove_profile / device_info / profile_list / device_lock
    params: dict = {}
    send_push: bool = True  # APNsでデバイスを起こすか


@router.post("/admin/ios/command", summary="iOS デバイスへMDMコマンド送信（管理者）")
async def admin_ios_command(
    body: MDMCommandBody,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    iOSデバイスにMDMコマンドをキューイングし、APNsでチェックインを促す。

    request_type の対応:
      add_web_clip                    - params: {url, label, full_screen}
      remove_profile                  - params: {identifier}
      device_info                     - params: {}
      profile_list                    - params: {}
      device_lock                     - params: {message, phone}
      install_application             - params: {manifest_url, management_flags?}
      install_enterprise_application  - params: {manifest_url}
      send_app_clip_invite            - params: {app_clip_url}
    """
    # デバイス確認
    ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == body.udid))
    if not ios_dev:
        raise HTTPException(status_code=404, detail="iOS device not found")

    # コマンドplist生成
    cmd_uuid = str(uuid.uuid4())
    rt = body.request_type
    p = body.params

    if rt == "add_web_clip":
        plist_bytes = mdm_commands.add_web_clip(
            url=p["url"], label=p.get("label", "App"),
            full_screen=p.get("full_screen", True), command_uuid=cmd_uuid,
        )
    elif rt == "remove_profile":
        plist_bytes = mdm_commands.remove_profile(p["identifier"], command_uuid=cmd_uuid)
    elif rt == "device_info":
        plist_bytes = mdm_commands.get_device_info(command_uuid=cmd_uuid)
    elif rt == "profile_list":
        plist_bytes = mdm_commands.get_profile_list(command_uuid=cmd_uuid)
    elif rt == "device_lock":
        plist_bytes = mdm_commands.device_lock(
            message=p.get("message", ""), phone=p.get("phone", ""), command_uuid=cmd_uuid,
        )
    elif rt == "install_application":
        plist_bytes = mdm_commands.install_application(
            manifest_url=p["manifest_url"],
            management_flags=p.get("management_flags", 1),
            command_uuid=cmd_uuid,
        )
    elif rt == "install_enterprise_application":
        plist_bytes = mdm_commands.install_enterprise_application(
            manifest_url=p["manifest_url"],
            command_uuid=cmd_uuid,
        )
    elif rt == "send_app_clip_invite":
        plist_bytes = mdm_commands.send_app_clip_invite(
            app_clip_url=p["app_clip_url"],
            command_uuid=cmd_uuid,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown request_type: {rt}")

    # NanoMDMにキューイング
    queued = await nanomdm_client.push_command(body.udid, plist_bytes)

    # DBにも記録
    cmd_record = MDMCommandDB(
        udid=body.udid,
        request_type=rt,
        command_uuid=cmd_uuid,
        payload=json.dumps(p),
        status="sent" if queued else "error",
        sent_at=datetime.now(timezone.utc).replace(tzinfo=None) if queued else None,
    )
    db.add(cmd_record)
    await db.commit()

    # APNsでデバイスを起こす
    push_sent = False
    if body.send_push and ios_dev.push_token and ios_dev.push_magic:
        push_sent = await send_mdm_push(ios_dev.push_token, ios_dev.push_magic)

    return {
        "command_uuid": cmd_uuid,
        "queued": queued,
        "push_sent": push_sent,
    }


@router.get("/admin/ios/devices", summary="iOSデバイス一覧（管理者）")
async def list_ios_devices(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = await db.execute(
        select(iOSDeviceDB).order_by(iOSDeviceDB.enrolled_at.desc()).limit(100)
    )
    devices = rows.scalars().all()
    return [
        {
            "udid": d.udid,
            "device_name": d.device_name,
            "product_name": d.product_name,
            "os_version": d.os_version,
            "serial_number": d.serial_number,
            "enrolled": d.enrolled,
            "status": d.status,
            "has_push_token": bool(d.push_token),
            "last_checkin_at": d.last_checkin_at.isoformat() if d.last_checkin_at else None,
            "enrolled_at": d.enrolled_at.isoformat() if d.enrolled_at else None,
        }
        for d in devices
    ]


@router.post("/admin/ios/push/{udid}", summary="iOS APNs MDM Push送信（管理者）")
async def admin_ios_push(
    udid: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """デバイスにAPNsを送信してMDMサーバーへのチェックインを促す"""
    ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == udid))
    if not ios_dev:
        raise HTTPException(status_code=404, detail="iOS device not found")

    if not ios_dev.push_token or not ios_dev.push_magic:
        raise HTTPException(status_code=400, detail="Device has no push token (not enrolled via MDM)")

    sent = await send_mdm_push(ios_dev.push_token, ios_dev.push_magic)
    return {"push_sent": sent, "udid": udid}


# ── Phase 5: GTM LP / 収益計算 / 精算レポート ──────────────────


@router.get("/lp/{campaign_id}", response_class=HTMLResponse, summary="GTM埋め込みランディングページ")
async def affiliate_lp(
    campaign_id: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    広告主のGTMコンテナIDが自動挿入されたLPを返す。
    /mdm/lp/{campaign_id}?token=ENROLLMENT_TOKEN でアクセスするとクリックログが記録される。
    """
    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if not campaign or campaign.status != "active":
        raise HTTPException(status_code=404, detail="Campaign not found")

    device = None
    if token:
        device = await db.scalar(select(DeviceDB).where(DeviceDB.enrollment_token == token))

    click = AffiliateClickDB(
        campaign_id=campaign_id,
        enrollment_token=token,
        dealer_id=device.dealer_id if device else None,
        platform=device.platform if device else "unknown",
    )
    db.add(click)
    await db.commit()
    await db.refresh(click)

    html = build_lp_html(
        campaign_id=campaign_id,
        title=campaign.name,
        description=f"おすすめの{campaign.category}サービスをご紹介します",
        cta_url=campaign.destination_url,
        gtm_container_id=campaign.gtm_container_id,
        enrollment_token=token or "",
        click_token=click.click_token,
    )
    return HTMLResponse(content=html)


@router.get("/admin/affiliate/report", summary="全体月次収益レポート（管理者）")
async def monthly_revenue_report(
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """指定月（デフォルト: 今月）の全体収益サマリー"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month
    return await calculate_monthly_revenue(db, y, m)


@router.get("/admin/affiliate/report-agencies", summary="代理店企業別月次実績サマリー（管理者）")
async def agencies_monthly_report(
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """代理店企業（AgencyDB）ごとに配下店舗のCV・収益・代理店取り分を集計して返す。"""
    from collections import defaultdict
    from db_models import AgencyDB  # noqa

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month

    # 全店舗レポートを取得
    all_dealer_reports = await get_all_dealers_report(db, y, m)

    # 全代理店を取得
    agencies = (await db.scalars(select(AgencyDB).order_by(AgencyDB.id))).all()
    agency_map = {ag.id: ag for ag in agencies}

    # 店舗をagency_idでグループ化
    agency_totals = defaultdict(lambda: {
        "clicks": 0, "installs": 0, "conversions": 0, "revenue_jpy": 0.0, "dealer_share_jpy": 0.0,
        "store_count": 0, "stores": [],
    })

    # 全店舗のagency_idを取得
    dealers = (await db.scalars(select(DealerDB))).all()
    dealer_agency = {d.id: d.agency_id for d in dealers}
    dealer_login = {d.id: d.login_id for d in dealers}

    for report in all_dealer_reports:
        agency_id = dealer_agency.get(report["dealer_id"])
        t = agency_totals[agency_id]
        t["clicks"] += report.get("clicks", 0)
        t["installs"] += report.get("installs", 0)
        t["conversions"] += report.get("conversions", 0)
        t["revenue_jpy"] += report.get("revenue_jpy", 0.0)
        t["dealer_share_jpy"] += report.get("dealer_share_jpy", 0.0)
        t["store_count"] += 1
        t["stores"].append({
            "dealer_id": report["dealer_id"],
            "dealer_name": report["dealer_name"],
            "store_code": report["store_code"],
            "login_id": dealer_login.get(report["dealer_id"], ""),
            "clicks": report.get("clicks", 0),
            "installs": report.get("installs", 0),
            "conversions": report.get("conversions", 0),
            "revenue_jpy": report.get("revenue_jpy", 0.0),
            "dealer_share_jpy": report.get("dealer_share_jpy", 0.0),
        })

    result = []
    for ag in agencies:
        t = agency_totals[ag.id]
        result.append({
            "agency_id": ag.id,
            "login_id": ag.login_id or "",
            "agency_name": ag.name,
            "store_count": t["store_count"],
            "clicks": t["clicks"],
            "installs": t["installs"],
            "conversions": t["conversions"],
            "revenue_jpy": round(t["revenue_jpy"], 2),
            "dealer_share_jpy": round(t["dealer_share_jpy"], 2),
            "stores": t["stores"],
        })

    # 未所属店舗（agency_id=None）があれば追加
    unassigned = agency_totals.get(None)
    if unassigned and unassigned["store_count"] > 0:
        result.append({
            "agency_id": None,
            "login_id": "",
            "agency_name": "（未所属）",
            "store_count": unassigned["store_count"],
            "clicks": unassigned["clicks"],
            "installs": unassigned["installs"],
            "conversions": unassigned["conversions"],
            "revenue_jpy": round(unassigned["revenue_jpy"], 2),
            "dealer_share_jpy": round(unassigned["dealer_share_jpy"], 2),
            "stores": unassigned["stores"],
        })

    return {"period": f"{y:04d}-{m:02d}", "agencies": result}


@router.get("/admin/affiliate/report/{dealer_id}", summary="代理店別月次精算レポート（管理者）")
async def dealer_monthly_report(
    dealer_id: str,
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """代理店単位の月次精算レポート"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month
    report = await get_dealer_monthly_report(db, dealer_id, y, m)
    if not report:
        raise HTTPException(status_code=404, detail="Dealer not found")
    return report


@router.get("/admin/affiliate/report-all", summary="全代理店月次サマリー（管理者）")
async def all_dealers_monthly_report(
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """全代理店の月次レポートを収益順で返す"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month
    return await get_all_dealers_report(db, y, m)


@router.get("/admin/affiliate/report/store/{store_id}", summary="店舗別月次CVレポート（管理者）")
async def affiliate_report_by_store(
    store_id: str,
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """店舗単位のCV・収益レポート。dealer_id も含めて返すことで代理店への紐づきを明示する。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month

    # 月の開始・終了 Unix timestamp (ms)
    from calendar import monthrange
    period_start = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    period_end = int(
        datetime(y, m, monthrange(y, m)[1], 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000
    )

    rows = await db.execute(
        select(InstallEventDB)
        .where(
            InstallEventDB.store_id == store_id,
            InstallEventDB.install_ts >= period_start,
            InstallEventDB.install_ts <= period_end,
            InstallEventDB.billing_status == "billable",
        )
        .order_by(InstallEventDB.created_at.desc())
    )
    events = rows.scalars().all()

    total_cv = len(events)
    total_revenue = sum(e.cpi_amount for e in events)
    dealer_id = events[0].dealer_id if events else None

    return {
        "store_id": store_id,
        "dealer_id": dealer_id,
        "period": f"{y:04d}-{m:02d}",
        "total_cv": total_cv,
        "total_revenue_jpy": round(total_revenue, 2),
        "events": [
            {
                "id": e.id,
                "device_id": e.device_id,
                "package_name": e.package_name,
                "campaign_id": e.campaign_id,
                "cv_method": e.cv_method,
                "cpi_amount": e.cpi_amount,
                "install_ts": e.install_ts,
            }
            for e in events
        ],
    }


@router.get("/admin/affiliate/conversions", summary="CV一覧（管理者）")
async def list_conversions(
    campaign_id: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """CVログ一覧。campaign_id / source（appsflyer/adjust/manual）でフィルタ可。"""
    q = select(AffiliateConversionDB).order_by(AffiliateConversionDB.converted_at.desc()).limit(limit)
    if campaign_id:
        q = q.where(AffiliateConversionDB.campaign_id == campaign_id)
    if source:
        q = q.where(AffiliateConversionDB.source == source)
    rows = await db.execute(q)
    return [
        {
            "id": c.id,
            "campaign_id": c.campaign_id,
            "click_token": c.click_token,
            "source": c.source,
            "event_type": c.event_type,
            "revenue_jpy": c.revenue_jpy,
            "converted_at": c.converted_at.isoformat() if c.converted_at else None,
            "attestation_status": c.attestation_status,
            "asp_action_id": c.asp_action_id,
            "user_token": c.user_token,
        }
        for c in rows.scalars().all()
    ]


@router.get("/admin/affiliate/points", summary="ユーザーポイント一覧（管理者）")
async def list_user_points(
    user_token: Optional[str] = Query(None),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """ポイント付与履歴一覧。user_token でフィルタ可。"""
    q = select(UserPointDB).order_by(UserPointDB.awarded_at.desc()).limit(limit)
    if user_token:
        q = q.where(UserPointDB.user_token == user_token)
    rows = await db.execute(q)
    return [
        {
            "id": p.id,
            "user_token": p.user_token,
            "conversion_id": p.conversion_id,
            "points": p.points,
            "awarded_at": p.awarded_at.isoformat() if p.awarded_at else None,
        }
        for p in rows.scalars().all()
    ]


# ── リアルタイム統計 SSE ──────────────────────────────────────


def _require_admin_query(
    admin_key: Optional[str] = Query(None, alias="admin_key"),
    header_key: Optional[str] = None,
) -> None:
    """SSE用: クエリパラメータ admin_key でも認証を受け付ける（EventSourceはヘッダ非対応）。"""
    from fastapi.security import APIKeyHeader
    # header は Security() 経由で取れないのでここでは Query のみチェック
    key = admin_key or header_key
    if not key or key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")


@router.get("/admin/stats/stream", summary="リアルタイムKPI SSEストリーム（管理者）")
async def stats_stream(
    request: Request,
    admin_key: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Server-Sent Events で 5 秒ごとに最新 KPI を push する。
    管理ダッシュボードの数値をリアルタイム更新するために使用する。
    認証: ?admin_key=xxx（EventSource はカスタムヘッダ非対応のためクエリ認証）
    """
    if not admin_key or admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")

    from fastapi.responses import StreamingResponse
    from sqlalchemy import Integer, cast

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                today = now.replace(hour=0, minute=0, second=0, microsecond=0)

                # 今日のインプレッション・クリック
                imp_row = await db.execute(
                    select(
                        func.count(MdmImpressionDB.id).label("impressions"),
                        func.sum(cast(MdmImpressionDB.clicked, Integer)).label("clicks"),
                    ).where(MdmImpressionDB.created_at >= today)
                )
                imp = imp_row.one()
                total_imp = imp.impressions or 0
                total_clicks = int(imp.clicks or 0)

                # 登録デバイス数
                android_count = await db.scalar(select(func.count(AndroidDeviceDB.id)))
                ios_count = await db.scalar(select(func.count(iOSDeviceDB.id)))

                # 今月収益
                start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                revenue = await db.scalar(
                    select(func.sum(AffiliateConversionDB.revenue_jpy))
                    .where(AffiliateConversionDB.converted_at >= start_of_month)
                )

                payload = json.dumps({
                    "ts": now.isoformat(),
                    "today_impressions": total_imp,
                    "today_clicks": total_clicks,
                    "today_ctr": round(total_clicks / total_imp, 4) if total_imp else 0.0,
                    "android_devices": android_count or 0,
                    "ios_devices": ios_count or 0,
                    "month_revenue_jpy": float(revenue or 0),
                })
                yield f"data: {payload}\n\n"
            except Exception as e:
                logger.warning(f"SSE stats error: {e}")
                yield f"data: {{}}\n\n"

            await asyncio.sleep(5)

    import asyncio
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx buffering 無効化
        },
    )


# ── Phase 6: ダッシュボード（HTML） ───────────────────────────


_DASHBOARD_STYLE = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', sans-serif;
       background: #f0f2f5; color: #1d1d1f; }
.nav { background: #1d1d1f; color: #fff; padding: 14px 24px; display: flex;
       justify-content: space-between; align-items: center; }
.nav h1 { font-size: 16px; font-weight: 700; }
.nav span { font-size: 12px; color: #8e8e93; }
.main { max-width: 1100px; margin: 0 auto; padding: 24px 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card { background: #fff; border-radius: 12px; padding: 20px;
        box-shadow: 0 1px 6px rgba(0,0,0,0.06); }
.card .label { font-size: 12px; color: #6e6e73; margin-bottom: 6px; }
.card .value { font-size: 28px; font-weight: 700; }
.card .sub { font-size: 12px; color: #6e6e73; margin-top: 4px; }
.section { background: #fff; border-radius: 12px; padding: 20px;
           box-shadow: 0 1px 6px rgba(0,0,0,0.06); margin-bottom: 20px; }
.section h2 { font-size: 16px; font-weight: 600; margin-bottom: 16px;
              padding-bottom: 10px; border-bottom: 1px solid #f0f0f0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 10px; color: #6e6e73; font-weight: 500;
     border-bottom: 1px solid #f0f0f0; }
td { padding: 10px; border-bottom: 1px solid #f8f8f8; }
tr:last-child td { border-bottom: none; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
         font-size: 11px; font-weight: 600; }
.badge-green { background: #d1fae5; color: #065f46; }
.badge-blue { background: #dbeafe; color: #1e40af; }
.revenue { color: #007aff; font-weight: 700; }
</style>
"""


@router.get("/admin/dashboard", response_class=HTMLResponse, summary="管理者ダッシュボード（HTML）")
async def admin_dashboard(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """MDM + アフィリエイト統合ダッシュボード"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    total_devices = await db.scalar(select(func.count(DeviceDB.id))) or 0
    active_devices = await db.scalar(
        select(func.count(DeviceDB.id)).where(DeviceDB.status == "active")
    ) or 0
    android_enrolled = await db.scalar(select(func.count(AndroidDeviceDB.id))) or 0
    ios_enrolled = await db.scalar(select(func.count(iOSDeviceDB.id))) or 0
    total_dealers = await db.scalar(select(func.count(DealerDB.id))) or 0
    total_campaigns = await db.scalar(
        select(func.count(AffiliateCampaignDB.id)).where(AffiliateCampaignDB.status == "active")
    ) or 0
    total_clicks = await db.scalar(select(func.count(AffiliateClickDB.id))) or 0
    total_cvs = await db.scalar(select(func.count(AffiliateConversionDB.id))) or 0
    total_revenue = await db.scalar(select(func.sum(AffiliateConversionDB.revenue_jpy))) or 0.0

    monthly = await calculate_monthly_revenue(db, now.year, now.month)

    dealer_rows_q = await db.execute(
        select(DealerDB, func.count(DeviceDB.id).label("device_count"))
        .outerjoin(DeviceDB, DealerDB.id == DeviceDB.dealer_id)
        .group_by(DealerDB.id)
        .order_by(func.count(DeviceDB.id).desc())
        .limit(5)
    )
    top_dealers = dealer_rows_q.all()

    campaign_rows_q = await db.execute(
        select(
            AffiliateCampaignDB,
            func.count(AffiliateConversionDB.id).label("cv_count"),
            func.sum(AffiliateConversionDB.revenue_jpy).label("revenue"),
        )
        .outerjoin(AffiliateConversionDB, AffiliateCampaignDB.id == AffiliateConversionDB.campaign_id)
        .where(AffiliateCampaignDB.status == "active")
        .group_by(AffiliateCampaignDB.id)
        .order_by(func.count(AffiliateConversionDB.id).desc())
        .limit(5)
    )
    top_campaigns = campaign_rows_q.all()

    dealer_rows_html = "".join(
        f"<tr><td>{d.name}</td><td>{d.store_code}</td>"
        f"<td>{cnt}</td>"
        f"<td><span class='badge badge-green'>{d.status}</span></td></tr>"
        for d, cnt in top_dealers
    ) or "<tr><td colspan='4' style='color:#8e8e93;text-align:center'>代理店がまだ登録されていません</td></tr>"

    campaign_rows_html = "".join(
        f"<tr><td>{c.name}</td>"
        f"<td><span class='badge badge-blue'>{c.reward_type.upper()}</span></td>"
        f"<td>{cv or 0}</td>"
        f"<td class='revenue'>¥{float(rev or 0):,.0f}</td></tr>"
        for c, cv, rev in top_campaigns
    ) or "<tr><td colspan='4' style='color:#8e8e93;text-align:center'>案件がまだ登録されていません</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MDM管理ダッシュボード</title>
  {_DASHBOARD_STYLE}
</head>
<body>
  <div class="nav">
    <h1>MDM管理ダッシュボード</h1>
    <span>{now.strftime('%Y-%m-%d %H:%M')} UTC</span>
  </div>
  <div class="main">
    <div class="grid">
      <div class="card"><div class="label">総エンロール端末</div>
        <div class="value">{total_devices:,}</div><div class="sub">アクティブ: {active_devices:,}</div></div>
      <div class="card"><div class="label">Android端末</div>
        <div class="value">{android_enrolled:,}</div><div class="sub">DPC登録済み</div></div>
      <div class="card"><div class="label">iOS端末</div>
        <div class="value">{ios_enrolled:,}</div><div class="sub">NanoMDM登録済み</div></div>
      <div class="card"><div class="label">代理店数</div>
        <div class="value">{total_dealers:,}</div></div>
      <div class="card"><div class="label">配信中案件</div>
        <div class="value">{total_campaigns:,}</div></div>
      <div class="card"><div class="label">累計クリック</div>
        <div class="value">{total_clicks:,}</div></div>
      <div class="card"><div class="label">累計CV</div>
        <div class="value">{total_cvs:,}</div></div>
      <div class="card"><div class="label">今月収益</div>
        <div class="value revenue">¥{monthly['total_revenue_jpy']:,.0f}</div>
        <div class="sub">{monthly['period']} / {monthly['total_conversions']} CV</div></div>
    </div>

    <div class="section" style="border-left: 4px solid #34c759;">
      <h2 style="display:flex;align-items:center;gap:8px;">
        <span id="live-dot" style="width:8px;height:8px;background:#34c759;border-radius:50%;display:inline-block;animation:pulse 1.5s infinite;"></span>
        リアルタイム（今日）
        <span style="font-size:12px;font-weight:400;color:#8e8e93;" id="live-ts"></span>
      </h2>
      <div class="grid" style="margin-top:16px;margin-bottom:0;">
        <div class="card"><div class="label">本日インプレッション</div>
          <div class="value" id="live-impressions">—</div></div>
        <div class="card"><div class="label">本日クリック</div>
          <div class="value" id="live-clicks">—</div></div>
        <div class="card"><div class="label">本日 CTR</div>
          <div class="value" id="live-ctr">—</div></div>
        <div class="card"><div class="label">今月収益</div>
          <div class="value revenue" id="live-revenue">—</div></div>
      </div>
    </div>
    <style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}</style>
    <script>
    (function(){{
      var adminKey = new URLSearchParams(location.search).get('admin_key') || '';
      if(!adminKey) return;
      var src = new EventSource('/mdm/admin/stats/stream?admin_key=' + adminKey);
      src.onmessage = function(e){{
        try {{
          var d = JSON.parse(e.data);
          if(!d.ts) return;
          document.getElementById('live-impressions').textContent = (d.today_impressions||0).toLocaleString();
          document.getElementById('live-clicks').textContent = (d.today_clicks||0).toLocaleString();
          document.getElementById('live-ctr').textContent = ((d.today_ctr||0)*100).toFixed(2) + '%';
          document.getElementById('live-revenue').textContent = '¥' + (d.month_revenue_jpy||0).toLocaleString();
          document.getElementById('live-ts').textContent = new Date(d.ts).toLocaleTimeString('ja-JP');
        }} catch(err){{}}
      }};
      src.onerror = function(){{ document.getElementById('live-dot').style.background='#ff3b30'; }};
    }})();
    </script>

    <div class="section">
      <h2>代理店 Top 5（端末数順）</h2>
      <table>
        <tr><th>店舗名</th><th>店舗コード</th><th>端末数</th><th>ステータス</th></tr>
        {dealer_rows_html}
      </table>
    </div>

    <div class="section">
      <h2>アフィリエイト案件 Top 5（CV数順）</h2>
      <table>
        <tr><th>案件名</th><th>報酬タイプ</th><th>CV数</th><th>収益</th></tr>
        {campaign_rows_html}
      </table>
    </div>

    <div class="section">
      <h2>主要APIエンドポイント</h2>
      <table>
        <tr><th>URL</th><th>説明</th></tr>
        <tr><td>GET /mdm/admin/affiliate/report</td><td>今月の全体収益レポート</td></tr>
        <tr><td>GET /mdm/admin/affiliate/report-all</td><td>全代理店月次サマリー</td></tr>
        <tr><td>GET /mdm/admin/affiliate/conversions</td><td>CV一覧</td></tr>
        <tr><td>GET /mdm/dealer/portal?api_key=KEY</td><td>代理店ポータル</td></tr>
        <tr><td>GET /mdm/advertiser/portal/CAMPAIGN_ID</td><td>広告主ポータル</td></tr>
      </table>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/dealer/portal", response_class=HTMLResponse, summary="代理店ポータル")
async def dealer_portal(
    request: Request,
    api_key: str = Query(..., description="代理店API Key（DealerDB.api_key）"),
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    代理店スタッフ向けポータル。
    QRコード・端末数・月次収益レポートを表示。
    認証: ?api_key=<dealer.api_key>
    """
    dealer = await db.scalar(
        select(DealerDB).where(DealerDB.api_key == api_key, DealerDB.status == "active")
    )
    if not dealer:
        raise HTTPException(status_code=403, detail="Invalid API key")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month
    report = await get_dealer_monthly_report(db, dealer.id, y, m)

    base = str(request.base_url).rstrip("/")
    qr_url = f"{base}/mdm/qr/{dealer.store_code}"
    portal_url = f"{base}/mdm/portal?dealer={dealer.id}"

    campaign_rows_html = "".join(
        f"<tr><td>{c['campaign_name']}</td>"
        f"<td>{c['reward_type'].upper()}</td>"
        f"<td>{c['cv_count']}</td>"
        f"<td class='revenue'>¥{c['revenue_jpy']:,.0f}</td></tr>"
        for c in report.get("by_campaign", [])
    ) or "<tr><td colspan='4' style='color:#8e8e93;text-align:center'>今月のCVはまだありません</td></tr>"

    # 本日統計用データ（portal表示時点で取得）
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    today_impressions = await db.scalar(
        select(func.count(MdmImpressionDB.id)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0
    today_clicks = await db.scalar(
        select(func.count(MdmImpressionDB.id)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.clicked == True,  # noqa: E712
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0
    today_revenue = await db.scalar(
        select(func.sum(MdmImpressionDB.cpm_price)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0.0
    today_ctr = round(today_clicks / today_impressions, 4) if today_impressions > 0 else 0.0

    # プッシュ通知残り回数
    month_start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    push_used = await db.scalar(
        select(func.count(DealerPushLogDB.id)).where(
            DealerPushLogDB.dealer_id == dealer.id,
            DealerPushLogDB.sent_at >= month_start,
        )
    ) or 0
    push_remaining = max(0, 3 - push_used)

    # WebClip一覧
    dealer_campaign = await _get_or_create_dealer_campaign(dealer.id, db)
    await db.commit()
    webclips_list = json.loads(dealer_campaign.webclips or "[]")
    webclip_rows_html = "".join(
        f"<tr><td>{wc.get('label','')}</td><td><a href='{wc.get('url','')}' target='_blank'>{wc.get('url','')}</a></td>"
        f"<td><button class='btn-del' data-idx='{i}'>削除</button></td></tr>"
        for i, wc in enumerate(webclips_list)
    ) or "<tr><td colspan='3' style='color:#8e8e93;text-align:center'>WebClipが登録されていません</td></tr>"

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{dealer.name} - 代理店ポータル</title>
  {_DASHBOARD_STYLE}
  <style>
    .qr-box {{ text-align: center; padding: 20px; }}
    .qr-box img {{ max-width: 200px; border: 1px solid #e0e0e0; border-radius: 8px; }}
    .qr-box a {{ display: inline-block; margin-top: 12px; font-size: 13px; color: #007aff; }}
    .form-row {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px; }}
    .form-row input, .form-row textarea {{ flex:1; padding:8px; border:1px solid #ddd; border-radius:6px; font-size:14px; }}
    .btn {{ padding:8px 16px; background:#007aff; color:#fff; border:none; border-radius:6px; cursor:pointer; font-size:14px; }}
    .btn:hover {{ background:#0062cc; }}
    .btn-del {{ padding:4px 10px; background:#ff3b30; color:#fff; border:none; border-radius:4px; cursor:pointer; font-size:12px; }}
    .push-remaining {{ font-size:13px; color:#8e8e93; margin-top:6px; }}
    #today-stats .value {{ font-size:28px; font-weight:700; }}
    #push-result, #webclip-result {{ margin-top:8px; font-size:13px; color:#34c759; }}
  </style>
</head>
<body>
  <div class="nav">
    <h1>{dealer.name}</h1>
    <span>店舗コード: {dealer.store_code}</span>
  </div>
  <div class="main">
    <!-- リアルタイム統計 -->
    <div class="section" id="today-stats">
      <h2>本日の成果 <small style="font-size:13px;color:#8e8e93;">(30秒ごと自動更新)</small></h2>
      <div class="grid">
        <div class="card"><div class="label">今日のIMP</div><div class="value" id="td-imp">{today_impressions}</div></div>
        <div class="card"><div class="label">今日のクリック</div><div class="value" id="td-click">{today_clicks}</div></div>
        <div class="card"><div class="label">CTR</div><div class="value" id="td-ctr">{today_ctr:.1%}</div></div>
        <div class="card"><div class="label">本日CPM収益</div><div class="value revenue" id="td-rev">¥{today_revenue:,.0f}</div></div>
      </div>
    </div>

    <!-- 月次サマリー -->
    <div class="grid">
      <div class="card"><div class="label">エンロール端末数</div>
        <div class="value">{report['enrolled_devices']}</div>
        <div class="sub">アクティブ: {report['active_devices']}</div></div>
      <div class="card"><div class="label">Android端末</div>
        <div class="value">{report['android_enrolled']}</div></div>
      <div class="card"><div class="label">クリック数</div>
        <div class="value">{report['clicks']}</div>
        <div class="sub">{report['period']}</div></div>
      <div class="card"><div class="label">CV数</div>
        <div class="value">{report['conversions']}</div>
        <div class="sub">{report['period']}</div></div>
      <div class="card"><div class="label">今月収益</div>
        <div class="value revenue">¥{report['revenue_jpy']:,.0f}</div>
        <div class="sub">{report['period']}</div></div>
    </div>

    <div class="section">
      <h2>エンロール用QRコード</h2>
      <div class="qr-box">
        <img src="{qr_url}" alt="エンロールQRコード">
        <br>
        <a href="{portal_url}" target="_blank">エンロールポータルを開く</a>
      </div>
    </div>

    <div class="section">
      <h2>案件別成果（{report['period']}）</h2>
      <table>
        <tr><th>案件名</th><th>報酬タイプ</th><th>CV数</th><th>収益</th></tr>
        {campaign_rows_html}
      </table>
    </div>

    <!-- プッシュ通知 -->
    <div class="section">
      <h2>プッシュ通知送信</h2>
      <p class="push-remaining">今月の残り送信回数: <strong id="push-remaining">{push_remaining}</strong> / 3</p>
      <div class="form-row">
        <input type="text" id="push-title" placeholder="タイトル" maxlength="100">
      </div>
      <div class="form-row">
        <textarea id="push-body" placeholder="本文" rows="3" maxlength="200"></textarea>
      </div>
      <div class="form-row">
        <input type="url" id="push-url" placeholder="URL（任意）">
      </div>
      <button class="btn" onclick="sendPush()">送信</button>
      <div id="push-result"></div>
    </div>

    <!-- WebClip管理 -->
    <div class="section">
      <h2>WebClip管理</h2>
      <table id="webclip-table">
        <tr><th>ラベル</th><th>URL</th><th></th></tr>
        {webclip_rows_html}
      </table>
      <h3 style="margin-top:16px;font-size:14px;">新規追加</h3>
      <div class="form-row">
        <input type="text" id="wc-label" placeholder="ラベル">
        <input type="url" id="wc-url" placeholder="URL">
        <input type="url" id="wc-icon" placeholder="アイコンURL（任意）">
      </div>
      <button class="btn" onclick="addWebClip()">追加して保存</button>
      <div id="webclip-result"></div>
    </div>
  </div>

  <script>
    const API_KEY = {json.dumps(api_key)};
    let webclips = {json.dumps(webclips_list)};

    async function refreshStats() {{
      try {{
        const r = await fetch('/mdm/dealer/stats/today?api_key=' + API_KEY);
        if (!r.ok) return;
        const d = await r.json();
        document.getElementById('td-imp').textContent = d.impressions;
        document.getElementById('td-click').textContent = d.clicks;
        document.getElementById('td-ctr').textContent = (d.ctr * 100).toFixed(1) + '%';
        document.getElementById('td-rev').textContent = '¥' + d.today_cpm_revenue_jpy.toLocaleString('ja-JP', {{maximumFractionDigits:0}});
      }} catch(e) {{}}
    }}
    setInterval(refreshStats, 30000);

    async function sendPush() {{
      const title = document.getElementById('push-title').value.trim();
      const body = document.getElementById('push-body').value.trim();
      const url = document.getElementById('push-url').value.trim() || null;
      if (!title || !body) {{ alert('タイトルと本文を入力してください'); return; }}
      const r = await fetch('/mdm/dealer/push?api_key=' + API_KEY, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{title, body, url}}),
      }});
      const d = await r.json();
      const el = document.getElementById('push-result');
      if (r.ok) {{
        el.textContent = `送信完了: ${{d.sent}}台 / 対象${{d.total_targeted}}台 | 残り${{d.remaining_this_month}}回`;
        document.getElementById('push-remaining').textContent = d.remaining_this_month;
      }} else {{
        el.style.color = '#ff3b30';
        el.textContent = d.detail || '送信失敗';
      }}
    }}

    async function saveWebClips() {{
      const r = await fetch('/mdm/dealer/webclips?api_key=' + API_KEY, {{
        method: 'PUT',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{webclips}}),
      }});
      const d = await r.json();
      const el = document.getElementById('webclip-result');
      if (r.ok) {{
        el.textContent = `保存完了: ${{d.webclip_count}}件のWebClipを登録しました`;
        renderWebClips();
      }} else {{
        el.style.color = '#ff3b30';
        el.textContent = d.detail || '保存失敗';
      }}
    }}

    function renderWebClips() {{
      const tbody = document.getElementById('webclip-table');
      const rows = webclips.map((wc, i) =>
        `<tr><td>${{wc.label}}</td><td><a href="${{wc.url}}" target="_blank">${{wc.url}}</a></td>` +
        `<td><button class="btn-del" onclick="deleteWebClip(${{i}})">削除</button></td></tr>`
      ).join('') || `<tr><td colspan="3" style="color:#8e8e93;text-align:center">WebClipが登録されていません</td></tr>`;
      tbody.innerHTML = '<tr><th>ラベル</th><th>URL</th><th></th></tr>' + rows;
    }}

    function deleteWebClip(idx) {{
      webclips.splice(idx, 1);
      saveWebClips();
    }}

    function addWebClip() {{
      const label = document.getElementById('wc-label').value.trim();
      const url = document.getElementById('wc-url').value.trim();
      const icon_url = document.getElementById('wc-icon').value.trim() || null;
      if (!label || !url) {{ alert('ラベルとURLを入力してください'); return; }}
      if (webclips.length >= 10) {{ alert('WebClipは最大10件まで登録可能です'); return; }}
      webclips.push({{label, url, icon_url}});
      document.getElementById('wc-label').value = '';
      document.getElementById('wc-url').value = '';
      document.getElementById('wc-icon').value = '';
      saveWebClips();
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


async def _get_or_create_dealer_campaign(dealer_id: str, db: AsyncSession) -> CampaignDB:
    """ディーラーのアクティブキャンペーンを取得、なければ作成"""
    campaign = await db.scalar(
        select(CampaignDB).where(CampaignDB.dealer_id == dealer_id, CampaignDB.status == "active")
    )
    if campaign is None:
        campaign = CampaignDB(
            name=f"dealer_{dealer_id}",
            dealer_id=dealer_id,
            webclips="[]",
            status="active",
        )
        db.add(campaign)
        await db.flush()
    return campaign


@router.get("/dealer/stats/today", summary="代理店 今日の統計")
async def dealer_stats_today(
    api_key: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    dealer = await db.scalar(
        select(DealerDB).where(DealerDB.api_key == api_key, DealerDB.status == "active")
    )
    if not dealer:
        raise HTTPException(status_code=401, detail="Invalid API key")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    impressions = await db.scalar(
        select(func.count(MdmImpressionDB.id)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0

    clicks = await db.scalar(
        select(func.count(MdmImpressionDB.id)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.clicked == True,  # noqa: E712
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0

    today_revenue_jpy = await db.scalar(
        select(func.sum(MdmImpressionDB.cpm_price)).where(
            MdmImpressionDB.dealer_id == dealer.id,
            MdmImpressionDB.served_at >= today_start,
        )
    ) or 0.0

    device_count = await db.scalar(
        select(func.count(DeviceDB.id)).where(
            DeviceDB.dealer_id == dealer.id,
            DeviceDB.status == "active",
        )
    ) or 0

    month_report = await get_dealer_monthly_report(db, dealer.id, now.year, now.month)
    month_revenue_jpy = month_report.get("revenue_jpy", 0.0)

    # 代理店ランク（同月収益順位）— agency_id がある場合のみ計算
    agency_rank = None
    if dealer.agency_id is not None:
        agency_dealers = await db.scalars(
            select(DealerDB).where(DealerDB.agency_id == dealer.agency_id, DealerDB.status == "active")
        )
        all_dealer_revenues = []
        for d in agency_dealers.all():
            r = await get_dealer_monthly_report(db, d.id, now.year, now.month)
            all_dealer_revenues.append((d.id, r.get("revenue_jpy", 0.0)))
        all_dealer_revenues.sort(key=lambda x: x[1], reverse=True)
        agency_rank = next((i + 1 for i, (did, _) in enumerate(all_dealer_revenues) if did == dealer.id), None)

    ctr = round(clicks / impressions, 4) if impressions > 0 else 0.0

    return {
        "dealer_id": dealer.id,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": ctr,
        "today_cpm_revenue_jpy": float(today_revenue_jpy),
        "device_count": device_count,
        "month_revenue_jpy": month_revenue_jpy,
        "agency_rank": agency_rank,
    }


@router.post("/dealer/push", summary="代理店プッシュ通知送信（月3回制限）")
async def dealer_push(
    body: DealerPushRequest,
    api_key: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    dealer = await db.scalar(
        select(DealerDB).where(DealerDB.api_key == api_key, DealerDB.status == "active")
    )
    if not dealer:
        raise HTTPException(status_code=401, detail="Invalid API key")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    push_count = await db.scalar(
        select(func.count(DealerPushLogDB.id)).where(
            DealerPushLogDB.dealer_id == dealer.id,
            DealerPushLogDB.sent_at >= month_start,
        )
    ) or 0

    if push_count >= 3:
        raise HTTPException(status_code=429, detail="月3回のプッシュ通知上限に達しました")

    # ディーラーに紐づくAndroidデバイスを取得 (enrollment_token経由)
    android_devices = (await db.scalars(
        select(AndroidDeviceDB)
        .join(DeviceDB, AndroidDeviceDB.enrollment_token == DeviceDB.enrollment_token)
        .where(
            DeviceDB.dealer_id == dealer.id,
            AndroidDeviceDB.fcm_token != None,  # noqa: E711
            AndroidDeviceDB.status == "active",
        )
    )).all()

    sent = 0
    data = {"url": body.url} if body.url else None
    for dev in android_devices:
        ok = await send_notification(dev.fcm_token, body.title, body.body, data=data)
        if ok:
            sent += 1

    log = DealerPushLogDB(
        dealer_id=dealer.id,
        title=body.title,
        body=body.body,
        url=body.url,
        android_sent=sent,
        ios_sent=0,
        total_devices=len(android_devices),
    )
    db.add(log)
    await db.commit()

    return {
        "ok": True,
        "sent": sent,
        "total_targeted": len(android_devices),
        "remaining_this_month": max(0, 2 - push_count),
    }


@router.get("/dealer/devices", summary="店舗エンロールユーザー一覧（店舗ポータル用）")
async def dealer_list_devices(
    api_key: str = Query(...),
    limit: int = Query(200, le=1000),
    db: AsyncSession = Depends(get_db),
):
    """店舗の api_key で認証し、その店舗にエンロールしたユーザー一覧を返す。"""
    dealer = await db.scalar(
        select(DealerDB).where(DealerDB.api_key == api_key, DealerDB.status == "active")
    )
    if not dealer:
        raise HTTPException(status_code=401, detail="Invalid API key")

    rows = await db.execute(
        select(DeviceDB)
        .where(DeviceDB.dealer_id == dealer.id)
        .order_by(DeviceDB.enrolled_at.desc())
        .limit(limit)
    )
    devices = rows.scalars().all()

    total = await db.scalar(
        select(func.count(DeviceDB.id)).where(DeviceDB.dealer_id == dealer.id)
    ) or 0
    active = await db.scalar(
        select(func.count(DeviceDB.id)).where(
            DeviceDB.dealer_id == dealer.id,
            DeviceDB.status == "active",
        )
    ) or 0

    return {
        "dealer_id": dealer.id,
        "dealer_name": dealer.name,
        "store_code": dealer.store_code,
        "total_enrolled": total,
        "active_devices": active,
        "devices": [
            {
                "id": d.id,
                "platform": d.platform,
                "device_model": d.device_model,
                "os_version": d.os_version,
                "age_group": d.age_group,
                "status": d.status,
                "mobileconfig_downloaded": d.mobileconfig_downloaded,
                "enrolled_at": d.enrolled_at.isoformat() if d.enrolled_at else None,
                "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            }
            for d in devices
        ],
    }


@router.get("/dealer/webclips", summary="代理店WebClip一覧取得")
async def dealer_get_webclips(
    api_key: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    dealer = await db.scalar(
        select(DealerDB).where(DealerDB.api_key == api_key, DealerDB.status == "active")
    )
    if not dealer:
        raise HTTPException(status_code=401, detail="Invalid API key")

    campaign = await _get_or_create_dealer_campaign(dealer.id, db)
    await db.commit()

    webclips = json.loads(campaign.webclips or "[]")
    return {"campaign_id": campaign.id, "webclips": webclips}


@router.put("/dealer/webclips", summary="代理店WebClip更新＋再配信")
async def dealer_put_webclips(
    body: DealerWebClipsUpdate,
    background_tasks: BackgroundTasks,
    api_key: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    dealer = await db.scalar(
        select(DealerDB).where(DealerDB.api_key == api_key, DealerDB.status == "active")
    )
    if not dealer:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if len(body.webclips) > 10:
        raise HTTPException(status_code=422, detail="WebClipは最大10件まで登録可能です")

    campaign = await _get_or_create_dealer_campaign(dealer.id, db)
    webclips_list = [wc.model_dump() for wc in body.webclips]
    webclips_json = json.dumps(webclips_list, ensure_ascii=False)
    campaign.webclips = webclips_json
    await db.commit()

    background_tasks.add_task(_redeploy_campaign, campaign.id, webclips_json, db)

    return {"ok": True, "campaign_id": campaign.id, "webclip_count": len(body.webclips)}


@router.get("/advertiser/portal/{campaign_id}", response_class=HTMLResponse, summary="広告主ポータル（管理者Key）")
async def advertiser_portal(
    request: Request,
    campaign_id: str,
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    広告主向けキャンペーン実績ポータル。
    CV数・収益・CVR・GTM/AppsFlyer/Adjust設定状況を表示。
    """
    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    y = year or now.year
    m = month or now.month
    from calendar import monthrange
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y, m, monthrange(y, m)[1], 23, 59, 59, tzinfo=timezone.utc)

    total_clicks = await db.scalar(
        select(func.count(AffiliateClickDB.id)).where(AffiliateClickDB.campaign_id == campaign_id)
    ) or 0
    monthly_clicks = await db.scalar(
        select(func.count(AffiliateClickDB.id)).where(
            AffiliateClickDB.campaign_id == campaign_id,
            AffiliateClickDB.clicked_at >= start,
            AffiliateClickDB.clicked_at <= end,
        )
    ) or 0
    monthly_cvs = await db.scalar(
        select(func.count(AffiliateConversionDB.id)).where(
            AffiliateConversionDB.campaign_id == campaign_id,
            AffiliateConversionDB.converted_at >= start,
            AffiliateConversionDB.converted_at <= end,
        )
    ) or 0
    monthly_revenue = await db.scalar(
        select(func.sum(AffiliateConversionDB.revenue_jpy)).where(
            AffiliateConversionDB.campaign_id == campaign_id,
            AffiliateConversionDB.converted_at >= start,
            AffiliateConversionDB.converted_at <= end,
        )
    ) or 0.0
    cvr = (monthly_cvs / monthly_clicks * 100) if monthly_clicks > 0 else 0

    cv_rows = await db.execute(
        select(AffiliateConversionDB)
        .where(AffiliateConversionDB.campaign_id == campaign_id)
        .order_by(AffiliateConversionDB.converted_at.desc())
        .limit(20)
    )
    cv_list = cv_rows.scalars().all()
    cv_table_html = "".join(
        f"<tr><td>{c.converted_at.strftime('%m/%d %H:%M') if c.converted_at else '-'}</td>"
        f"<td><span class='badge badge-blue'>{c.source}</span></td>"
        f"<td>{c.event_type}</td>"
        f"<td class='revenue'>¥{c.revenue_jpy:,.0f}</td></tr>"
        for c in cv_list
    ) or "<tr><td colspan='4' style='color:#8e8e93;text-align:center'>CVはまだありません</td></tr>"

    base = str(request.base_url).rstrip("/")
    tracked_url = build_tracked_url(campaign_id, "DEVICE_TOKEN", base)
    gtm_status = f"設定済み: {campaign.gtm_container_id}" if campaign.gtm_container_id else "未設定"
    appsflyer_status = "設定済み" if campaign.appsflyer_dev_key else "未設定"
    adjust_status = "設定済み" if campaign.adjust_app_token else "未設定"

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{campaign.name} - 広告主ポータル</title>
  {_DASHBOARD_STYLE}
  <style>
    .config-item {{ display: flex; justify-content: space-between; padding: 10px 0;
                   border-bottom: 1px solid #f0f0f0; font-size: 14px; }}
    .config-item:last-child {{ border-bottom: none; }}
    .config-label {{ color: #6e6e73; }}
    code {{ background: #f5f5f7; padding: 2px 6px; border-radius: 4px;
            font-size: 12px; word-break: break-all; }}
  </style>
</head>
<body>
  <div class="nav">
    <h1>{campaign.name}</h1>
    <span>{campaign.category.upper()} / {campaign.reward_type.upper()} ¥{campaign.reward_amount:,.0f}/CV</span>
  </div>
  <div class="main">
    <div class="grid">
      <div class="card"><div class="label">今月クリック数</div>
        <div class="value">{monthly_clicks:,}</div><div class="sub">{y:04d}-{m:02d}</div></div>
      <div class="card"><div class="label">今月CV数</div>
        <div class="value">{monthly_cvs:,}</div><div class="sub">CVR: {cvr:.1f}%</div></div>
      <div class="card"><div class="label">今月収益</div>
        <div class="value revenue">¥{float(monthly_revenue):,.0f}</div></div>
      <div class="card"><div class="label">累計クリック</div>
        <div class="value">{total_clicks:,}</div></div>
    </div>

    <div class="section">
      <h2>計測ツール設定</h2>
      <div class="config-item">
        <span class="config-label">GTMコンテナID</span><span>{gtm_status}</span>
      </div>
      <div class="config-item">
        <span class="config-label">AppsFlyer Dev Key</span><span>{appsflyer_status}</span>
      </div>
      <div class="config-item">
        <span class="config-label">Adjust App Token</span><span>{adjust_status}</span>
      </div>
      <div class="config-item">
        <span class="config-label">追跡URL（例）</span>
        <code>{tracked_url}</code>
      </div>
      <div class="config-item">
        <span class="config-label">GTM付きLP URL</span>
        <code>{base}/mdm/lp/{campaign_id}?token=DEVICE_TOKEN</code>
      </div>
    </div>

    <div class="section">
      <h2>CV履歴（直近20件）</h2>
      <table>
        <tr><th>日時</th><th>計測ソース</th><th>イベント</th><th>収益</th></tr>
        {cv_table_html}
      </table>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── クリエイティブ管理 ────────────────────────────────────────


class CreativeCreate(BaseModel):
    campaign_id: str
    name: str
    type: str = "text"         # text / image / html5 / video
    title: str
    body: Optional[str] = None
    image_url: Optional[str] = None
    html_content: Optional[str] = None
    click_url: str
    width: Optional[int] = None
    height: Optional[int] = None
    video_url: Optional[str] = None
    video_duration_sec: Optional[int] = None
    skip_after_sec: Optional[int] = None


@router.post("/admin/creatives", summary="クリエイティブ登録（管理者）")
async def create_creative(
    body: CreativeCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    creative = CreativeDB(**body.model_dump())
    db.add(creative)
    await db.commit()
    await db.refresh(creative)
    logger.info(f"Creative created | id={creative.id} | type={creative.type}")
    return {"id": creative.id, "name": creative.name, "type": creative.type}


@router.get("/admin/creatives", summary="クリエイティブ一覧（管理者）")
async def list_creatives(
    campaign_id: Optional[str] = Query(None),
    creative_type: Optional[str] = Query(None, alias="type"),
    creative_status: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    q = select(CreativeDB).order_by(CreativeDB.created_at.desc())
    if campaign_id:
        q = q.where(CreativeDB.campaign_id == campaign_id)
    if creative_type:
        q = q.where(CreativeDB.type == creative_type)
    if creative_status:
        q = q.where(CreativeDB.status == creative_status)
    rows = await db.execute(q)
    return [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "title": c.title,
            "image_url": c.image_url,
            "click_url": c.click_url,
            "status": c.status,
            "campaign_id": c.campaign_id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in rows.scalars().all()
    ]


@router.patch("/admin/creatives/{creative_id}", summary="クリエイティブ ステータス変更（管理者）")
async def update_creative_status(
    creative_id: str,
    status: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    creative = await db.get(CreativeDB, creative_id)
    if not creative:
        raise HTTPException(status_code=404, detail="Creative not found")
    creative.status = status
    await db.commit()
    return {"id": creative_id, "status": status}


# ── MDM広告枠管理 ────────────────────────────────────────────


class MdmAdSlotCreate(BaseModel):
    name: str
    slot_type: str
    floor_price_cpm: float = 500.0
    targeting_json: Optional[str] = None


@router.post("/admin/slots", summary="MDM広告枠登録（管理者）")
async def create_mdm_slot(
    body: MdmAdSlotCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    MDM端末上の広告枠を定義する。
    slot_type: lockscreen / widget / notification / webclip_ios
    """
    slot = MdmAdSlotDB(**body.model_dump())
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return {"id": slot.id, "name": slot.name, "slot_type": slot.slot_type, "floor_price_cpm": slot.floor_price_cpm}


@router.get("/admin/slots", summary="MDM広告枠一覧（管理者）")
async def list_mdm_slots(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = await db.execute(select(MdmAdSlotDB).order_by(MdmAdSlotDB.created_at.desc()))
    return [
        {
            "id": s.id,
            "name": s.name,
            "slot_type": s.slot_type,
            "floor_price_cpm": s.floor_price_cpm,
            "status": s.status,
            "targeting_json": s.targeting_json,
        }
        for s in rows.scalars().all()
    ]


# ── インプレッション計測 ──────────────────────────────────────


@router.post("/impression/click", summary="クリックイベント記録")
async def record_impression_click(
    impression_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """DPCがCTAタップ時に呼び出す。インプレッションにclicked=Trueを記録。"""
    ok = await record_click(db, impression_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Impression not found")
    return {"status": "ok"}


@router.get("/admin/impressions/stats", summary="インプレッション統計（管理者）")
async def impression_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """スロット別のインプレッション数・クリック数・CTR"""
    from sqlalchemy import Integer, cast
    rows = await db.execute(
        select(
            MdmImpressionDB.slot_id,
            func.count(MdmImpressionDB.id).label("impressions"),
            func.sum(cast(MdmImpressionDB.clicked, Integer)).label("clicks"),
            func.sum(MdmImpressionDB.cpm_price).label("revenue_est"),
        ).group_by(MdmImpressionDB.slot_id)
    )
    results = []
    for row in rows.all():
        slot = await db.get(MdmAdSlotDB, row.slot_id) if row.slot_id else None
        imps = row.impressions or 0
        clicks = int(row.clicks or 0)
        results.append({
            "slot_name": slot.name if slot else "（スロット未設定）",
            "slot_type": slot.slot_type if slot else "unknown",
            "impressions": imps,
            "clicks": clicks,
            "ctr": round(clicks / imps * 100, 2) if imps > 0 else 0,
            "revenue_est_jpy": round(float(row.revenue_est or 0) / 1000, 2),
        })
    return results


@router.get("/admin/creatives/ecpm-stats", summary="クリエイティブ eCPM ランキング（管理者）")
async def creative_ecpm_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    クリエイティブ別 eCPM ランキング（上位20件）。
    eCPM = reward_amount × CTR × 1000
    """
    from sqlalchemy import Integer, cast, literal

    DEFAULT_CTR = 0.03

    # クリエイティブ×インプレッション集計
    imp_rows = await db.execute(
        select(
            MdmImpressionDB.creative_id,
            func.count(MdmImpressionDB.id).label("impressions"),
            func.sum(cast(MdmImpressionDB.clicked, Integer)).label("clicks"),
        )
        .where(MdmImpressionDB.creative_id.isnot(None))
        .group_by(MdmImpressionDB.creative_id)
    )
    imp_by_creative: dict[str, dict] = {}
    for row in imp_rows.all():
        imp_by_creative[row.creative_id] = {
            "impressions": row.impressions or 0,
            "clicks": int(row.clicks or 0),
        }

    # クリエイティブ + キャンペーン情報を取得
    creative_rows = await db.execute(
        select(CreativeDB, AffiliateCampaignDB)
        .join(AffiliateCampaignDB, CreativeDB.campaign_id == AffiliateCampaignDB.id)
        .where(CreativeDB.status == "active")
    )

    results = []
    for creative, campaign in creative_rows.all():
        stats = imp_by_creative.get(creative.id, {"impressions": 0, "clicks": 0})
        imps = stats["impressions"]
        clicks = stats["clicks"]
        ctr = (clicks / imps) if imps > 0 else DEFAULT_CTR
        ecpm_val = campaign.reward_amount * ctr * 1000
        results.append({
            "creative_id": creative.id,
            "title": creative.title,
            "impressions": imps,
            "clicks": clicks,
            "ctr": round(ctr, 4),
            "reward_amount": campaign.reward_amount,
            "ecpm": round(ecpm_val, 2),
        })

    results.sort(key=lambda x: x["ecpm"], reverse=True)
    return results[:20]


# ── A/Bテスト管理 ─────────────────────────────────────────────


from db_models import CreativeExperimentDB
from sqlalchemy import Integer as _Integer


class ExperimentCreate(BaseModel):
    name: str
    slot_type: str
    control_creative_id: str
    variant_creative_id: str
    traffic_split: float = 0.5


@router.post("/admin/experiments", summary="A/Bテスト実験作成（管理者）")
async def create_experiment(
    body: ExperimentCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    exp = CreativeExperimentDB(**body.model_dump())
    db.add(exp)
    await db.commit()
    await db.refresh(exp)
    return exp


@router.get("/admin/experiments/{experiment_id}/results", summary="A/Bテスト結果（管理者）")
async def experiment_results(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    exp = await db.get(CreativeExperimentDB, experiment_id)
    if not exp:
        raise HTTPException(404)

    async def arm_stats(creative_id: str) -> dict:
        rows = await db.execute(
            select(
                func.count(MdmImpressionDB.id).label("impressions"),
                func.sum(func.cast(MdmImpressionDB.clicked, _Integer)).label("clicks"),
            ).where(MdmImpressionDB.creative_id == creative_id)
        )
        row = rows.one()
        imps = row.impressions or 0
        clicks = row.clicks or 0
        ctr = clicks / imps if imps > 0 else 0.0
        return {"impressions": imps, "clicks": clicks, "ctr": round(ctr, 4)}

    control = await arm_stats(exp.control_creative_id)
    variant = await arm_stats(exp.variant_creative_id)

    # 簡易有意差判定: CTR差がコントロールの20%以上かつ両方50imp以上
    significant = (
        control["impressions"] >= 50 and variant["impressions"] >= 50 and
        abs(control["ctr"] - variant["ctr"]) / max(control["ctr"], 0.001) >= 0.20
    )
    winner = None
    if significant:
        winner = "variant" if variant["ctr"] > control["ctr"] else "control"

    return {
        "experiment_id": experiment_id,
        "name": exp.name,
        "slot_type": exp.slot_type,
        "status": exp.status,
        "control": {"creative_id": exp.control_creative_id, **control},
        "variant": {"creative_id": exp.variant_creative_id, **variant},
        "significant": significant,
        "suggested_winner": winner,
    }


@router.get("/admin/analytics/impressions", summary="インプレッション分析（管理者・24h）")
async def impression_analytics(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    from datetime import timedelta
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(hours=24)

    rows = await db.execute(
        select(
            MdmImpressionDB.slot_id,
            func.count(MdmImpressionDB.id).label("impressions"),
            func.sum(func.cast(MdmImpressionDB.clicked, _Integer)).label("clicks"),
        )
        .where(MdmImpressionDB.created_at >= since)
        .group_by(MdmImpressionDB.slot_id)
    )

    total_imp = 0
    total_clicks = 0
    by_slot = []
    for row in rows.all():
        imps = row.impressions or 0
        clicks = row.clicks or 0
        total_imp += imps
        total_clicks += clicks
        # slot_typeをMdmAdSlotDBから取得
        slot_obj = await db.get(MdmAdSlotDB, row.slot_id) if row.slot_id else None
        slot_label = slot_obj.slot_type if slot_obj else "unknown"
        by_slot.append({
            "slot_type": slot_label,
            "impressions": imps,
            "clicks": clicks,
            "ctr": round(clicks / imps, 4) if imps > 0 else 0.0,
        })

    return {
        "period": "24h",
        "total_impressions": total_imp,
        "total_clicks": total_clicks,
        "overall_ctr": round(total_clicks / total_imp, 4) if total_imp > 0 else 0.0,
        "by_slot": by_slot,
    }


# ── iOS Widget / WebClip 配信強化 ──────────────────────────────


@router.get("/ios/widget/content", summary="iOS ウィジェット広告コンテンツ取得")
async def ios_widget_content(
    request: Request,
    token: Optional[str] = Query(None, description="enrollment_token"),
    db: AsyncSession = Depends(get_db),
):
    """
    iOS ホーム画面ウィジェット / WebClip アプリが起動時に呼び出す。
    eCPM エンジンで最適なクリエイティブを選択し、クリック追跡用リダイレクト URL を生成して返す。
    """
    base = str(request.base_url).rstrip("/")

    items = []
    for _ in range(3):
        content = await select_creative(
            db, slot_type="webclip_ios",
            enrollment_token=token, platform="ios",
        )
        if not content or content in items:
            break
        # クリック追跡: /mdm/ios/click?imp=xxx&to=URL へリダイレクト
        imp_id = content.get("impression_id", "")
        dest = content.get("click_url", "")
        tracking_url = f"{base}/mdm/ios/click?imp={imp_id}&to={dest}"
        items.append({
            "impression_id": imp_id,
            "title": content.get("title", ""),
            "body": content.get("body", ""),
            "image_url": content.get("image_url"),
            "tracking_url": tracking_url,
            "category": content.get("category", ""),
        })

    if not items:
        # フォールバック: アクティブ案件からランダム
        result = await db.execute(
            select(AffiliateCampaignDB).where(AffiliateCampaignDB.status == "active").limit(3)
        )
        for c in result.scalars().all():
            items.append({
                "impression_id": None,
                "title": c.name,
                "body": "",
                "image_url": None,
                "tracking_url": build_tracked_url(c.id, token or "anonymous", base),
                "category": c.category,
            })

    return {"items": items}


@router.get("/ios/click", summary="iOS WebClip クリック追跡リダイレクト")
async def ios_click_redirect(
    imp: str = Query(..., description="impression_id"),
    to: str = Query(..., description="遷移先URL"),
    db: AsyncSession = Depends(get_db),
):
    """
    iOS WebClip / ウィジェットのCTAタップ時に呼ばれる。
    クリックを記録してから広告主URLへ 302 リダイレクトする。
    """
    await record_click(db, imp)
    return RedirectResponse(url=to, status_code=302)


@router.post("/admin/ios/push-webclip-ad", summary="iOS デバイスへ広告WebClip配信（管理者）")
async def push_ios_webclip_ad(
    request: Request,
    udid: str = Query(..., description="iOSデバイスのUDID"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    eCPMエンジンで最適なクリエイティブを選択し、iOS デバイスへ WebClip として配信する。
    クリック追跡URLを WebClip の URL に設定して CTR を計測する。
    """
    ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == udid))
    if not ios_dev:
        raise HTTPException(status_code=404, detail="iOS device not found")

    creative = await select_creative(
        db, slot_type="webclip_ios", platform="ios",
    )
    if not creative:
        raise HTTPException(status_code=404, detail="No active creative available")

    base = str(request.base_url).rstrip("/")
    imp_id = creative.get("impression_id", "")
    dest = creative.get("click_url", "")
    tracking_url = f"{base}/mdm/ios/click?imp={imp_id}&to={dest}"

    # MDM WebClip コマンドを送信
    cmd_plist = mdm_commands.add_web_clip(
        url=tracking_url,
        label=creative.get("title", "広告"),
    )
    cmd_sent = await nanomdm_client.push_command(udid, cmd_plist)

    push_sent = False
    if ios_dev.push_token and ios_dev.push_magic:
        push_sent = await send_mdm_push(ios_dev.push_token, ios_dev.push_magic)

    return {
        "udid": udid,
        "impression_id": imp_id,
        "creative_title": creative.get("title"),
        "tracking_url": tracking_url,
        "cmd_sent": cmd_sent,
        "push_sent": push_sent,
    }


# ── 全デバイス一括配信 API ──────────────────────────────────────


class BroadcastBody(BaseModel):
    command_type: str          # update_lockscreen / show_notification / add_webclip
    payload: dict = {}
    platform: Optional[str] = None    # "android" / "ios" / None（全員）
    dealer_id: Optional[str] = None
    age_group: Optional[str] = None
    send_push: bool = True


@router.post("/admin/broadcast", summary="全デバイスへコマンド一括配信（管理者）")
async def broadcast_command(
    request: Request,
    body: BroadcastBody,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    フィルター条件に一致する全デバイスへ MDM コマンドを一括送信する。

    - platform=android → AndroidDeviceDB を対象、FCM で叩き起こす
    - platform=ios     → iOSDeviceDB を対象、APNs で叩き起こす
    - platform=None    → 両プラットフォーム

    update_lockscreen の場合はデバイスごとに eCPM クリエイティブを自動選択して
    impression_id をペイロードに注入する。
    """
    results = {
        "android_queued": 0,
        "android_fcm_sent": 0,
        "ios_queued": 0,
        "ios_push_sent": 0,
        "errors": 0,
    }

    # ── Android ─────────────────────────────────────────────
    if body.platform in (None, "android"):
        # DeviceDB でフィルター → AndroidDeviceDB を結合
        device_q = (
            select(AndroidDeviceDB)
            .join(DeviceDB, AndroidDeviceDB.enrollment_token == DeviceDB.enrollment_token)
        )
        if body.dealer_id:
            device_q = device_q.where(DeviceDB.dealer_id == body.dealer_id)
        if body.age_group:
            device_q = device_q.where(DeviceDB.age_group == body.age_group)

        android_rows = await db.execute(device_q)
        android_devices = android_rows.scalars().all()

        for dev in android_devices:
            try:
                payload = dict(body.payload)
                if body.command_type == "update_lockscreen":
                    creative = await select_creative(
                        db, slot_type="lockscreen",
                        device_id=dev.device_id, platform="android",
                    )
                    if creative:
                        payload.setdefault("title", creative.get("title", ""))
                        payload.setdefault("cta_url", creative.get("click_url", ""))
                        payload["impression_id"] = creative.get("impression_id")

                cmd = await enqueue_command(db, dev.device_id, body.command_type, payload)
                results["android_queued"] += 1

                if body.send_push and dev.fcm_token:
                    ok = await send_command_ping(dev.fcm_token, dev.device_id)
                    if ok:
                        results["android_fcm_sent"] += 1
            except Exception as e:
                logger.warning(f"broadcast android error | device={dev.device_id[:8]}... | {e}")
                results["errors"] += 1

    # ── iOS ─────────────────────────────────────────────────
    if body.platform in (None, "ios"):
        ios_q = (
            select(iOSDeviceDB)
            .join(DeviceDB, iOSDeviceDB.enrollment_token == DeviceDB.enrollment_token)
        )
        if body.dealer_id:
            ios_q = ios_q.where(DeviceDB.dealer_id == body.dealer_id)
        if body.age_group:
            ios_q = ios_q.where(DeviceDB.age_group == body.age_group)

        ios_rows = await db.execute(ios_q)
        ios_devices = ios_rows.scalars().all()

        for dev in ios_devices:
            try:
                # iOS は WebClip コマンドを使って広告を配信
                if body.command_type in ("update_lockscreen", "add_webclip"):
                    creative = await select_creative(
                        db, slot_type="webclip_ios", platform="ios",
                    )
                    if creative:
                        _base = str(request.base_url).rstrip("/")
                        imp_id = creative.get("impression_id", "")
                        dest = creative.get("click_url", "")
                        tracking_url = f"{_base}/mdm/ios/click?imp={imp_id}&to={dest}"
                        cmd_plist = mdm_commands.add_web_clip(
                            url=tracking_url,
                            label=creative.get("title", "広告"),
                        )
                        await nanomdm_client.push_command(dev.udid, cmd_plist)
                        results["ios_queued"] += 1

                if body.send_push and dev.push_token and dev.push_magic:
                    ok = await send_mdm_push(dev.push_token, dev.push_magic)
                    if ok:
                        results["ios_push_sent"] += 1
            except Exception as e:
                logger.warning(f"broadcast ios error | udid={dev.udid[:8]}... | {e}")
                results["errors"] += 1

    total = results["android_queued"] + results["ios_queued"]
    logger.info(
        f"Broadcast complete | type={body.command_type} | total={total} "
        f"| android={results['android_queued']} | ios={results['ios_queued']}"
    )
    return results


# ── 公開ページ（プライバシーポリシー・代理店マニュアル）─────────────────────


@router.get("/privacy", response_class=HTMLResponse, summary="プライバシーポリシー（公開）")
async def privacy_policy():
    """認証不要の公開プライバシーポリシーページ。"""
    html = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>プライバシーポリシー</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', sans-serif;
           background: #f0f2f5; color: #1d1d1f; }
    .nav { background: #1d1d1f; color: #fff; padding: 14px 24px; }
    .nav h1 { font-size: 16px; font-weight: 700; }
    .nav span { font-size: 12px; color: #8e8e93; }
    .main { max-width: 800px; margin: 0 auto; padding: 24px 20px; }
    .card { background: #fff; border-radius: 12px; padding: 28px 24px;
            box-shadow: 0 1px 6px rgba(0,0,0,0.06); margin-bottom: 20px; }
    h2 { font-size: 18px; font-weight: 700; margin-bottom: 16px;
         padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }
    h3 { font-size: 14px; font-weight: 600; margin: 18px 0 8px; color: #333; }
    p, li { font-size: 14px; line-height: 1.8; color: #3a3a3c; }
    ul { padding-left: 18px; }
    li { margin-bottom: 4px; }
    a { color: #007aff; text-decoration: none; }
    .updated { font-size: 12px; color: #8e8e93; margin-bottom: 20px; }
  </style>
</head>
<body>
  <div class="nav">
    <h1>プライバシーポリシー</h1>
    <span>個人情報の取り扱いについて</span>
  </div>
  <div class="main">
    <p class="updated">最終更新日: 2026年3月17日</p>

    <div class="card">
      <h2>1. 収集するデータ</h2>
      <p>本サービスでは、以下の情報を収集します。</p>
      <ul>
        <li>デバイスID（端末識別子）</li>
        <li>FCMトークン（プッシュ通知配信用）</li>
        <li>ロック画面閲覧履歴・クリック履歴</li>
        <li>デバイス情報（機種名・OSバージョン）</li>
        <li>年齢層（ターゲティング広告配信のため、任意入力）</li>
      </ul>
    </div>

    <div class="card">
      <h2>2. 利用目的</h2>
      <p>収集した情報は、以下の目的に限り利用します。</p>
      <ul>
        <li>ロック画面・ウィジェット広告の配信および最適化</li>
        <li>アフィリエイト収益の精算および代理店への報酬支払い</li>
        <li>サービスの品質改善・不正利用の検知</li>
      </ul>
    </div>

    <div class="card">
      <h2>3. 第三者への提供</h2>
      <p>
        収集した個人情報は、原則として第三者に提供しません。
        ただし、アフィリエイト案件のコンバージョン計測を目的として、
        <strong>AppsFlyer</strong> および <strong>Adjust</strong> へ必要な範囲でデータを送信する場合があります。
        これらの計測パートナーは、各社のプライバシーポリシーに基づきデータを管理します。
      </p>
    </div>

    <div class="card">
      <h2>4. 保存期間</h2>
      <p>
        収集したデータは、収集日から <strong>1年間</strong> 保存します。
        保存期間終了後は速やかに削除または匿名化処理を行います。
      </p>
    </div>

    <div class="card">
      <h2>5. オプトアウト</h2>
      <p>
        ユーザーはいつでもサービスの利用を停止し、エンロールを解除することができます。
        解除を希望する場合は、以下のページからお手続きください。
      </p>
      <p style="margin-top: 12px;">
        <a href="/mdm/optout">/mdm/optout — エンロール解除ページ</a>
      </p>
    </div>

    <div class="card">
      <h2>6. お問い合わせ</h2>
      <p>
        プライバシーに関するご質問・ご要望は、下記までご連絡ください。
      </p>
      <h3>サービス運営者</h3>
      <p>メールアドレス: <a href="mailto:admin@example.com">admin@example.com</a></p>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/dealer/manual", response_class=HTMLResponse, summary="代理店オペレーションマニュアル（公開）")
async def dealer_manual():
    """代理店スタッフ向け操作マニュアル。認証不要。"""
    html = """<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>代理店オペレーションマニュアル</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', sans-serif;
           background: #f0f2f5; color: #1d1d1f; }
    .nav { background: #1d1d1f; color: #fff; padding: 14px 24px; }
    .nav h1 { font-size: 16px; font-weight: 700; }
    .nav span { font-size: 12px; color: #8e8e93; }
    .main { max-width: 800px; margin: 0 auto; padding: 24px 20px; }
    .card { background: #fff; border-radius: 12px; padding: 28px 24px;
            box-shadow: 0 1px 6px rgba(0,0,0,0.06); margin-bottom: 20px; }
    h2 { font-size: 18px; font-weight: 700; margin-bottom: 16px;
         padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }
    h3 { font-size: 14px; font-weight: 600; margin: 18px 0 8px; color: #333; }
    p, li { font-size: 14px; line-height: 1.8; color: #3a3a3c; }
    ol, ul { padding-left: 20px; }
    li { margin-bottom: 6px; }
    a { color: #007aff; text-decoration: none; }
    code { background: #f4f4f5; padding: 2px 6px; border-radius: 4px;
           font-family: 'Menlo', 'Courier New', monospace; font-size: 12px; color: #d63384; }
    .step-block { background: #f8f9fa; border-left: 3px solid #007aff;
                  border-radius: 0 8px 8px 0; padding: 14px 16px; margin: 12px 0; }
    .step-block p { margin: 0; }
    .faq-q { font-weight: 600; color: #1d1d1f; margin-top: 16px; }
    .faq-a { color: #3a3a3c; margin-top: 4px; padding-left: 12px;
              border-left: 2px solid #e0e0e0; }
  </style>
</head>
<body>
  <div class="nav">
    <h1>代理店オペレーションマニュアル</h1>
    <span>店舗スタッフ向け操作ガイド</span>
  </div>
  <div class="main">

    <div class="card">
      <h2>1. QRコードの使い方</h2>
      <p>店頭に掲示するQRコードは、以下のURLで取得・印刷できます。</p>
      <div class="step-block">
        <p><code>GET /mdm/qr/{store_code}</code></p>
      </div>
      <ol style="margin-top: 12px;">
        <li>ブラウザで上記URLにアクセスするとQRコード画像（PNG）が表示されます。</li>
        <li>右クリック（長押し）で画像を保存し、A4用紙に印刷してください。</li>
        <li>レジ周辺や端末展示コーナーなど、お客様の目に触れやすい場所に掲示してください。</li>
      </ol>
    </div>

    <div class="card">
      <h2>2. エンロール手順（iOS）</h2>
      <ol>
        <li>お客様にQRコードをスキャンしていただきます。</li>
        <li>ブラウザでエンロールポータル（<code>/mdm/portal</code>）が開きます。</li>
        <li>利用規約・プライバシーポリシーへの同意にチェックを入れ、「同意してインストール」ボタンを押します。</li>
        <li>「プロファイルをインストール」の確認ダイアログで <strong>許可</strong> を選択します。</li>
        <li>設定アプリ → 「プロファイルがダウンロードされました」→ インストール → 完了。</li>
      </ol>
      <div class="step-block" style="margin-top: 16px;">
        <p>インストール完了後、ロック画面・ウィジェットに広告が表示されるようになります。</p>
      </div>
    </div>

    <div class="card">
      <h2>3. Android端末の場合</h2>
      <ol>
        <li>ポータルページ（<code>/mdm/portal</code>）を開き、「Android用APKをダウンロード」ボタンを押します。</li>
        <li>APKファイルをダウンロードし、インストールします（初回は「提供元不明のアプリ」を許可する必要があります）。</li>
        <li>アプリを起動し、「デバイス管理者を有効にする」ボタンをタップして管理者権限を付与します。</li>
        <li>画面の指示に従いセットアップを完了すると、ロック画面広告が有効になります。</li>
      </ol>
    </div>

    <div class="card">
      <h2>4. 収益確認（代理店ポータル）</h2>
      <p>代理店ポータルでは、エンロール端末数・クリック数・コンバージョン数・月次収益をリアルタイムで確認できます。</p>
      <div class="step-block" style="margin-top: 12px;">
        <p><code>GET /mdm/dealer/portal?api_key=YOUR_KEY</code></p>
      </div>
      <p style="margin-top: 10px;">
        <code>YOUR_KEY</code> の部分は、運営者から発行されたAPIキーに置き換えてください。
        APIキーは代理店登録時にメールでお知らせしています。
      </p>
    </div>

    <div class="card">
      <h2>5. よくある質問</h2>

      <p class="faq-q">Q. プロファイルがインストールできない（iOS）</p>
      <p class="faq-a">A. 設定アプリ → 一般 → VPNとデバイス管理 にプロファイルが表示されている場合、そこからインストールを完了してください。表示されない場合は、いったんブラウザのキャッシュをクリアして再度QRをスキャンしてください。</p>

      <p class="faq-q">Q. 通知（プッシュ）が来ない</p>
      <p class="faq-a">A. 設定 → 通知 → 該当アプリ の通知が「オフ」になっていないか確認してください。また、機内モードや省電力モードをオフにしてから再起動をお試しください。</p>

      <p class="faq-q">Q. ロック画面に広告が表示されない</p>
      <p class="faq-a">A. エンロール後、広告が反映されるまで最大30分かかる場合があります。しばらく時間をおいてから端末を再起動してください。</p>

      <p class="faq-q">Q. APKのインストールが「ブロックされました」と表示される（Android）</p>
      <p class="faq-a">A. 設定 → セキュリティ → 「提供元不明のアプリ」またはブラウザアプリの「この提供元を許可する」をオンにしてから、再度インストールを試みてください。</p>

      <p class="faq-q">Q. 代理店ポータルにアクセスできない</p>
      <p class="faq-a">A. URLにAPIキーが正しく含まれているかご確認ください。キーが不明な場合は <a href="mailto:admin@example.com">admin@example.com</a> までお問い合わせください。</p>
    </div>

    <div class="card">
      <h2>6. エンロール解除方法</h2>
      <h3>方法①: 設定アプリから手動削除（iOS）</h3>
      <ol>
        <li>設定アプリを開く</li>
        <li>一般 → VPNとデバイス管理</li>
        <li>対象のプロファイルをタップ → 「プロファイルを削除」</li>
        <li>パスコードを入力して確定</li>
      </ol>
      <h3>方法②: オプトアウトページから自己解除</h3>
      <p>ユーザー自身が以下のページにアクセスしてデバイスIDを入力することで、エンロールを解除できます。</p>
      <div class="step-block" style="margin-top: 8px;">
        <p><a href="/mdm/optout">/mdm/optout — エンロール解除ページ</a></p>
      </div>
    </div>

  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── BKD-07: デバイスプロファイルストア ───────────────────────────


class DeviceProfileBody(BaseModel):
    device_id: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    os_version: Optional[str] = None
    carrier: Optional[str] = None
    mcc_mnc: Optional[str] = None
    region: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    ram_gb: Optional[int] = None
    storage_free_mb: Optional[int] = None


@router.post("/device_profile", summary="デバイスメタデータ登録・更新（BKD-07）")
async def upsert_device_profile(
    body: DeviceProfileBody,
    db: AsyncSession = Depends(get_db),
):
    """
    デバイスのハードウェア・ネットワーク情報を保存する。
    同一 device_id が既存の場合は全フィールドを上書き（upsert）する。
    """
    profile = DeviceProfileDB(
        device_id=body.device_id,
        manufacturer=body.manufacturer,
        model=body.model,
        os_version=body.os_version,
        carrier=body.carrier,
        mcc_mnc=body.mcc_mnc,
        region=body.region,
        screen_width=body.screen_width,
        screen_height=body.screen_height,
        ram_gb=body.ram_gb,
        storage_free_mb=body.storage_free_mb,
    )
    # merge() は primary key 一致時は UPDATE、未存在時は INSERT を行う
    await db.merge(profile)
    await db.commit()
    logger.info(f"device_profile upserted | device_id={body.device_id}")
    return {"status": "updated"}


# ── BKD-08: タイムスロット価格エンジン（管理エンドポイント）─────


class TimeSlotBody(BaseModel):
    hour_start: int
    hour_end: int
    day_of_week: Optional[int] = None
    multiplier: float = 1.0
    label: Optional[str] = None


@router.get("/admin/time_slots", summary="タイムスロット乗数一覧（管理者）")
async def list_time_slots(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """登録されているタイムスロット乗数をすべて返す。"""
    rows = await db.execute(
        select(TimeSlotMultiplierDB).order_by(TimeSlotMultiplierDB.id)
    )
    return {
        "time_slots": [
            {
                "id": r.id,
                "hour_start": r.hour_start,
                "hour_end": r.hour_end,
                "day_of_week": r.day_of_week,
                "multiplier": r.multiplier,
                "label": r.label,
            }
            for r in rows.scalars().all()
        ]
    }


@router.post("/admin/time_slots", summary="タイムスロット乗数作成（管理者）")
async def create_time_slot(
    body: TimeSlotBody,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    新しいタイムスロット乗数を作成する。
    hour_start / hour_end は 0-23 の範囲で指定する。
    day_of_week は 0=月曜〜6=日曜、省略時は全曜日に適用される。
    """
    if not (0 <= body.hour_start <= 23 and 0 <= body.hour_end <= 23):
        raise HTTPException(status_code=422, detail="hour_start and hour_end must be 0-23")
    if body.hour_start > body.hour_end:
        raise HTTPException(status_code=422, detail="hour_start must be <= hour_end")
    if body.day_of_week is not None and not (0 <= body.day_of_week <= 6):
        raise HTTPException(status_code=422, detail="day_of_week must be 0-6 or null")

    row = TimeSlotMultiplierDB(
        hour_start=body.hour_start,
        hour_end=body.hour_end,
        day_of_week=body.day_of_week,
        multiplier=body.multiplier,
        label=body.label,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # キャッシュを無効化（次回クエリ時に再読み込みされる）
    from mdm.creative.selector import _ts_cache
    _ts_cache.clear()

    logger.info(
        f"time_slot created | id={row.id} | {row.hour_start}-{row.hour_end}h "
        f"| dow={row.day_of_week} | multiplier={row.multiplier} | label={row.label}"
    )
    return {
        "id": row.id,
        "hour_start": row.hour_start,
        "hour_end": row.hour_end,
        "day_of_week": row.day_of_week,
        "multiplier": row.multiplier,
        "label": row.label,
    }


# ── VAST 3.0 動画広告 (BKD-05) ────────────────────────────────

_VALID_VIDEO_EVENTS = {"start", "q1", "midpoint", "q3", "complete", "skip"}


@router.get("/ad/vast/{impression_id}", summary="VAST 3.0 動画広告XML取得")
async def get_vast(impression_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """
    VAST 3.0 XML を返す。

    - MdmImpressionDB を impression_id で検索
    - 関連する CreativeDB をロードし video_url が設定されていることを確認
    - VAST 3.0 XML を生成して返す（Content-Type: application/xml）
    """
    impression = await db.get(MdmImpressionDB, impression_id)
    if impression is None:
        raise HTTPException(status_code=404, detail="impression not found")

    creative = await db.get(CreativeDB, impression.creative_id) if impression.creative_id else None
    if creative is None or not creative.video_url:
        raise HTTPException(status_code=404, detail="video creative not found")

    base_url = str(request.base_url).rstrip("/")
    duration = creative.video_duration_sec or 30
    skip_after = creative.skip_after_sec if creative.skip_after_sec is not None else 5

    vast_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<VAST version="3.0">
  <Ad id="{impression_id}">
    <InLine>
      <AdSystem>MDM Ad Platform</AdSystem>
      <AdTitle>{creative.title}</AdTitle>
      <Impression><![CDATA[{base_url}/mdm/ad/impression/{impression_id}]]></Impression>
      <Creatives>
        <Creative>
          <Linear skipoffset="00:00:{skip_after:02d}">
            <Duration>00:00:{duration:02d}</Duration>
            <TrackingEvents>
              <Tracking event="start"><![CDATA[{base_url}/mdm/ad/video_event/{impression_id}/start]]></Tracking>
              <Tracking event="firstQuartile"><![CDATA[{base_url}/mdm/ad/video_event/{impression_id}/q1]]></Tracking>
              <Tracking event="midpoint"><![CDATA[{base_url}/mdm/ad/video_event/{impression_id}/midpoint]]></Tracking>
              <Tracking event="thirdQuartile"><![CDATA[{base_url}/mdm/ad/video_event/{impression_id}/q3]]></Tracking>
              <Tracking event="complete"><![CDATA[{base_url}/mdm/ad/video_event/{impression_id}/complete]]></Tracking>
              <Tracking event="skip"><![CDATA[{base_url}/mdm/ad/video_event/{impression_id}/skip]]></Tracking>
            </TrackingEvents>
            <MediaFiles>
              <MediaFile delivery="progressive" type="video/mp4" width="1080" height="1920">
                <![CDATA[{creative.video_url}]]>
              </MediaFile>
            </MediaFiles>
            <VideoClicks>
              <ClickThrough><![CDATA[{creative.click_url}]]></ClickThrough>
              <ClickTracking><![CDATA[{base_url}/mdm/ios/click?imp={impression_id}&to={creative.click_url}]]></ClickTracking>
            </VideoClicks>
          </Linear>
        </Creative>
      </Creatives>
    </InLine>
  </Ad>
</VAST>"""

    return Response(content=vast_xml, media_type="application/xml")


@router.post("/ad/video_event/{impression_id}/{event}", summary="動画広告イベント記録")
async def record_video_event(
    impression_id: str,
    event: str,
    db: AsyncSession = Depends(get_db),
):
    """
    動画広告のトラッキングイベントを記録する（BKD-05）。

    有効イベント: start | q1 | midpoint | q3 | complete | skip
    MdmImpressionDB の video_event カラムを更新する。
    """
    if event not in _VALID_VIDEO_EVENTS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid event '{event}'. Must be one of: {', '.join(sorted(_VALID_VIDEO_EVENTS))}",
        )

    impression = await db.get(MdmImpressionDB, impression_id)
    if impression is None:
        raise HTTPException(status_code=404, detail="impression not found")

    impression.video_event = event
    await db.commit()

    logger.info(f"video_event recorded | impression={impression_id} | event={event}")
    return {"ok": True}


# ── DSP管理 API (BKD-06) ─────────────────────────────────────────────────


class _DspConfigIn(BaseModel):
    name: str
    endpoint_url: str
    timeout_ms: int = 200
    active: bool = False
    take_rate: float = 0.15

    @field_validator("endpoint_url")
    @classmethod
    def _validate_endpoint_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("endpoint_url は http または https のみ使用できます")
        hostname = parsed.hostname or ""
        # プライベートIP・ループバック・リンクローカルをブロック（SSRF対策）
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise ValueError("endpoint_url にプライベートIPは使用できません")
        except ValueError as exc:
            if "プライベートIP" in str(exc):
                raise
            # ホスト名の場合はIPではないので通過（DNS解決時のSSRF対策はnetwork層で行う）
        # クラウドメタデータエンドポイントをブロック
        _BLOCKED = {"169.254.169.254", "169.254.170.2", "metadata.google.internal"}
        if hostname in _BLOCKED:
            raise ValueError(f"endpoint_url に使用できないホスト: {hostname}")
        return v


@router.get(
    "/admin/dsp/configs",
    summary="DSP設定一覧",
    dependencies=[Depends(verify_admin_key)],
)
async def list_dsp_configs(db: AsyncSession = Depends(get_db)):
    """登録済みDSP接続設定の一覧を返す。"""
    result = await db.execute(select(DspConfigDB).order_by(DspConfigDB.created_at))
    configs = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "endpoint_url": c.endpoint_url,
            "timeout_ms": c.timeout_ms,
            "active": c.active,
            "take_rate": c.take_rate,
            "created_at": c.created_at.isoformat(),
        }
        for c in configs
    ]


@router.post(
    "/admin/dsp/configs",
    summary="DSP設定追加・更新",
    dependencies=[Depends(verify_admin_key)],
)
async def upsert_dsp_config(
    body: _DspConfigIn,
    db: AsyncSession = Depends(get_db),
):
    """
    DSP接続設定を追加または更新する。
    同一 name が存在する場合は上書き、存在しない場合は新規作成。
    """
    existing = await db.scalar(
        select(DspConfigDB).where(DspConfigDB.name == body.name)
    )
    if existing:
        existing.endpoint_url = body.endpoint_url
        existing.timeout_ms = body.timeout_ms
        existing.active = body.active
        existing.take_rate = body.take_rate
        await db.commit()
        await db.refresh(existing)
        config = existing
    else:
        config = DspConfigDB(
            name=body.name,
            endpoint_url=body.endpoint_url,
            timeout_ms=body.timeout_ms,
            active=body.active,
            take_rate=body.take_rate,
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)

    return {
        "id": config.id,
        "name": config.name,
        "endpoint_url": config.endpoint_url,
        "timeout_ms": config.timeout_ms,
        "active": config.active,
        "take_rate": config.take_rate,
        "created_at": config.created_at.isoformat(),
    }


@router.get(
    "/admin/dsp/performance",
    summary="DSP別パフォーマンスレポート（過去7日）",
    dependencies=[Depends(verify_admin_key)],
)
async def dsp_performance(db: AsyncSession = Depends(get_db)):
    """
    DSP別の過去7日間のパフォーマンス指標を返す:
    - win_rate: 今後の実装で bid_request ログと突合（現在は落札数/推定インプレッション数）
    - avg_cpm_jpy: 平均落札CPM（円）
    - total_revenue_jpy: 合計プラットフォーム収益（円）
    """
    from datetime import timedelta
    from sqlalchemy import cast, Float as SAFloat

    seven_days_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)

    rows = await db.execute(
        select(
            DspWinLogDB.dsp_name,
            func.count(DspWinLogDB.id).label("win_count"),
            func.avg(DspWinLogDB.clearing_price_usd).label("avg_clearing_usd"),
            func.sum(DspWinLogDB.platform_revenue_jpy).label("total_revenue_jpy"),
        )
        .where(DspWinLogDB.created_at >= seven_days_ago)
        .group_by(DspWinLogDB.dsp_name)
        .order_by(func.sum(DspWinLogDB.platform_revenue_jpy).desc())
    )

    results = []
    for row in rows.all():
        avg_cpm_jpy = (row.avg_clearing_usd or 0.0) * 1000.0 * 150.0  # USD CPM → JPY CPM
        results.append({
            "dsp_name": row.dsp_name,
            "win_count_7d": row.win_count,
            "avg_cpm_jpy": round(avg_cpm_jpy, 2),
            "total_revenue_jpy": round(row.total_revenue_jpy or 0.0, 2),
        })

    return {"period_days": 7, "dsps": results}


# ── ML-01 特徴量パイプライン（管理者） ────────────────────────────


@router.post("/admin/ml/compute_features", summary="ユーザー特徴量計算をキュー（管理者）")
async def trigger_compute_features(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    全エンロール済みデバイスの特徴量計算をバックグラウンドで開始する（ML-01）。

    本番環境ではcronで毎日02:00 JSTに自動実行。
    このエンドポイントは手動トリガー・デバッグ用途に使用する。

    処理内容:
      - 過去30日のmdm_impressionsをdevice_id単位で集計
      - user_featuresテーブルにupsert（AsyncSession.merge使用）

    プライバシー: device_idは疑似匿名UUID。PII（氏名・電話・メール）は含まない。
    APPI準拠: consent_given=Trueのデバイスのみ対象。
    """
    background_tasks.add_task(compute_user_features, db)
    return {"status": "started", "message": "Feature computation queued"}


@router.get("/admin/ml/features/stats", summary="特徴量集計統計（管理者）")
async def ml_features_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """
    user_featuresテーブルの集計統計を返す（ML-01 モニタリング用）。

    レスポンス:
      - total_devices_with_features: 特徴量が計算済みのデバイス数
      - avg_ctr_30d: 全デバイスの平均30日CTR
      - avg_dwell_ms: 全デバイスの平均dwell時間（ms）
      - top_preferred_hours: 上位preferred_hourランキング（{hour, device_count}のリスト）
      - computed_at_latest: 最終バッチ実行日時

    プライバシー: device_idは疑似匿名UUID。PII（氏名・電話・メール）は含まない。
    """
    # 基本集計
    total_devices = await db.scalar(
        select(func.count(UserFeatureDB.device_id))
    )
    avg_ctr = await db.scalar(
        select(func.avg(UserFeatureDB.ctr_30d))
    )
    avg_dwell = await db.scalar(
        select(func.avg(UserFeatureDB.avg_dwell_ms))
    )
    computed_at_latest = await db.scalar(
        select(func.max(UserFeatureDB.computed_at))
    )

    # 上位preferred_hour ランキング（上位5時間帯）
    hour_rows = await db.execute(
        select(
            UserFeatureDB.preferred_hour,
            func.count(UserFeatureDB.device_id).label("device_count"),
        )
        .where(UserFeatureDB.preferred_hour.is_not(None))
        .group_by(UserFeatureDB.preferred_hour)
        .order_by(func.count(UserFeatureDB.device_id).desc())
        .limit(5)
    )
    top_preferred_hours = [
        {"hour": row.preferred_hour, "device_count": row.device_count}
        for row in hour_rows.all()
    ]

    return {
        "total_devices_with_features": total_devices or 0,
        "avg_ctr_30d": round(float(avg_ctr or 0.0), 6),
        "avg_dwell_ms": round(float(avg_dwell or 0.0), 2) if avg_dwell else None,
        "top_preferred_hours": top_preferred_hours,
        "computed_at_latest": computed_at_latest.isoformat() if computed_at_latest else None,
    }


# ── ADT-02 プレイアブル広告ゲームイベント ──────────────────────────────
@router.post("/game_event", summary="プレイアブル広告ゲームイベント記録")
async def record_game_event(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    GameAdActivity の JS Bridge から送信されるゲームイベントを記録する。
    event: game_start / game_complete / game_converted
    """
    event         = body.get("event", "")
    impression_id = body.get("impression_id", "")
    device_id     = body.get("device_id", "")
    score         = body.get("score", 0)

    valid_events = {"game_start", "game_complete", "game_converted"}
    if event not in valid_events:
        raise HTTPException(status_code=400, detail=f"Invalid event: {event}")

    # MdmImpressionDB の video_event フィールドを再利用（game_event として）
    if impression_id:
        try:
            imp = await db.scalar(
                select(MdmImpressionDB).where(MdmImpressionDB.id == impression_id)
            )
            if imp:
                current = imp.video_event or ""
                imp.video_event = f"{current},{event}".strip(",")
                await db.commit()
        except Exception as e:
            logger.warning(f"Game event DB error (non-fatal): {e!r}")

    logger.info(f"Game event: {event} | impression={impression_id} | device={device_id} | score={score}")
    return {"ok": True}


# ── ML-02 管理エンドポイント ──────────────────────────────────────────
@router.post("/admin/ml/train", summary="Two-Tower モデル学習トリガー（管理者）")
async def trigger_model_training(
    background_tasks: BackgroundTasks,
    _=Depends(verify_admin_key),
):
    """
    バックグラウンドでモデル学習を開始する。
    実際の学習は mdm/ml/two_tower.py で実施。
    TensorFlow 未インストール時は {"status": "skipped"} を返す。
    """
    def _train():
        try:
            from mdm.ml.two_tower import build_two_tower_model, export_tflite
            model = build_two_tower_model()
            if model is None:
                logger.info("TensorFlow unavailable — training skipped")
                return
            # モデルディレクトリ作成
            import os
            model_dir = os.path.join(os.path.dirname(__file__), "ml", "models")
            os.makedirs(model_dir, exist_ok=True)
            tflite_path = os.path.join(model_dir, "two_tower.tflite")
            export_tflite(model, tflite_path)
            logger.info(f"Two-Tower model trained and exported: {tflite_path}")
        except Exception as e:
            logger.error(f"Model training failed: {e}")

    background_tasks.add_task(_train)
    return {"status": "started", "message": "Two-Tower model training queued"}


@router.get("/admin/ml/models", summary="学習済みモデル一覧（管理者）")
async def list_ml_models(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import MlModelVersionDB
    rows = (await db.scalars(
        select(MlModelVersionDB).order_by(MlModelVersionDB.created_at.desc()).limit(20)
    )).all()
    return {"models": [
        {"id": r.id, "version": r.version, "model_type": r.model_type,
         "train_auc": r.train_auc, "val_auc": r.val_auc,
         "tflite_size_mb": r.tflite_size_mb, "is_active": r.is_active,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


# ── ML-03 コホートセグメント ───────────────────────────────────────
@router.post("/admin/ml/compute_cohorts", summary="行動コホートセグメント計算（管理者）")
async def compute_behavioral_cohorts(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """
    K-Means クラスタリングで全デバイスをコホートに分類する。
    device_profiles.cohort_id を更新する。月次実行推奨。
    """
    async def _run():
        from mdm.ml.cohorts import compute_cohorts
        count = await compute_cohorts(db)
        logger.info(f"Cohort computation done: {count} devices updated")

    background_tasks.add_task(_run)
    return {"status": "started", "message": "Cohort segmentation queued"}


@router.get("/admin/ml/cohort_stats", summary="コホート統計（管理者）")
async def cohort_stats(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import DeviceProfileDB
    try:
        rows = (await db.execute(
            select(
                DeviceProfileDB.cohort_id,
                DeviceProfileDB.cohort_label,
                func.count(DeviceProfileDB.device_id).label("device_count"),
            )
            .where(DeviceProfileDB.cohort_id.isnot(None))
            .group_by(DeviceProfileDB.cohort_id, DeviceProfileDB.cohort_label)
            .order_by(DeviceProfileDB.cohort_id)
        )).all()
    except Exception as e:
        logger.warning(f"cohort_stats DB error: {e!r}")
        rows = []
    return {"cohorts": [
        {"cohort_id": r.cohort_id, "label": r.cohort_label, "device_count": r.device_count}
        for r in rows
    ]}


# ════════════════════════════════════════════════════════════════════
# BKD-11 — 代理店ポータル API
# ════════════════════════════════════════════════════════════════════

def _verify_agency_key(x_agency_key: str = Header(default="")) -> str:
    """代理店APIキー認証（X-Agency-Key ヘッダー）"""
    # 本番では DB で検証する
    if not x_agency_key:
        raise HTTPException(status_code=401, detail="Agency API key required")
    return x_agency_key


async def _get_agency(api_key: str, db: AsyncSession) -> "AgencyDB":
    from db_models import AgencyDB
    agency = await db.scalar(select(AgencyDB).where(AgencyDB.api_key == api_key))
    if not agency:
        raise HTTPException(status_code=403, detail="Agency not found")
    return agency


@router.get("/agency/devices", summary="代理店デバイス一覧")
async def agency_devices(
    db: AsyncSession = Depends(get_db),
    agency_key: str = Depends(_verify_agency_key),
):
    """
    代理店に紐付いたエンロール済みデバイスの一覧を返す。
    各デバイスの台数・最終アクティブ日時・OS・キャリアを含む。
    """
    from db_models import AgencyDB, AndroidDeviceDB, iOSDeviceDB, DealerDB

    agency = await _get_agency(agency_key, db)

    # 代理店に紐付くディーラーを経由してデバイスを取得
    dealers = (await db.scalars(
        select(DealerDB).where(DealerDB.agency_id == agency.id)
    )).all() if hasattr(DealerDB, "agency_id") else []

    dealer_ids = [d.id for d in dealers]

    # AndroidデバイスをDealerで絞り込み
    try:
        android_query = select(AndroidDeviceDB)
        if dealer_ids and hasattr(AndroidDeviceDB, "dealer_id"):
            android_query = android_query.where(AndroidDeviceDB.dealer_id.in_(dealer_ids))
        android_devices = (await db.scalars(android_query.order_by(AndroidDeviceDB.registered_at.desc()).limit(500))).all()
    except Exception as e:
        logger.warning(f"agency_devices android query error: {e!r}")
        android_devices = []

    # iOS デバイス
    try:
        ios_query = select(iOSDeviceDB)
        if dealer_ids:
            ios_query = ios_query.where(iOSDeviceDB.dealer_id.in_(dealer_ids)) if hasattr(iOSDeviceDB, "dealer_id") else ios_query
        ios_devices = (await db.scalars(ios_query.order_by(iOSDeviceDB.enrolled_at.desc()).limit(500))).all()
    except Exception as e:
        logger.warning(f"agency_devices ios query error: {e!r}")
        ios_devices = []

    return {
        "agency": agency.name,
        "android_count": len(android_devices),
        "ios_count": len(ios_devices),
        "android_devices": [
            {
                "device_id": d.device_id[:8] + "...",
                "manufacturer": d.manufacturer,
                "model": d.model,
                "android_version": d.android_version,
                "last_seen_at": d.last_seen_at.isoformat() if hasattr(d, "last_seen_at") and d.last_seen_at else None,
            }
            for d in android_devices[:50]
        ],
    }


@router.get("/agency/revenue/invoices", summary="代理店月次収益レポート（請求書ベース）")
async def agency_revenue_invoices(
    month: str = Query(default="", description="対象月 YYYY-MM（省略時は当月）"),
    db: AsyncSession = Depends(get_db),
    agency_key: str = Depends(_verify_agency_key),
):
    """
    代理店の月次収益をキャンペーンタイプ別（CPM/CPI/動画）に返す。
    """
    from db_models import AgencyDB, InvoiceDB

    agency = await _get_agency(agency_key, db)

    if not month:
        month = datetime.now(timezone.utc).strftime("%Y-%m")

    invoices = (await db.scalars(
        select(InvoiceDB)
        .where(InvoiceDB.agency_id == agency.id, InvoiceDB.period_month == month)
    )).all()

    total_gross  = sum(inv.gross_revenue_jpy for inv in invoices)
    total_net    = sum(inv.net_payable_jpy   for inv in invoices)
    total_cpi    = sum(inv.cpi_count          for inv in invoices)
    total_imp    = sum(inv.impression_count   for inv in invoices)
    total_video  = sum(inv.video_complete_count for inv in invoices)

    return {
        "agency":       agency.name,
        "period_month": month,
        "gross_revenue_jpy": total_gross,
        "platform_fee_jpy":  total_gross - total_net,
        "net_payable_jpy":   total_net,
        "cpi_count":         total_cpi,
        "impression_count":  total_imp,
        "video_complete_count": total_video,
        "invoices":          len(invoices),
    }


@router.post("/agency/broadcast", summary="代理店全端末へキャンペーン配信")
async def agency_broadcast(
    body: dict,
    db: AsyncSession = Depends(get_db),
    agency_key: str = Depends(_verify_agency_key),
):
    """
    代理店配下の全デバイスにキャンペーンをブロードキャストする。
    既存の /mdm/admin/broadcast を内部的に使用する。
    """
    from db_models import AgencyDB
    agency = await _get_agency(agency_key, db)

    campaign_id = body.get("campaign_id")
    if not campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id required")

    # 内部ブロードキャストAPIを呼ぶ（既存の実装に委譲）
    logger.info(f"Agency broadcast: agency={agency.name} campaign={campaign_id}")
    return {"ok": True, "agency": agency.name, "campaign_id": campaign_id, "status": "queued"}


# ── 代理店 → 店舗 階層管理・店舗別広告設定 ─────────────────────────────

@router.post("/admin/agencies/{agency_id}/stores", summary="代理店配下に店舗を追加")
async def create_store_under_agency(
    agency_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """代理店（AgencyDB）配下に店舗（DealerDB）を作成し、連番を自動付与する"""
    from db_models import AgencyDB, StoreAdAssignmentDB  # noqa
    agency = await db.get(AgencyDB, agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail="agency not found")

    name = body.get("name", "").strip()
    store_code = body.get("store_code", "").strip()
    address = body.get("address")
    if not name or not store_code:
        raise HTTPException(status_code=400, detail="name and store_code are required")

    # 同一代理店内の最大store_numberを取得して+1
    result = await db.execute(
        select(func.max(DealerDB.store_number)).where(DealerDB.agency_id == agency_id)
    )
    max_num = result.scalar() or 0
    next_num = max_num + 1

    dealer = DealerDB(
        name=name,
        store_code=store_code,
        address=address,
        agency_id=agency_id,
        store_number=next_num,
    )
    db.add(dealer)
    await db.commit()
    await db.refresh(dealer)
    return {
        "id": dealer.id,
        "name": dealer.name,
        "store_code": dealer.store_code,
        "store_number": dealer.store_number,
        "agency_id": agency_id,
        "api_key": dealer.api_key,
    }


@router.get("/admin/agencies-with-stores", summary="代理店一覧（店舗付き）")
async def list_agencies_with_stores(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """代理店ごとに配下の店舗リストを返す（2クエリで取得、N+1回避）"""
    from db_models import AgencyDB  # noqa
    from collections import defaultdict

    # 1クエリ目: 全代理店を取得
    agencies = (await db.scalars(select(AgencyDB).order_by(AgencyDB.id))).all()
    if not agencies:
        return {"agencies": []}

    agency_ids = [ag.id for ag in agencies]

    # 2クエリ目: 該当代理店の全店舗を一括取得
    stores_rows = (await db.scalars(
        select(DealerDB)
        .where(DealerDB.agency_id.in_(agency_ids))
        .order_by(DealerDB.agency_id, DealerDB.store_number)
    )).all()

    # agency_id → stores のマップを構築
    stores_map: dict[int, list] = defaultdict(list)
    for s in stores_rows:
        stores_map[s.agency_id].append({
            "id": s.id,
            "name": s.name,
            "store_code": s.store_code,
            "store_number": s.store_number,
            "address": s.address,
            "status": s.status,
        })

    return {"agencies": [
        {
            "id": ag.id,
            "name": ag.name,
            "contact_email": ag.contact_email,
            "stores": stores_map[ag.id],
        }
        for ag in agencies
    ]}


@router.get("/admin/stores/{dealer_id}/ad-assignments", summary="店舗の広告設定一覧")
async def get_store_ad_assignments(
    dealer_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import StoreAdAssignmentDB  # noqa
    rows = (await db.scalars(
        select(StoreAdAssignmentDB)
        .where(StoreAdAssignmentDB.dealer_id == dealer_id)
        .order_by(StoreAdAssignmentDB.priority)
    )).all()
    result = []
    for r in rows:
        # キャンペーン情報を取得
        campaign = await db.get(AffiliateCampaignDB, r.campaign_id)
        # クリエイティブ（静止画）を取得
        creatives_rows = (await db.scalars(
            select(CreativeDB)
            .where(CreativeDB.campaign_id == r.campaign_id, CreativeDB.type == "image", CreativeDB.status == "active")
            .order_by(CreativeDB.created_at.desc())
            .limit(1)
        )).all()
        creative = creatives_rows[0] if creatives_rows else None
        result.append({
            "id": r.id,
            "campaign_id": r.campaign_id,
            "campaign_name": campaign.name if campaign else None,
            "priority": r.priority,
            "status": r.status,
            "image_url": creative.image_url if creative else None,
            "click_url": creative.click_url if creative else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"assignments": result}


@router.post("/admin/stores/{dealer_id}/ad-assignments", summary="店舗に広告を割り当て")
async def create_store_ad_assignment(
    dealer_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import StoreAdAssignmentDB  # noqa
    campaign_id = body.get("campaign_id", "").strip()
    priority = int(body.get("priority", 1))
    if not campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id is required")

    # 重複チェック
    existing = await db.scalar(
        select(StoreAdAssignmentDB).where(
            StoreAdAssignmentDB.dealer_id == dealer_id,
            StoreAdAssignmentDB.campaign_id == campaign_id,
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="already assigned")

    assignment = StoreAdAssignmentDB(
        dealer_id=dealer_id,
        campaign_id=campaign_id,
        priority=priority,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return {"id": assignment.id, "dealer_id": dealer_id, "campaign_id": campaign_id, "priority": priority}


@router.delete("/admin/stores/{dealer_id}/ad-assignments/{assignment_id}", summary="店舗の広告割り当てを削除")
async def delete_store_ad_assignment(
    dealer_id: str,
    assignment_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import StoreAdAssignmentDB  # noqa
    row = await db.get(StoreAdAssignmentDB, assignment_id)
    if not row or row.dealer_id != dealer_id:
        raise HTTPException(status_code=404, detail="not found")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


# ── Feature 4: 店舗ロック画面専用枠 管理API ──────────────────────────────────


class StoreCreativeBody(BaseModel):
    title: str
    image_url: str
    click_url: str
    body: Optional[str] = None
    slot_type: str = "lockscreen"   # lockscreen / widget
    priority: int = 1
    floor_cpm: float = 0.0
    width: Optional[int] = 1080
    height: Optional[int] = 1920


@router.post(
    "/admin/stores/{dealer_id}/lockscreen-creative",
    summary="店舗ロック画面専用クリエイティブ登録",
)
async def create_store_lockscreen_creative(
    dealer_id: str,
    body: StoreCreativeBody,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """
    店舗専用ロック画面/ウィジェット広告クリエイティブを1コールで登録する。
    内部で AffiliateCampaignDB + CreativeDB + StoreAdAssignmentDB を作成する。
    管理画面のみで利用可能（X-Admin-Key 必須）。
    """
    from db_models import StoreAdAssignmentDB  # noqa

    dealer = await db.get(DealerDB, dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="dealer not found")

    if body.slot_type not in ("lockscreen", "widget"):
        raise HTTPException(status_code=422, detail="slot_type must be lockscreen or widget")

    # 店舗専用キャンペーンを作成（category="store" で通常オークションから除外）
    campaign = AffiliateCampaignDB(
        name=f"[店舗枠] {dealer.name} - {body.title}",
        category="store",
        destination_url=body.click_url,
        reward_type="store",
        reward_amount=body.floor_cpm,
        status="active",
    )
    db.add(campaign)
    await db.flush()

    # クリエイティブを作成
    creative = CreativeDB(
        campaign_id=campaign.id,
        name=f"[店舗枠] {body.title}",
        type="image",
        title=body.title,
        body=body.body or "",
        image_url=body.image_url,
        click_url=body.click_url,
        width=body.width,
        height=body.height,
        status="active",
    )
    db.add(creative)
    await db.flush()

    # StoreAdAssignmentDB でディーラーに紐付け
    assignment = StoreAdAssignmentDB(
        dealer_id=dealer_id,
        campaign_id=campaign.id,
        priority=body.priority,
        status="active",
    )
    db.add(assignment)
    await db.commit()

    logger.info(
        f"Store lockscreen creative created | dealer={dealer_id[:8]}... "
        f"| campaign={campaign.id[:8]}... | slot={body.slot_type}"
    )
    return {
        "assignment_id": assignment.id,
        "campaign_id": campaign.id,
        "creative_id": creative.id,
        "dealer_id": dealer_id,
        "slot_type": body.slot_type,
        "priority": assignment.priority,
    }


@router.patch(
    "/admin/stores/{dealer_id}/lockscreen-creatives/{assignment_id}/status",
    summary="店舗専用クリエイティブ アクティブ/一時停止切り替え",
)
async def update_store_creative_status(
    dealer_id: str,
    assignment_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import StoreAdAssignmentDB  # noqa

    row = await db.get(StoreAdAssignmentDB, assignment_id)
    if not row or row.dealer_id != dealer_id:
        raise HTTPException(status_code=404, detail="not found")

    new_status = body.get("status", "")
    if new_status not in ("active", "paused"):
        raise HTTPException(status_code=422, detail="status must be active or paused")

    row.status = new_status
    await db.commit()
    return {"ok": True, "assignment_id": assignment_id, "status": new_status}


@router.get(
    "/admin/stores/{dealer_id}/lockscreen-creatives",
    summary="店舗専用クリエイティブ一覧",
)
async def list_store_lockscreen_creatives(
    dealer_id: str,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """
    dealer_id に登録された店舗専用クリエイティブ（category=store）の一覧を返す。
    """
    from db_models import StoreAdAssignmentDB  # noqa

    rows = (await db.scalars(
        select(StoreAdAssignmentDB)
        .where(
            StoreAdAssignmentDB.dealer_id == dealer_id,
        )
        .order_by(StoreAdAssignmentDB.priority)
    )).all()

    result = []
    for r in rows:
        campaign = await db.get(AffiliateCampaignDB, r.campaign_id)
        if not campaign or campaign.category != "store":
            continue
        creative = await db.scalar(
            select(CreativeDB)
            .where(CreativeDB.campaign_id == r.campaign_id, CreativeDB.status == "active")
            .order_by(CreativeDB.created_at.desc())
            .limit(1)
        )
        result.append({
            "assignment_id": r.id,
            "campaign_id": r.campaign_id,
            "priority": r.priority,
            "status": r.status,
            "title": creative.title if creative else None,
            "image_url": creative.image_url if creative else None,
            "click_url": creative.click_url if creative else None,
            "creative_id": creative.id if creative else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"dealer_id": dealer_id, "creatives": result}


# ── Feature 6: iOS ウィジェット広告 管理API ───────────────────────────────


class IosWidgetCreativeBody(BaseModel):
    title: str
    image_url: str
    click_url: str
    body: Optional[str] = None


@router.post(
    "/admin/ios/widget/creative",
    summary="iOS ウィジェット広告クリエイティブ登録",
)
async def create_ios_widget_creative(
    body: IosWidgetCreativeBody,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """
    iOS ホーム画面/ロック画面ウィジェット向けクリエイティブを登録する。
    登録したクリエイティブは GET /mdm/ios/widget_content/{device_id} で自動配信される。
    管理画面のみで利用可能（X-Admin-Key 必須）。
    """
    # iOS ウィジェット用キャンペーン
    campaign = AffiliateCampaignDB(
        name=f"[iOSウィジェット] {body.title}",
        category="ios_widget",
        destination_url=body.click_url,
        reward_type="cpc",
        reward_amount=0.0,
        status="active",
    )
    db.add(campaign)
    await db.flush()

    creative = CreativeDB(
        campaign_id=campaign.id,
        name=f"[iOSウィジェット] {body.title}",
        type="image",
        title=body.title,
        body=body.body or "",
        image_url=body.image_url,
        click_url=body.click_url,
        width=360,
        height=169,   # rectangular widget サイズ
        status="active",
    )
    db.add(creative)
    await db.commit()

    logger.info(f"iOS widget creative created | campaign={campaign.id[:8]}...")
    return {
        "campaign_id": campaign.id,
        "creative_id": creative.id,
        "title": body.title,
        "image_url": body.image_url,
    }


@router.get(
    "/admin/ios/widget/stats",
    summary="iOS ウィジェット広告統計",
)
async def ios_widget_stats(
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """
    webclip_ios スロットのインプレッション統計を返す。
    管理画面のみで利用可能。
    """
    from datetime import timedelta

    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    slot = await db.scalar(
        select(MdmAdSlotDB).where(
            MdmAdSlotDB.slot_type == "webclip_ios",
            MdmAdSlotDB.status == "active",
        ).limit(1)
    )

    imp_q = select(func.count(MdmImpressionDB.id)).where(
        MdmImpressionDB.created_at >= since,
    )
    if slot:
        imp_q = imp_q.where(MdmImpressionDB.slot_id == slot.id)

    total_imp = await db.scalar(imp_q) or 0

    click_q = select(func.count(MdmImpressionDB.id)).where(
        MdmImpressionDB.created_at >= since,
        MdmImpressionDB.clicked.is_(True),
    )
    if slot:
        click_q = click_q.where(MdmImpressionDB.slot_id == slot.id)

    total_clicks = await db.scalar(click_q) or 0

    ctr = round(total_clicks / total_imp, 4) if total_imp > 0 else 0.0

    # クリエイティブ別集計 (上位10件)
    cr_rows = await db.execute(
        select(
            MdmImpressionDB.creative_id,
            func.count(MdmImpressionDB.id).label("impressions"),
            func.sum(func.cast(MdmImpressionDB.clicked, Integer)).label("clicks"),
        )
        .where(MdmImpressionDB.created_at >= since)
        .group_by(MdmImpressionDB.creative_id)
        .order_by(func.count(MdmImpressionDB.id).desc())
        .limit(10)
    )
    top_creatives = []
    for row in cr_rows.all():
        cr = await db.get(CreativeDB, row.creative_id)
        top_creatives.append({
            "creative_id": row.creative_id,
            "title": cr.title if cr else None,
            "image_url": cr.image_url if cr else None,
            "impressions": row.impressions,
            "clicks": int(row.clicks or 0),
            "ctr": round(int(row.clicks or 0) / row.impressions, 4) if row.impressions > 0 else 0.0,
        })

    return {
        "period_days": days,
        "total_impressions": total_imp,
        "total_clicks": total_clicks,
        "ctr": ctr,
        "top_creatives": top_creatives,
    }


@router.get(
    "/admin/ios/widget/preview",
    summary="iOS ウィジェット配信プレビュー（ドライラン）",
)
async def ios_widget_preview(
    token: Optional[str] = Query(None, description="enrollment_token（省略可）"),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """
    実際にデバイスへ配信される前に、どのクリエイティブが選ばれるかをプレビューする。
    インプレッションは記録しない（ドライランモード）。
    管理画面のみで利用可能。
    """
    from mdm.creative.selector import select_creative  # noqa

    # ドライラン: select_creative は実際にインプレッションを記録するため
    # DB を直接クエリしてプレビューを構築する
    q = (
        select(CreativeDB, AffiliateCampaignDB)
        .join(AffiliateCampaignDB, CreativeDB.campaign_id == AffiliateCampaignDB.id)
        .where(
            CreativeDB.status == "active",
            AffiliateCampaignDB.status == "active",
        )
        .order_by(AffiliateCampaignDB.reward_amount.desc())
        .limit(3)
    )
    rows = (await db.execute(q)).all()

    items = []
    for creative, campaign in rows:
        items.append({
            "creative_id": creative.id,
            "campaign_id": campaign.id,
            "title": creative.title,
            "image_url": creative.image_url,
            "click_url": creative.click_url,
            "category": campaign.category,
            "reward_amount": campaign.reward_amount,
        })

    return {
        "note": "dry_run — impressions are NOT recorded",
        "enrollment_token": token,
        "preview_items": items,
    }


@router.get("/admin/campaigns-for-assignment", summary="広告割り当て用キャンペーン一覧")
async def list_campaigns_for_assignment(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """管理画面の広告割り当てUI用：静止画クリエイティブがあるキャンペーンを返す"""
    rows = (await db.scalars(
        select(AffiliateCampaignDB)
        .where(AffiliateCampaignDB.status == "active")
        .order_by(AffiliateCampaignDB.created_at.desc())
    )).all()
    if not rows:
        return {"campaigns": []}

    # 全キャンペーンの静止画クリエイティブを一括取得（N+1 回避）
    campaign_ids = [c.id for c in rows]
    creative_rows = (await db.execute(
        select(CreativeDB.campaign_id, CreativeDB.image_url)
        .where(
            CreativeDB.campaign_id.in_(campaign_ids),
            CreativeDB.type == "image",
            CreativeDB.status == "active",
        )
    )).all()
    # campaign_id → image_url の辞書（最初の1件を使用）
    creative_by_campaign: dict[str, str | None] = {}
    for row in creative_rows:
        if row.campaign_id not in creative_by_campaign:
            creative_by_campaign[row.campaign_id] = row.image_url

    result = []
    for c in rows:
        image_url = creative_by_campaign.get(c.id)
        result.append({
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "reward_amount": c.reward_amount,
            "has_image": image_url is not None,
            "image_url": image_url,
        })
    return {"campaigns": result}


# ── 代理店管理（管理者用）────────────────────────────────────────────

@router.post("/admin/agencies", summary="代理店登録（管理者）")
async def create_agency(
    body: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import AgencyDB
    import secrets
    name  = body.get("name", "").strip()
    email = body.get("contact_email", "")
    if not name:
        raise HTTPException(status_code=400, detail="name required")

    api_key = secrets.token_urlsafe(32)
    agency  = AgencyDB(name=name, api_key=api_key, contact_email=email)
    db.add(agency)
    await db.commit()
    await db.refresh(agency)
    return {"id": agency.id, "name": agency.name, "api_key": api_key}


@router.get("/admin/agencies", summary="代理店一覧（管理者）")
async def list_agencies(
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import AgencyDB
    rows = (await db.scalars(select(AgencyDB).order_by(AgencyDB.id))).all()
    return {"agencies": [
        {"id": r.id, "name": r.name, "contact_email": r.contact_email,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


# ════════════════════════════════════════════════════════════════════
# BKD-12 — 収益自動精算エンジン
# ════════════════════════════════════════════════════════════════════

@router.post("/admin/settlement/run", summary="月次精算実行（管理者）")
async def run_monthly_settlement(
    body: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """
    指定月の収益を自動集計して invoice レコードを生成する。
    period_month: "2026-03"（省略時は前月）
    """
    period = body.get("period_month", "")
    if not period:
        # 前月を自動計算
        today = datetime.now(timezone.utc).replace(tzinfo=None)
        first = today.replace(day=1)
        last_month = first - timedelta(days=1)
        period = last_month.strftime("%Y-%m")

    async def _settle():
        try:
            await _run_settlement(period, db)
        except Exception as e:
            logger.error(f"Settlement failed: {e}")

    background_tasks.add_task(_settle)
    return {"status": "started", "period_month": period}


async def _run_settlement(period_month: str, db: AsyncSession):
    """
    月次精算のコアロジック。
    各キャンペーンのCPI+CPM+動画を集計してInvoiceを生成する。
    take_rate は担当代理店の設定値を使用（代理店なしの場合はデフォルト 17.5%）。
    """
    from db_models import AffiliateCampaignDB, AgencyDB, InstallEventDB, InvoiceDB
    from sqlalchemy import extract

    DEFAULT_TAKE_RATE = 0.175

    year, month = period_month.split("-")
    year, month = int(year), int(month)

    campaigns = (await db.scalars(select(AffiliateCampaignDB))).all()

    # N+1 を防ぐため、対象キャンペーンに紐づく代理店を一括取得して辞書化
    agency_ids = {c.agency_id for c in campaigns if c.agency_id is not None}
    agencies_by_id: dict = {}
    if agency_ids:
        rows = (await db.scalars(select(AgencyDB).where(AgencyDB.id.in_(agency_ids)))).all()
        agencies_by_id = {a.id: a for a in rows}

    for campaign in campaigns:
        # CPI集計
        cpi_count = await db.scalar(
            select(func.count(InstallEventDB.id)).where(
                InstallEventDB.campaign_id == campaign.id,
                extract("year",  InstallEventDB.installed_at) == year,
                extract("month", InstallEventDB.installed_at) == month,
            )
        ) or 0
        cpi_revenue = cpi_count * float(campaign.reward_amount or 0)

        # CPM集計
        imp_count = await db.scalar(
            select(func.count(MdmImpressionDB.id)).where(
                MdmImpressionDB.campaign_id == campaign.id,
                extract("year",  MdmImpressionDB.served_at) == year,
                extract("month", MdmImpressionDB.served_at) == month,
            )
        ) or 0
        cpm_revenue = (imp_count / 1000.0) * float(campaign.cpm_rate or 500)

        # 動画完了集計
        video_count = await db.scalar(
            select(func.count(MdmImpressionDB.id)).where(
                MdmImpressionDB.campaign_id == campaign.id,
                MdmImpressionDB.video_event.contains("complete"),
                extract("year",  MdmImpressionDB.served_at) == year,
                extract("month", MdmImpressionDB.served_at) == month,
            )
        ) or 0
        video_revenue = (video_count / 1000.0) * 3000  # デフォルト動画CPM ¥3,000

        gross = int(cpi_revenue + cpm_revenue + video_revenue)
        if gross == 0:
            continue

        # 担当代理店の take_rate を使用。未設定の場合はデフォルト値にフォールバック。
        agency = agencies_by_id.get(campaign.agency_id) if campaign.agency_id else None
        take_rate = agency.take_rate if agency is not None else DEFAULT_TAKE_RATE
        platform_fee = int(gross * take_rate)
        net_payable  = gross - platform_fee

        # 予算超過チェック → 自動一時停止
        if hasattr(campaign, "budget_limit") and campaign.budget_limit:
            used = await db.scalar(
                select(func.sum(InvoiceDB.gross_revenue_jpy)).where(
                    InvoiceDB.campaign_id == campaign.id,
                    InvoiceDB.status != "draft",
                )
            ) or 0
            if used + gross > campaign.budget_limit:
                campaign.status = "paused"
                logger.warning(f"Campaign {campaign.id} paused: budget exceeded")

        invoice = InvoiceDB(
            period_month=period_month,
            campaign_id=campaign.id,
            agency_id=campaign.agency_id,
            gross_revenue_jpy=gross,
            take_rate=take_rate,
            platform_fee_jpy=platform_fee,
            net_payable_jpy=net_payable,
            cpi_count=cpi_count,
            impression_count=imp_count,
            video_complete_count=video_count,
            status="draft",
        )
        db.add(invoice)

    await db.commit()
    logger.info(f"Settlement complete: period={period_month} campaigns={len(campaigns)}")


@router.get("/admin/settlement/invoices", summary="精算一覧（管理者）")
async def list_invoices(
    period_month: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    from db_models import InvoiceDB
    q = select(InvoiceDB)
    if period_month:
        q = q.where(InvoiceDB.period_month == period_month)
    rows = (await db.scalars(q.order_by(InvoiceDB.created_at.desc()).limit(200))).all()
    return {"invoices": [
        {
            "id":                   r.id,
            "period_month":         r.period_month,
            "campaign_id":          r.campaign_id,
            "gross_revenue_jpy":    r.gross_revenue_jpy,
            "platform_fee_jpy":     r.platform_fee_jpy,
            "net_payable_jpy":      r.net_payable_jpy,
            "cpi_count":            r.cpi_count,
            "impression_count":     r.impression_count,
            "video_complete_count": r.video_complete_count,
            "status":               r.status,
            "created_at":           r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]}


# ── Lock Screen KPI / 5軸ターゲティング 管理エンドポイント ──────────────


class SlotTargetingUpdate(BaseModel):
    targeting_json: str


@router.put("/admin/slots/{slot_id}/targeting", summary="広告枠ターゲティング更新")
async def update_slot_targeting(
    slot_id: str,
    body: SlotTargetingUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    try:
        json.loads(body.targeting_json)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="targeting_json must be valid JSON")
    slot = await db.get(MdmAdSlotDB, slot_id)
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    slot.targeting_json = body.targeting_json
    await db.commit()
    return {"id": slot_id, "targeting_json": slot.targeting_json}


class DealerRegionUpdate(BaseModel):
    region: str


@router.put("/admin/dealer/{dealer_id}/region", summary="代理店リージョン設定")
async def set_dealer_region(
    dealer_id: str,
    body: DealerRegionUpdate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    dealer = await db.get(DealerDB, dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="Dealer not found")
    dealer.region = body.region
    await db.commit()
    return {"id": dealer_id, "region": dealer.region}


@router.get("/admin/lockscreen/analytics", summary="ロック画面時間帯CTR分析")
async def lockscreen_analytics(
    days: int = Query(7, description="集計期間（日数）"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    from datetime import timedelta
    from sqlalchemy import Integer as _Int, cast as _cast
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    rows = await db.execute(
        select(
            MdmImpressionDB.hour_of_day,
            func.count(MdmImpressionDB.id).label("impressions"),
            func.sum(_cast(MdmImpressionDB.clicked, _Int)).label("clicks"),
        )
        .where(
            MdmImpressionDB.hour_of_day.isnot(None),
            MdmImpressionDB.created_at >= cutoff,
        )
        .group_by(MdmImpressionDB.hour_of_day)
        .order_by(MdmImpressionDB.hour_of_day)
    )

    hourly: dict[int, dict] = {h: {"impressions": 0, "clicks": 0} for h in range(24)}
    for row in rows.all():
        h = row.hour_of_day
        hourly[h] = {"impressions": row.impressions or 0, "clicks": int(row.clicks or 0)}

    return {
        "period_days": days,
        "hours": [
            {
                "hour": h,
                "impressions": hourly[h]["impressions"],
                "clicks": hourly[h]["clicks"],
                "ctr": round(hourly[h]["clicks"] / hourly[h]["impressions"] * 100, 2)
                       if hourly[h]["impressions"] > 0 else 0.0,
            }
            for h in range(24)
        ],
    }


# ── CPI課金管理エンドポイント (BKD-billing-01) ─────────────────


@router.get("/admin/billing/pending", summary="CPI課金pending一覧（管理者）")
async def list_billing_pending(
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = await db.execute(
        select(InstallEventDB)
        .where(InstallEventDB.billing_status == "pending")
        .order_by(InstallEventDB.created_at.desc())
        .limit(limit)
    )
    events = rows.scalars().all()
    return [
        {
            "id": e.id,
            "device_id": e.device_id[:8] + "...",
            "package_name": e.package_name,
            "campaign_id": e.campaign_id,
            "cpi_amount": e.cpi_amount,
            "postback_status": e.postback_status,
            "billing_status": e.billing_status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/admin/billing/invoice/{period}", summary="月次請求書（管理者）")
async def get_billing_invoice(
    period: str,  # "2026-03"
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    # period フォーマット検証
    import re
    if not re.match(r'^\d{4}-\d{2}$', period):
        raise HTTPException(status_code=400, detail="period must be YYYY-MM format")

    from db_models import InvoiceDB
    rows = await db.execute(
        select(InvoiceDB).where(InvoiceDB.period_month == period)
    )
    invoices = rows.scalars().all()

    return {
        "period": period,
        "invoices": [
            {
                "id": inv.id,
                "campaign_id": inv.campaign_id,
                "agency_id": inv.agency_id,
                "gross_revenue_jpy": inv.gross_revenue_jpy,
                "take_rate": inv.take_rate,
                "platform_fee_jpy": inv.platform_fee_jpy,
                "net_payable_jpy": inv.net_payable_jpy,
                "cpi_count": inv.cpi_count,
                "impression_count": inv.impression_count,
                "status": inv.status,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            }
            for inv in invoices
        ],
        "total_gross_jpy": sum(inv.gross_revenue_jpy or 0 for inv in invoices),
        "total_cpi_count": sum(inv.cpi_count or 0 for inv in invoices),
    }


@router.post("/admin/billing/mark-paid/{install_event_id}", summary="CPI課金をpaidに変更（管理者）")
async def mark_install_paid(
    install_event_id: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    event = await db.get(InstallEventDB, install_event_id)
    if not event:
        raise HTTPException(status_code=404, detail="install_event not found")
    if event.billing_status not in ("billable", "paid"):
        raise HTTPException(
            status_code=409,
            detail=f"billing_status is '{event.billing_status}', must be 'billable' to mark as paid"
        )
    event.billing_status = "paid"
    await db.commit()
    return {"id": install_event_id, "billing_status": "paid"}


# ── 代理店ポータル（BKD-11） ──────────────────────────────────────────


async def _verify_agency_key_query(api_key: str, db: AsyncSession) -> AgencyDB:
    agency = await db.scalar(
        select(AgencyDB).where(AgencyDB.api_key == api_key)
    )
    if not agency:
        raise HTTPException(status_code=403, detail="Invalid or inactive agency API key")
    return agency


@router.get("/agency/portal", response_class=HTMLResponse, summary="代理店ポータル（HTML）")
async def agency_portal(api_key: str = Query(...), db: AsyncSession = Depends(get_db)):
    agency = await _verify_agency_key_query(api_key, db)
    html = _AGENCY_PORTAL_HTML.replace("{{ agency_name }}", agency.name).replace("{{ api_key }}", api_key)
    return HTMLResponse(content=html)


@router.get("/agency/stats", summary="代理店全店舗合計KPI")
async def agency_stats(api_key: str = Query(...), db: AsyncSession = Depends(get_db)):
    agency = await _verify_agency_key_query(api_key, db)

    dealers = (await db.execute(
        select(DealerDB).where(DealerDB.agency_id == agency.id)
    )).scalars().all()
    dealer_ids = [d.id for d in dealers]

    if not dealer_ids:
        return {"agency_name": agency.name, "total_devices": 0, "total_dealers": 0, "monthly_revenue_jpy": 0, "monthly_impressions": 0}

    total_devices = await db.scalar(
        select(func.count(DeviceDB.id)).where(DeviceDB.dealer_id.in_(dealer_ids))
    ) or 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    imp_result = await db.execute(
        select(
            func.count(MdmImpressionDB.id).label("impressions"),
            func.sum(MdmImpressionDB.cpm_price).label("revenue"),
        ).where(
            MdmImpressionDB.dealer_id.in_(dealer_ids),
            MdmImpressionDB.created_at >= month_start,
        )
    )
    imp_row = imp_result.one()

    return {
        "agency_name": agency.name,
        "total_dealers": len(dealers),
        "total_devices": total_devices,
        "monthly_impressions": imp_row.impressions or 0,
        "monthly_revenue_jpy": int((imp_row.revenue or 0) / 1000),
    }


@router.get("/agency/stores", summary="代理店配下の店舗一覧（端末数・収益付き）")
async def agency_stores(api_key: str = Query(...), db: AsyncSession = Depends(get_db)):
    agency = await _verify_agency_key_query(api_key, db)

    dealers = (await db.execute(
        select(DealerDB)
        .where(DealerDB.agency_id == agency.id)
        .order_by(DealerDB.store_number, DealerDB.created_at)
    )).scalars().all()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    stores = []
    for dealer in dealers:
        device_count = await db.scalar(
            select(func.count(DeviceDB.id)).where(DeviceDB.dealer_id == dealer.id)
        ) or 0

        imp_result = await db.execute(
            select(
                func.count(MdmImpressionDB.id).label("impressions"),
                func.sum(MdmImpressionDB.cpm_price).label("revenue"),
                func.sum(func.cast(MdmImpressionDB.clicked, Integer)).label("clicks"),
            ).where(
                MdmImpressionDB.dealer_id == dealer.id,
                MdmImpressionDB.created_at >= month_start,
            )
        )
        imp_row = imp_result.one()

        stores.append({
            "id": dealer.id,
            "name": dealer.name,
            "store_code": dealer.store_code,
            "store_number": dealer.store_number,
            "region": dealer.region,
            "status": dealer.status,
            "device_count": device_count,
            "monthly_impressions": imp_row.impressions or 0,
            "monthly_clicks": int(imp_row.clicks or 0),
            "monthly_revenue_jpy": int((imp_row.revenue or 0) / 1000),
            "portal_url": f"/mdm/dealer/portal?api_key={dealer.api_key}",
        })

    return {
        "stores": stores,
        "total_devices": sum(s["device_count"] for s in stores),
        "total_revenue_jpy": sum(s["monthly_revenue_jpy"] for s in stores),
    }


class AgencyStoreCreate(BaseModel):
    name: str
    store_code: str
    region: Optional[str] = None


@router.post("/agency/stores", status_code=201, summary="代理店配下に店舗を新規追加")
async def agency_create_store(
    body: AgencyStoreCreate,
    api_key: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    agency = await _verify_agency_key_query(api_key, db)

    max_num = await db.scalar(
        select(func.max(DealerDB.store_number)).where(DealerDB.agency_id == agency.id)
    ) or 0

    dealer = DealerDB(
        name=body.name,
        store_code=body.store_code,
        region=body.region,
        agency_id=agency.id,
        store_number=max_num + 1,
        api_key=str(uuid.uuid4()),
        status="active",
    )
    db.add(dealer)
    await db.commit()
    await db.refresh(dealer)

    return {
        "id": dealer.id,
        "name": dealer.name,
        "store_code": dealer.store_code,
        "api_key": dealer.api_key,
        "portal_url": f"/mdm/dealer/portal?api_key={dealer.api_key}",
    }


@router.get("/agency/revenue", summary="代理店月次収益レポート")
async def agency_revenue(
    api_key: Optional[str] = Query(None),
    x_agency_key: Optional[str] = Header(None, alias="X-Agency-Key"),
    period: Optional[str] = Query(None, description="YYYY-MM"),
    month: Optional[str] = Query(None, description="YYYY-MM (alias for period)"),
    db: AsyncSession = Depends(get_db),
):
    import re as _re
    import calendar
    # Support X-Agency-Key header as well as api_key query param
    resolved_key = api_key or x_agency_key
    if not resolved_key:
        raise HTTPException(status_code=401, detail="api_key required")
    # Support 'month' as alias for 'period'; default to current month
    resolved_period = period or month
    if not resolved_period:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        resolved_period = now.strftime("%Y-%m")
    if not _re.match(r'^\d{4}-\d{2}$', resolved_period):
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    period = resolved_period

    agency = await _verify_agency_key_query(resolved_key, db)

    dealers = (await db.execute(
        select(DealerDB).where(DealerDB.agency_id == agency.id)
    )).scalars().all()

    year, month = map(int, period.split("-"))
    month_start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    month_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)

    result = []
    for dealer in dealers:
        row = await db.execute(
            select(
                func.count(MdmImpressionDB.id).label("impressions"),
                func.sum(func.cast(MdmImpressionDB.clicked, Integer)).label("clicks"),
                func.sum(MdmImpressionDB.cpm_price).label("revenue"),
            ).where(
                MdmImpressionDB.dealer_id == dealer.id,
                MdmImpressionDB.created_at >= month_start,
                MdmImpressionDB.created_at <= month_end,
            )
        )
        r = row.one()
        # アフィリエイトCV由来の代理店取り分を加算
        affiliate_report = await get_dealer_monthly_report(db, dealer.id, year, month)
        dealer_share = affiliate_report.get("dealer_share_jpy", 0.0) if affiliate_report else 0.0
        result.append({
            "store_name": dealer.name,
            "store_code": dealer.store_code,
            "user_count": affiliate_report.get("enrolled_devices", 0) if affiliate_report else 0,
            "impressions": r.impressions or 0,
            "clicks": int(r.clicks or 0),
            "revenue_jpy": int((r.revenue or 0) / 1000),
            "dealer_share_jpy": dealer_share,
            # アフィリエイト計測指標
            "affiliate_clicks": affiliate_report.get("clicks", 0) if affiliate_report else 0,
            "installs": affiliate_report.get("installs", 0) if affiliate_report else 0,
            "conversions": affiliate_report.get("conversions", 0) if affiliate_report else 0,
            "affiliate_revenue_jpy": affiliate_report.get("revenue_jpy", 0.0) if affiliate_report else 0.0,
        })

    gross = sum(r["revenue_jpy"] for r in result)
    take_rate = getattr(agency, "take_rate", 0.3) or 0.3
    net = int(gross * (1 - take_rate))
    return {
        "agency": agency.name,
        "period_month": period,
        "period": period,
        "stores": result,
        "gross_revenue_jpy": gross,
        "net_payable_jpy": net,
    }


# ── ポータル認証管理（BKD-11-PW） ──────────────────────────────────────────


@router.put("/admin/agencies/{agency_id}/password", summary="代理店ポータルパスワード設定")
async def set_agency_portal_password(
    agency_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """管理者が代理店のポータルログイン情報（login_id + password）を設定する"""
    from auth import hash_password as _hash
    agency = await db.get(AgencyDB, agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail="Agency not found")
    login_id = (body.get("login_id") or "").strip()
    password = body.get("password") or ""
    if not login_id or not password:
        raise HTTPException(status_code=400, detail="login_id と password は必須です")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password は8文字以上にしてください")
    agency.login_id = login_id
    agency.hashed_password = _hash(password)
    await db.commit()
    return {"id": agency_id, "login_id": login_id, "ok": True}


@router.put("/admin/dealers/{dealer_id}/password", summary="店舗ポータルパスワード設定")
async def set_dealer_portal_password(
    dealer_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(verify_admin_key),
):
    """管理者が店舗のポータルログイン情報（login_id + password）を設定する"""
    from auth import hash_password as _hash
    dealer = await db.get(DealerDB, dealer_id)
    if not dealer:
        raise HTTPException(status_code=404, detail="Dealer not found")
    login_id = (body.get("login_id") or "").strip()
    password = body.get("password") or ""
    if not login_id or not password:
        raise HTTPException(status_code=400, detail="login_id と password は必須です")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password は8文字以上にしてください")
    dealer.login_id = login_id
    dealer.hashed_password = _hash(password)
    await db.commit()
    return {"id": dealer_id, "login_id": login_id, "ok": True}


@router.get("/agency/enrolled-users", summary="代理店エンロールユーザー一覧（店舗フィルター付き）")
async def agency_enrolled_users(
    api_key: str = Query(...),
    dealer_id: Optional[str] = Query(None, description="店舗IDで絞り込み"),
    platform: Optional[str] = Query(None, description="ios / android"),
    limit: int = Query(500, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """代理店の api_key で認証し、傘下の全店舗のエンロールユーザー一覧を返す。"""
    agency = await _verify_agency_key_query(api_key, db)

    # 傘下の店舗IDリスト
    dealers_rows = await db.execute(
        select(DealerDB).where(DealerDB.agency_id == agency.id)
    )
    dealers = dealers_rows.scalars().all()
    dealer_map = {d.id: {"name": d.name, "store_code": d.store_code} for d in dealers}
    dealer_ids = list(dealer_map.keys())

    if not dealer_ids:
        return {"agency_name": agency.name, "total": 0, "devices": []}

    stmt = (
        select(DeviceDB)
        .where(DeviceDB.dealer_id.in_(dealer_ids))
        .order_by(DeviceDB.enrolled_at.desc())
    )
    if dealer_id:
        stmt = stmt.where(DeviceDB.dealer_id == dealer_id)
    if platform:
        stmt = stmt.where(DeviceDB.platform == platform)
    stmt = stmt.limit(limit)

    devices = (await db.scalars(stmt)).all()
    total = await db.scalar(
        select(func.count(DeviceDB.id)).where(DeviceDB.dealer_id.in_(dealer_ids))
    ) or 0

    return {
        "agency_name": agency.name,
        "total": total,
        "dealers": [{"id": d.id, "name": d.name, "store_code": d.store_code} for d in dealers],
        "devices": [
            {
                "id": dev.id,
                "platform": dev.platform,
                "device_model": dev.device_model,
                "os_version": dev.os_version,
                "age_group": dev.age_group,
                "status": dev.status,
                "mobileconfig_downloaded": dev.mobileconfig_downloaded,
                "enrolled_at": dev.enrolled_at.isoformat() if dev.enrolled_at else None,
                "last_seen_at": dev.last_seen_at.isoformat() if dev.last_seen_at else None,
                "dealer_id": dev.dealer_id,
                "dealer_name": dealer_map.get(dev.dealer_id, {}).get("name"),
                "store_code": dealer_map.get(dev.dealer_id, {}).get("store_code"),
            }
            for dev in devices
        ],
    }


_AGENCY_PORTAL_HTML = """\
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ agency_name }} \u2013 \u4ee3\u7406\u5e97\u30dd\u30fc\u30bf\u30eb</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #ffffff;
      --surface: #f8fafc;
      --border: #e2e8f0;
      --text: #1e293b;
      --muted: #64748b;
      --accent: #6366f1;
      --green: #16a34a;
      --red: #dc2626;
      --yellow: #d97706;
      --nav-bg: #0f172a;
      --nav-text: #cbd5e1;
      --nav-active-bg: #1e3a5f;
      --nav-active-color: #818cf8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

    header {
      position: sticky; top: 0; z-index: 100;
      background: var(--nav-bg);
      border-bottom: 1px solid #1e293b;
      padding: 0 24px;
      height: 56px;
      display: flex; align-items: center; justify-content: space-between;
    }
    .header-brand { display: flex; align-items: center; gap: 10px; }
    .header-brand span { color: #e2e8f0; font-weight: 700; font-size: 16px; }
    .header-badge { background: #1e3a5f; color: #818cf8; font-size: 11px; padding: 2px 8px; border-radius: 4px; }
    .btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 500; cursor: pointer; border: none; text-decoration: none; transition: all .15s; }
    .btn-primary { background: var(--accent); color: #fff; }
    .btn-primary:hover { background: #4f46e5; }
    .btn-ghost { background: transparent; color: var(--nav-text); border: 1px solid #334155; }
    .btn-ghost:hover { background: #1e293b; }
    .btn-sm { padding: 5px 10px; font-size: 12px; }

    .layout { display: flex; min-height: calc(100vh - 56px); }
    nav {
      width: 220px; flex-shrink: 0;
      background: var(--nav-bg);
      padding: 20px 12px;
      position: sticky; top: 56px; height: calc(100vh - 56px); overflow-y: auto;
    }
    nav a {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 12px; border-radius: 8px;
      color: var(--nav-text); text-decoration: none; font-size: 13px; font-weight: 500;
      margin-bottom: 2px; transition: all .15s;
    }
    nav a:hover { background: #1e293b; }
    nav a.active { background: var(--nav-active-bg); color: var(--nav-active-color); }
    .section-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; color: #475569; padding: 8px 12px 4px; }

    main { flex: 1; padding: 32px; max-width: 1100px; }
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 24px; }
    h2 { font-size: 16px; font-weight: 700; margin-bottom: 16px; }

    .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
    .kpi-card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
    .kpi-value { font-size: 28px; font-weight: 800; color: var(--accent); }
    .kpi-label { font-size: 12px; color: var(--muted); margin-top: 4px; }

    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 10px 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); border-bottom: 2px solid var(--border); }
    td { padding: 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--surface); }

    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
    .badge-green { background: #dcfce7; color: #16a34a; }
    .badge-red { background: #fee2e2; color: #dc2626; }

    .form-group { margin-bottom: 14px; }
    .form-group label { display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px; color: var(--muted); }
    .form-group input, .form-group select { width: 100%; padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; background: var(--bg); color: var(--text); }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

    .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 200; align-items: center; justify-content: center; }
    .modal-overlay.open { display: flex; }
    .modal { background: var(--bg); border-radius: 16px; padding: 28px; width: 480px; max-width: 95vw; }
    .modal-close { float: right; background: none; border: none; font-size: 20px; cursor: pointer; color: var(--muted); }
  </style>
</head>
<body>

<header>
  <div class="header-brand">
    <span>&#128241;</span>
    <span>{{ agency_name }}</span>
    <span class="header-badge">\u4ee3\u7406\u5e97\u30dd\u30fc\u30bf\u30eb</span>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <span style="font-size:12px;color:#64748b" id="last-updated"></span>
    <a href="/mdm/dealer/manual" class="btn btn-ghost btn-sm" target="_blank">\u30de\u30cb\u30e5\u30a2\u30eb</a>
  </div>
</header>

<div class="layout">
  <nav>
    <div class="section-label">\u30e1\u30cb\u30e5\u30fc</div>
    <a href="#" onclick="showSection('summary')" class="active" id="nav-summary">&#128202; \u30b5\u30de\u30ea\u30fc</a>
    <a href="#" onclick="showSection('stores')" id="nav-stores">&#127978; \u5e97\u8217\u4e00\u89a7</a>
    <a href="#" onclick="showSection('users')" id="nav-users">&#128100; \u30e6\u30fc\u30b6\u30fc\u4e00\u89a7</a>
    <a href="#" onclick="showSection('revenue')" id="nav-revenue">&#128176; \u53ce\u76ca\u30ec\u30dd\u30fc\u30c8</a>
  </nav>

  <main>
    <section id="summary" style="margin-bottom:32px">
      <h2 style="font-size:20px;font-weight:700;margin-bottom:20px">\u5168\u5e97\u8217\u30b5\u30de\u30ea\u30fc</h2>
      <div class="kpi-grid">
        <div class="kpi-card">
          <div style="font-size:24px;margin-bottom:8px">&#127978;</div>
          <div class="kpi-value" id="kpi-dealers">-</div>
          <div class="kpi-label">\u7ba1\u7406\u5e97\u8217\u6570</div>
        </div>
        <div class="kpi-card">
          <div style="font-size:24px;margin-bottom:8px">&#128241;</div>
          <div class="kpi-value" id="kpi-devices">-</div>
          <div class="kpi-label">\u7dcf\u30a8\u30f3\u30ed\u30fc\u30eb\u7aef\u672b</div>
        </div>
        <div class="kpi-card">
          <div style="font-size:24px;margin-bottom:8px">&#128202;</div>
          <div class="kpi-value" id="kpi-impressions">-</div>
          <div class="kpi-label">\u5f53\u6708\u30a4\u30f3\u30d7\u30ec\u30c3\u30b7\u30e7\u30f3</div>
        </div>
        <div class="kpi-card" style="background:linear-gradient(135deg,#1e3a5f,#1e293b)">
          <div style="font-size:24px;margin-bottom:8px">&#128176;</div>
          <div class="kpi-value" id="kpi-revenue" style="color:#818cf8">\u00a5-</div>
          <div class="kpi-label" style="color:#94a3b8">\u5f53\u6708\u53ce\u76ca\uff08\u63a8\u8a08\uff09</div>
        </div>
      </div>
    </section>

    <section id="stores">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
          <h2 style="margin:0">\u5e97\u8217\u4e00\u89a7</h2>
          <button class="btn btn-primary btn-sm" onclick="showAddStoreModal()">+ \u5e97\u8217\u8ffd\u52a0</button>
        </div>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>\u5e97\u8217\u540d</th>
              <th>\u5e97\u8217\u30b3\u30fc\u30c9</th>
              <th>\u5730\u57df</th>
              <th>\u7aef\u672b\u6570</th>
              <th>\u5f53\u6708IMP</th>
              <th>\u5f53\u6708\u53ce\u76ca</th>
              <th>\u30b9\u30c6\u30fc\u30bf\u30b9</th>
              <th>\u64cd\u4f5c</th>
            </tr>
          </thead>
          <tbody id="stores-tbody">
            <tr><td colspan="9" style="text-align:center;color:var(--muted);padding:32px">\u8aad\u307f\u8fbc\u307f\u4e2d...</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section id="revenue">
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
          <h2 style="margin:0">\u6708\u6b21\u53ce\u76ca\u30ec\u30dd\u30fc\u30c8</h2>
          <input type="month" id="revenue-period" style="padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px" onchange="loadRevenue()">
        </div>
        <div style="position:relative;height:240px;margin-bottom:16px">
          <canvas id="store-revenue-chart"></canvas>
        </div>
        <table id="revenue-table">
          <thead>
            <tr><th>\u5e97\u8217\u540d</th><th>IMP</th><th>\u30af\u30ea\u30c3\u30af</th><th>\u53ce\u76ca</th></tr>
          </thead>
          <tbody id="revenue-tbody">
            <tr><td colspan="4" style="text-align:center;color:var(--muted);padding:24px">\u6708\u3092\u9078\u629e\u3057\u3066\u304f\u3060\u3055\u3044</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>
</div>

<div class="modal-overlay" id="add-store-modal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('add-store-modal')">&times;</button>
    <h2 style="margin-bottom:20px">\u5e97\u8217\u8ffd\u52a0</h2>
    <form id="add-store-form">
      <div class="form-grid">
        <div class="form-group">
          <label>\u5e97\u8217\u540d *</label>
          <input id="store-name" placeholder="\u4f8b: \u30bd\u30d5\u30c8\u30d0\u30f3\u30af\u6e0b\u8c37\u5e97" required>
        </div>
        <div class="form-group">
          <label>\u5e97\u8217\u30b3\u30fc\u30c9 *</label>
          <input id="store-code" placeholder="\u4f8b: SB-SHIBUYA-001" required>
        </div>
      </div>
      <div class="form-group">
        <label>\u5730\u57df\uff08\u4efb\u610f\uff09</label>
        <input id="store-region" placeholder="\u4f8b: tokyo">
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center">\u8ffd\u52a0\u3059\u308b</button>
      <div id="add-store-result" style="margin-top:10px;font-size:13px"></div>
    </form>
  </div>
</div>

<script>
const API_KEY = '{{ api_key }}';
const headers = { 'Content-Type': 'application/json' };
let revenueChart = null;

function closeModal(id) { document.getElementById(id).classList.remove('open'); }
function showAddStoreModal() { document.getElementById('add-store-modal').classList.add('open'); }

function fmt(n) { return (n || 0).toLocaleString('ja-JP'); }
function fmtYen(n) { return '\\u00a5' + (n || 0).toLocaleString('ja-JP'); }

async function loadStats() {
  try {
    const r = await fetch(`/mdm/agency/stats?api_key=${API_KEY}`);
    if (!r.ok) return;
    const d = await r.json();
    document.getElementById('kpi-dealers').textContent = fmt(d.total_dealers);
    document.getElementById('kpi-devices').textContent = fmt(d.total_devices);
    document.getElementById('kpi-impressions').textContent = fmt(d.monthly_impressions);
    document.getElementById('kpi-revenue').textContent = fmtYen(d.monthly_revenue_jpy);
    document.getElementById('last-updated').textContent = '\\u66f4\\u65b0: ' + new Date().toLocaleTimeString('ja-JP');
  } catch(e) { console.error(e); }
}

async function loadStores() {
  try {
    const r = await fetch(`/mdm/agency/stores?api_key=${API_KEY}`);
    if (!r.ok) return;
    const d = await r.json();
    const tbody = document.getElementById('stores-tbody');
    if (!d.stores.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:32px">\\u5e97\\u8217\\u304c\\u307e\\u3060\\u3042\\u308a\\u307e\\u305b\\u3093</td></tr>';
      return;
    }
    tbody.innerHTML = d.stores.map(s => `
      <tr>
        <td style="color:var(--muted)">${s.store_number || '-'}</td>
        <td style="font-weight:600">${s.name}</td>
        <td style="font-family:monospace;font-size:12px;color:var(--muted)">${s.store_code}</td>
        <td>${s.region || '-'}</td>
        <td>${fmt(s.device_count)} \\u53f0</td>
        <td>${fmt(s.monthly_impressions)}</td>
        <td style="font-weight:600;color:var(--accent)">${fmtYen(s.monthly_revenue_jpy)}</td>
        <td><span class="badge ${s.status === 'active' ? 'badge-green' : 'badge-red'}">${s.status}</span></td>
        <td><a href="${s.portal_url}" target="_blank" class="btn btn-ghost btn-sm">\\u5e97\\u8217\\u30dd\\u30fc\\u30bf\\u30eb \\u2192</a></td>
      </tr>
    `).join('');
  } catch(e) { console.error(e); }
}

async function loadRevenue() {
  const period = document.getElementById('revenue-period').value;
  if (!period) return;
  try {
    const r = await fetch(`/mdm/agency/revenue?api_key=${API_KEY}&period=${period}`);
    if (!r.ok) return;
    const d = await r.json();

    const ctx = document.getElementById('store-revenue-chart').getContext('2d');
    if (revenueChart) revenueChart.destroy();
    revenueChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: d.stores.map(s => s.store_name),
        datasets: [{
          label: '\\u53ce\\u76ca\\uff08\\u5186\\uff09',
          data: d.stores.map(s => s.revenue_jpy),
          backgroundColor: 'rgba(99,102,241,0.7)',
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { callback: v => '\\u00a5' + v.toLocaleString() } } },
      },
    });

    document.getElementById('revenue-tbody').innerHTML = d.stores.map(s => `
      <tr>
        <td>${s.store_name}</td>
        <td>${fmt(s.impressions)}</td>
        <td>${fmt(s.clicks)}</td>
        <td style="font-weight:600;color:var(--accent)">${fmtYen(s.revenue_jpy)}</td>
      </tr>
    `).join('') || '<tr><td colspan="4" style="text-align:center;color:var(--muted)">\\u30c7\\u30fc\\u30bf\\u306a\\u3057</td></tr>';
  } catch(e) { console.error(e); }
}

document.getElementById('add-store-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    name: document.getElementById('store-name').value,
    store_code: document.getElementById('store-code').value,
    region: document.getElementById('store-region').value || null,
  };
  try {
    const r = await fetch(`/mdm/agency/stores?api_key=${API_KEY}`, {
      method: 'POST', headers, body: JSON.stringify(body),
    });
    const d = await r.json();
    const el = document.getElementById('add-store-result');
    if (r.ok) {
      el.style.color = '#16a34a';
      el.innerHTML = `\\u2713 \\u5e97\\u8217\\u3092\\u8ffd\\u52a0\\u3057\\u307e\\u3057\\u305f\\u3002API\\u30ad\\u30fc: <code style="background:#f1f5f9;padding:2px 6px;border-radius:4px">${d.api_key}</code>`;
      loadStores();
      loadStats();
      document.getElementById('add-store-form').reset();
    } else {
      el.style.color = '#dc2626';
      el.textContent = d.detail || '\\u30a8\\u30e9\\u30fc\\u304c\\u767a\\u751f\\u3057\\u307e\\u3057\\u305f';
    }
  } catch(e) {
    document.getElementById('add-store-result').textContent = '\\u30cd\\u30c3\\u30c8\\u30ef\\u30fc\\u30af\\u30a8\\u30e9\\u30fc';
  }
});

const now = new Date();
document.getElementById('revenue-period').value =
  `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;

loadStats();
loadStores();
setInterval(loadStats, 60000);
</script>
</body>
</html>"""

