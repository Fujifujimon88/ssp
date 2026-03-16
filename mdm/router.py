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
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import verify_admin_key
from config import settings
from database import get_db
from db_models import (
    AffiliateCampaignDB, AffiliateClickDB, AffiliateConversionDB,
    AndroidCommandDB, AndroidDeviceDB,
    CampaignDB, ConsentLogDB, CreativeDB, DealerDB, DeviceDB,
    MDMCommandDB, MdmAdSlotDB, MdmImpressionDB, iOSDeviceDB,
)
from mdm.creative.selector import record_click, select_creative
from mdm.affiliate.billing import (
    calculate_monthly_revenue, get_all_dealers_report, get_dealer_monthly_report,
)
from mdm.affiliate.tracking import (
    build_tracked_url, send_adjust_postback, send_appsflyer_postback,
)
from mdm.measurement.gtm import build_lp_html
from mdm.android.commands import (
    acknowledge_command, enqueue_command, get_pending_commands, update_device_last_seen,
)
from mdm.android.fcm import send_command_ping, send_notification
from mdm.enrollment.mobileconfig import MDMConfig, VPNConfig, WebClipConfig, generate_mobileconfig
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
        var data = await res.json();
        if (data.mobileconfig_url) {{
          btn.textContent = "ダウンロード中...";
          window.location.href = data.mobileconfig_url;
          setTimeout(function() {{
            window.location.href = data.line_add_friend_url || "/";
          }}, 2500);
        }}
      }} catch(err) {{
        btn.textContent = "同意してダウンロード";
        btn.classList.remove("btn-disabled");
        btn.classList.add("btn-primary");
        alert("エラーが発生しました。もう一度お試しください。");
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
        var data = await res.json();
        if (data.android_apk_url) {{
          btn.textContent = "ダウンロード中...";
          // DPC APKダウンロード開始
          window.location.href = data.android_apk_url;
          setTimeout(function() {{
            window.location.href = data.line_add_friend_url || "/";
          }}, 3000);
        }}
      }} catch(err) {{
        btn.textContent = "同意してセットアップ";
        btn.classList.remove("btn-disabled");
        btn.classList.add("btn-primary");
        alert("エラーが発生しました。もう一度お試しください。");
      }}
      return false;
    }}
  </script>
