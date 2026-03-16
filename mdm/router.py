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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
    CampaignDB, DealerDB, DeviceDB,
    MDMCommandDB, iOSDeviceDB,
)
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
      <h2>📋 このサービスでできること</h2>
      <div class="consent-item">
        <span class="consent-icon">🔒</span>
        <div class="consent-text"><strong>VPN自動設定</strong><br>安全なインターネット接続を自動で設定します</div>
      </div>
      <div class="consent-item">
        <span class="consent-icon">📱</span>
        <div class="consent-text"><strong>ホーム画面にショートカット追加</strong><br>便利なサービスへのアクセスを追加します</div>
      </div>
      <div class="consent-item">
        <span class="consent-icon">🎁</span>
        <div class="consent-text"><strong>クーポン・お得情報の配信</strong><br>おすすめのアプリやサービスをお知らせします</div>
      </div>
      <div class="consent-item">
        <span class="consent-icon">⚙️</span>
        <div class="consent-text"><strong>設定の遠隔更新</strong><br>VPN設定などを最新状態に自動更新します</div>
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

    function checkReady() {{
      var age = document.getElementById("age-group").value;
      var iosBtn = document.getElementById("download-btn");
      var andBtn = document.getElementById("android-btn");
      if (age) {{
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


@router.post("/device/consent", summary="同意登録 → mobileconfig URL返却")
async def device_consent(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    dealer_id = body.get("dealer_id") or None
    campaign_id = body.get("campaign_id") or None
    age_group = body.get("age_group")
    user_agent = body.get("user_agent", "")

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
    db: AsyncSession = Depends(get_db),
):
    """
    ロック画面アプリが起動時に呼び出す。
    デバイスのセグメントに応じた広告コンテンツを返す。
    現時点ではアクティブなアフィリエイト案件をランダムで返す（Phase 5で最適化）。
    """
    result = await db.execute(
        select(AffiliateCampaignDB)
        .where(AffiliateCampaignDB.status == "active")
        .limit(5)
    )
    campaigns = list(result.scalars().all())

    if not campaigns:
        return {"content": None}

    import random
    campaign = random.choice(campaigns)

    tracked_url = build_tracked_url(campaign.id, device_id or "anonymous")

    return {
        "content": {
            "campaign_id": campaign.id,
            "title": campaign.name,
            "cta_url": tracked_url,
            "category": campaign.category,
        }
    }


@router.get("/android/widget/content", summary="ホーム画面ウィジェットコンテンツ取得")
async def widget_content(
    device_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """ホーム画面ウィジェットアプリが表示するコンテンツを返す"""
    result = await db.execute(
        select(AffiliateCampaignDB)
        .where(AffiliateCampaignDB.status == "active", AffiliateCampaignDB.category == "app")
        .limit(5)
    )
    campaigns = list(result.scalars().all())

    if not campaigns:
        return {"items": []}

    import random
    random.shuffle(campaigns)
    items = [
        {
            "campaign_id": c.id,
            "title": c.name,
            "cta_url": build_tracked_url(c.id, device_id or "anonymous"),
        }
        for c in campaigns[:3]
    ]
    return {"items": items}


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
    """管理画面からAndroidデバイスにコマンドをキューイングし、FCMで通知する"""
    # デバイス確認
    device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == body.device_id)
    )
    if not device:
        raise HTTPException(status_code=404, detail="Android device not found")

    cmd = await enqueue_command(db, body.device_id, body.command_type, body.payload)

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