</body>
</html>"""


# ── エンドポイント ─────────────────────────────────────────────

@router.get("/portal", response_class=HTMLResponse, summary="エンロールポータル")
async def enrollment_portal(
    dealer: Optional[str] = Query(None),
    campaign: Optional[str] = Query(None),
):
    html = PORTAL_HTML.format(
        dealer_id=dealer or "",
        campaign_id=campaign or "",
        base_url=settings.ssp_endpoint.rstrip("/"),
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

    logger.info(f"MDM consent | token={device.enrollment_token[:8]}... | platform={platform} | dealer={dealer_id}")

    base = settings.ssp_endpoint.rstrip("/")
    token = device.enrollment_token
    return {
        "enrollment_token": token,
        "mobileconfig_url": f"{base}/mdm/ios/mobileconfig?token={token}",
        "android_apk_url": f"{base}/mdm/android/dpc.apk?token={token}",
        "line_add_friend_url": f"{base}/mdm/line/add-friend?token={token}",
    }


@router.get("/ios/mobileconfig", summary="iOS .mobileconfig ダウンロード")
async def download_mobileconfig(
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

    config_bytes = generate_mobileconfig(
        profile_name=profile_name,
        enrollment_token=token,
        vpn=vpn,
        webclips=webclips or None,
    )

    # ダウンロード記録
    device.mobileconfig_downloaded = True
    device.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(f"MDM mobileconfig downloaded | token={token[:8]}...")

    # LINE友だち追加URLをX-Next-Urlヘッダーで返す（JSがリダイレクト）
    base = settings.ssp_endpoint.rstrip("/")
    next_url = f"{base}/mdm/line/add-friend?token={token}"

    return Response(
        content=config_bytes,
        media_type="application/x-apple-aspen-config",
        headers={
            "Content-Disposition": 'attachment; filename="config.mobileconfig"',
            "X-Next-Url": next_url,
        },
    )


@router.get("/qr/{store_code}", summary="店舗別エンロールQRコード（PNG）")
async def enrollment_qr(
    store_code: str,
    campaign: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    dealer = await db.scalar(select(DealerDB).where(DealerDB.store_code == store_code))
    if not dealer:
        raise HTTPException(status_code=404, detail="Dealer not found")

    base = settings.ssp_endpoint.rstrip("/")
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

    device.status = "inactive"
    await db.commit()

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
    return [{"id": d.id, "name": d.name, "store_code": d.store_code, "status": d.status} for d in dealers]


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
        eru_nage_scenario_id=body.eru_nage_scenario_id,
        line_liff_url=body.line_liff_url,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return {"id": campaign.id, "name": campaign.name}


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
    <a href="/" class="btn btn-skip">スキップ</a>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


# ── アフィリエイト ─────────────────────────────────────────────

@router.get("/affiliate/click/{campaign_id}", summary="アフィリエイトクリック追跡")
async def affiliate_click(
    campaign_id: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
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

    logger.info(f"Affiliate click | campaign={campaign_id[:8]}... | token={str(token)[:8]}...")
    return RedirectResponse(url=campaign.destination_url, status_code=302)


@router.post("/affiliate/postback/appsflyer", summary="AppsFlyer S2S Postbackを受信")
async def appsflyer_postback(request: Request, db: AsyncSession = Depends(get_db)):
    """AppsFlyerからのインストール通知を受信してCV記録する"""
    body = await request.json()
    click_token = body.get("af_customer_user_id") or body.get("customer_user_id")
    app_id = body.get("app_id", "")

    campaign = await db.scalar(
        select(AffiliateCampaignDB)
        .where(AffiliateCampaignDB.appsflyer_dev_key != None)
        .limit(1)
    )

    conversion = AffiliateConversionDB(
        click_token=click_token,
        campaign_id=campaign.id if campaign else "unknown",
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

    conversion = AffiliateConversionDB(
        click_token=click_token,
        campaign_id=body.get("app_token", "unknown"),
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


class AffiliateCampaignCreate(BaseModel):
    name: str
    category: str = "app"
    destination_url: str
    reward_type: str = "cpi"
    reward_amount: float = 0.0
    appsflyer_dev_key: Optional[str] = None
    adjust_app_token: Optional[str] = None
    gtm_container_id: Optional[str] = None


@router.post("/admin/affiliate/campaigns", summary="アフィリエイト案件登録（管理者）")
async def create_affiliate_campaign(
    body: AffiliateCampaignCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    campaign = AffiliateCampaignDB(**body.model_dump())
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return {"id": campaign.id, "name": campaign.name, "tracked_url_example": build_tracked_url(campaign.id, "EXAMPLE_TOKEN")}


@router.get("/admin/affiliate/campaigns", summary="アフィリエイト案件一覧（管理者）")
async def list_affiliate_campaigns(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    rows = await db.execute(select(AffiliateCampaignDB).order_by(AffiliateCampaignDB.created_at.desc()))
    campaigns = rows.scalars().all()
    return [{"id": c.id, "name": c.name, "category": c.category, "reward_type": c.reward_type, "reward_amount": c.reward_amount} for c in campaigns]


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


# ── Android MDM API ───────────────────────────────────────────


class AndroidRegisterBody(BaseModel):
    device_id: str         # Android ID（Settings.Secure.ANDROID_ID）
    enrollment_token: Optional[str] = None
    fcm_token: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    android_version: Optional[str] = None
    sdk_int: Optional[int] = None


@router.post("/android/register", summary="Android DPCデバイス登録")
async def android_register(body: AndroidRegisterBody, db: AsyncSession = Depends(get_db)):
    """
    DPC APKが初回起動時に呼び出す。
    デバイス情報とFCMトークンを登録してコマンドキューの準備をする。
    """
    existing = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.device_id)
    )

    if existing:
        # FCMトークン更新
        existing.fcm_token = body.fcm_token or existing.fcm_token
        existing.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"Android device updated | device={body.device_id[:8]}...")
        return {"status": "updated", "device_id": body.device_id}

    device = AndroidDeviceDB(
        device_id=body.device_id,
        enrollment_token=body.enrollment_token,
        fcm_token=body.fcm_token,
        manufacturer=body.manufacturer,
        model=body.model,
        android_version=body.android_version,
        sdk_int=body.sdk_int,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(device)

    # DeviceDBのstatusもactiveに更新
    if body.enrollment_token:
        portal_device = await db.scalar(
            select(DeviceDB).where(DeviceDB.enrollment_token == body.enrollment_token)
        )
        if portal_device:
            portal_device.status = "active"
            portal_device.last_seen_at = datetime.now(timezone.utc)

    await db.commit()
    logger.info(f"Android device registered | device={body.device_id[:8]}... | model={body.model}")
    return {"status": "registered", "device_id": body.device_id}


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


@router.get("/android/lockscreen/content", summary="ロック画面広告コンテンツ取得")
async def lockscreen_content(
    device_id: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
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
    return {
        "impression_id": None,
        "content": {
            "campaign_id": campaign.id,
            "type": "text",
            "title": campaign.name,
            "click_url": build_tracked_url(campaign.id, device_id or "anonymous"),
            "category": campaign.category,
        },
    }


@router.get("/android/widget/content", summary="ホーム画面ウィジェットコンテンツ取得")
async def widget_content(
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
    return {
        "items": [
            {
                "campaign_id": c.id,
                "type": "text",
                "title": c.name,
                "click_url": build_tracked_url(c.id, device_id or "anonymous"),
            }
            for c in campaigns
        ]
    }


@router.get("/android/dpc.apk", summary="DPC APKダウンロード（プレースホルダー）")
async def download_dpc_apk(token: Optional[str] = Query(None)):
    """
    DPC APKダウンロードエンドポイント。
    実際のAPKはビルド後に静的ファイルとして配置する。
    現時点ではインストール手順ページへリダイレクト。
    """
    base = settings.ssp_endpoint.rstrip("/")
    return RedirectResponse(
        url=f"{base}/mdm/android/install-guide?token={token or ''}",
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
    <a href="/" class="btn btn-skip">スキップ</a>
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

    cmd = await enqueue_command(db, body.device_id, body.command_type, payload)

    fcm_sent = False
    if body.send_fcm and device.fcm_token:
        fcm_sent = await send_command_ping(device.fcm_token, body.device_id)

    return {
        "command_id": cmd.id,
        "status": "queued",
        "fcm_sent": fcm_sent,
    }


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
            "model": d.model,
            "android_version": d.android_version,
            "status": d.status,
            "has_fcm": bool(d.fcm_token),
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
    device.last_seen_at = datetime.now(timezone.utc)
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
    body = await request.json()
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
        existing.last_checkin_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"iOS device updated | udid={udid[:8]}... | event={event}")
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
            last_checkin_at=datetime.now(timezone.utc),
        )
        db.add(ios_dev)
        await db.commit()
        logger.info(f"iOS device registered | udid={udid[:8]}... | model={product_name}")

    return {"status": "ok"}


# ── App Clips API ─────────────────────────────────────────────


@router.get("/appclips/content", summary="App Clipsコンテンツ取得")
async def appclips_content(
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
    tracked_url = build_tracked_url(campaign.id, udid or "appclip-anonymous")

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
      add_web_clip    - params: {url, label, full_screen}
      remove_profile  - params: {identifier}
      device_info     - params: {}
      profile_list    - params: {}
      device_lock     - params: {message, phone}
    """
    # デバイス確認
    ios_dev = await db.scalar(select(iOSDeviceDB).where(iOSDeviceDB.udid == body.udid))
    if not ios_dev:
        raise HTTPException(status_code=404, detail="iOS device not found")

    # コマンドplist生成
    cmd_uuid = str(__import__("uuid").uuid4())
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
        sent_at=datetime.now(timezone.utc) if queued else None,
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
            "enrolled_at": d.enrolled_at.isoformat(),
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
    now = datetime.now(timezone.utc)
    y = year or now.year
    m = month or now.month
    return await calculate_monthly_revenue(db, y, m)


@router.get("/admin/affiliate/report/{dealer_id}", summary="代理店別月次精算レポート（管理者）")
async def dealer_monthly_report(
    dealer_id: str,
    year: int = Query(default=None),
    month: int = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_key),
):
    """代理店単位の月次精算レポート"""
    now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)
    y = year or now.year
    m = month or now.month
    return await get_all_dealers_report(db, y, m)


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
        }
        for c in rows.scalars().all()
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
                now = datetime.now(timezone.utc)
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
    now = datetime.now(timezone.utc)

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

    now = datetime.now(timezone.utc)
    y = year or now.year
    m = month or now.month
    report = await get_dealer_monthly_report(db, dealer.id, y, m)

    base = settings.ssp_endpoint.rstrip("/")
    qr_url = f"{base}/mdm/qr/{dealer.store_code}"
    portal_url = f"{base}/mdm/portal?dealer={dealer.id}"

    campaign_rows_html = "".join(
        f"<tr><td>{c['campaign_name']}</td>"
        f"<td>{c['reward_type'].upper()}</td>"
        f"<td>{c['cv_count']}</td>"
        f"<td class='revenue'>¥{c['revenue_jpy']:,.0f}</td></tr>"
        for c in report.get("by_campaign", [])
    ) or "<tr><td colspan='4' style='color:#8e8e93;text-align:center'>今月のCVはまだありません</td></tr>"

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
  </style>
</head>
<body>
  <div class="nav">
    <h1>{dealer.name}</h1>
    <span>店舗コード: {dealer.store_code}</span>
  </div>
  <div class="main">
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
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/advertiser/portal/{campaign_id}", response_class=HTMLResponse, summary="広告主ポータル（管理者Key）")
async def advertiser_portal(
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

    now = datetime.now(timezone.utc)
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

    base = settings.ssp_endpoint.rstrip("/")
    tracked_url = build_tracked_url(campaign_id, "DEVICE_TOKEN")
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
    now = datetime.now(timezone.utc)
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
    token: Optional[str] = Query(None, description="enrollment_token"),
    db: AsyncSession = Depends(get_db),
):
    """
    iOS ホーム画面ウィジェット / WebClip アプリが起動時に呼び出す。
    eCPM エンジンで最適なクリエイティブを選択し、クリック追跡用リダイレクト URL を生成して返す。
    """
    base = settings.ssp_endpoint.rstrip("/")

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
                "tracking_url": build_tracked_url(c.id, token or "anonymous"),
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

    base = settings.ssp_endpoint.rstrip("/")
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
                        base = settings.ssp_endpoint.rstrip("/")
                        imp_id = creative.get("impression_id", "")
                        dest = creative.get("click_url", "")
                        tracking_url = f"{base}/mdm/ios/click?imp={imp_id}&to={dest}"
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
