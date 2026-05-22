"""
dsp_engine FastAPI ルーター。

エンドポイント:
  POST /dsp-engine/conversion              ← 購入CVポストバック受信（広告主/AppsFlyer等）
  GET  /dsp-engine/advertiser/login        ← 広告主ログイン
  POST /dsp-engine/advertiser/login
  POST /dsp-engine/advertiser/logout
  GET  /dsp-engine/advertiser/dashboard    ← 広告主ダッシュボード（読み取り専用 ROAS）
  GET  /dsp-engine/advertiser/api/stats
  GET  /dsp-engine/admin/campaigns         ← 運用者: DSPキャンペーン管理
  POST /dsp-engine/admin/campaigns
  POST /dsp-engine/admin/campaigns/{id}
  GET  /dsp-engine/admin/supply            ← 運用者: SSP連携・外部IDマッピング
  POST /dsp-engine/admin/supply
  POST /dsp-engine/admin/supply/{id}
  POST /dsp-engine/admin/supply/{id}/mapping
  GET  /dsp-engine/admin/report            ← 運用者: 多次元レポート（Combined型）
  GET  /dsp-engine/admin/report/api
"""
import hashlib
import hmac
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from auction.openrtb import BidRequest
from auth import create_portal_token, decode_portal_token, hash_password, verify_password
from config import settings
from database import get_db
from cache import get_redis
from dsp_engine import campaign_manager, exchange, fraud, reporting, supply
from dsp_engine.attribution import (
    get_campaign_roas, normalize_conversion_payload, record_click, record_conversion,
    sanitize_pii_payload, verify_postback_secret,
)
from dsp_engine.fraud import validate_revenue
from dsp_engine.bidder import (
    click_destination_url,
    get_bid_log_summary,
    handle_bid_request,
    record_dsp_win,
    verify_win_notice,
)
from dsp_engine.sjcache import get_cached_sellers, lookup_seller
from dsp_engine.supply_chain import (
    SchainVerdict,
    extract_schain,
    verifiable_nodes,
    verify_schain,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dsp-engine", tags=["dsp-engine"])
templates = Jinja2Templates(directory="dsp_engine/templates")

ADVERTISER_COOKIE = "dsp_advertiser_token"


# ── 認証ヘルパー ────────────────────────────────────────────────

_ADMIN_IPS = {ip.strip() for ip in settings.admin_allowed_ips.split(",") if ip.strip()}


def require_admin_ip(request: Request) -> None:
    """管理画面 IP 制限（main.py の _require_admin_ip と同じ挙動）。"""
    client_ip = request.client.host if request.client else ""
    if client_ip not in _ADMIN_IPS:
        raise HTTPException(status_code=403, detail="アクセスが拒否されました")


async def get_advertiser_campaign_id(request: Request) -> str:
    """広告主セッション（cookie JWT）から campaign_id を取得。未認証はログインへ。"""
    token = request.cookies.get(ADVERTISER_COOKIE)
    payload = decode_portal_token(token) if token else None
    if not payload or payload.get("type") != "advertiser":
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/dsp-engine/advertiser/login"},
            detail="ログインが必要です",
        )
    return payload["sub"]


# ── 購入CV受信（ROAS の分子） ───────────────────────────────────

@router.api_route("/conversion", methods=["GET", "POST"], summary="購入CVポストバック受信")
async def receive_conversion(request: Request, db: AsyncSession = Depends(get_db)):
    """広告主 / AppsFlyer / Adjust からの購入CVポストバックを受け取り記録する。

    GET（クエリ文字列）・POST（JSON / フォーム）のどちらでも受信し、各 MMP の
    パラメータ名は normalize_conversion_payload で正規化する。
    設定手順は tasks/dsp_engine_mmp_integration.md を参照。
    """
    params: dict = dict(request.query_params)
    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                params.update(body)
        except Exception:
            try:
                params.update(dict(await request.form()))
            except Exception:
                pass

    # 署名検証: dsp_postback_hmac_secret が設定されていれば HMAC-SHA256 を検証する。
    # 未設定なら後方互換として静的シークレット (asp_postback_secret) を検証する。
    if settings.dsp_postback_hmac_secret:
        sig = str(params.get("signature", ""))
        if sig:
            ct_sig = str(params.get("click_token", params.get("dsp_ct", "")))
            rev_sig = str(params.get("revenue_jpy", ""))
            dedup_sig = str(params.get("dedup_key", ""))
            canonical = f"{ct_sig}|{rev_sig}|{dedup_sig}"
            expected_sig = hmac.new(
                settings.dsp_postback_hmac_secret.encode("utf-8"),
                canonical.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                raise HTTPException(status_code=401, detail="invalid hmac signature")
        else:
            raise HTTPException(status_code=401, detail="signature required")
    elif settings.asp_postback_secret:
        provided = str(params.get("secret", ""))
        if not verify_postback_secret(provided, settings.asp_postback_secret):
            raise HTTPException(status_code=401, detail="invalid secret")

    # PII サニタイズ: raw_payload 保存前に PII キーを除去する
    pii_keys = [k.strip() for k in settings.dsp_pii_strip_keys.split(",") if k.strip()]
    sanitized_params = sanitize_pii_payload(params, pii_keys=pii_keys)

    norm = normalize_conversion_payload(sanitized_params)

    # revenue_jpy の validate_revenue ガード（#8: 不正 revenue を 0 に丸める）
    campaign_for_rev = await campaign_manager.get_campaign(db, norm["campaign_id"])
    if campaign_for_rev is not None:
        if not validate_revenue(
            norm["revenue_jpy"],
            avg_purchase_value_jpy=campaign_for_rev.avg_purchase_value_jpy,
            revenue_cap_multiplier=settings.dsp_revenue_cap_multiplier,
        ):
            logger.warning(
                f"receive_conversion: invalid revenue_jpy={norm['revenue_jpy']} "
                f"for campaign={norm['campaign_id']} — zeroing"
            )
            norm["revenue_jpy"] = 0.0

    try:
        event, created = await record_conversion(
            db,
            campaign_id=norm["campaign_id"],
            click_token=norm["click_token"],
            event_type=norm["event_type"],
            revenue_jpy=norm["revenue_jpy"],
            dedup_key=norm["dedup_key"],
            source=norm["source"],
            platform=norm["platform"],
            raw_payload=str(sanitized_params)[:2000],
            window_days=settings.dsp_attribution_window_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "ok", "created": created, "conversion_id": event.id}


@router.get("/click", summary="クリック計測トラッカー（記録→LPへリダイレクト）")
async def click_redirect(
    request: Request,
    ct: str = Query(..., description="click_token"),
    db: AsyncSession = Depends(get_db),
):
    """広告マークアップのクリックリンク先。クリックを記録し広告主 LP へ 302 する。"""
    client_ip = request.client.host if request.client else ""
    try:
        redis = await get_redis()
    except Exception as exc:
        logger.warning(f"click_redirect: get_redis failed — rate limiting skipped: {exc}")
        redis = None
    token_count, ip_count = await fraud.incr_click_counters(redis, ct, client_ip)
    rate_limited = fraud.check_click_rate_limit(
        None, ct, client_ip,
        token_limit=settings.dsp_click_token_limit,
        ip_limit=settings.dsp_click_ip_limit,
        window_seconds=settings.dsp_click_window_seconds,
        _override_token_count=token_count,
        _override_ip_count=ip_count,
    )
    if rate_limited:
        return RedirectResponse(url="/", status_code=302)  # レート制限超過は記録せずリダイレクト
    log = await record_click(db, ct)
    if log is None:
        return RedirectResponse(url="/", status_code=302)  # 未知トークンは安全側に
    campaign = await campaign_manager.get_campaign(db, log.campaign_id)
    if campaign is None:
        return RedirectResponse(url="/", status_code=302)
    # クリックされたクリエイティブの LP へ（#7。無ければインライン素材へフォールバック）
    creative = (await campaign_manager.get_creative(db, log.creative_id)
                if log.creative_id else None)
    target = click_destination_url(campaign, creative, ct)
    return RedirectResponse(url=target, status_code=302)


# ── 広告主ログイン ──────────────────────────────────────────────

_LOGIN_HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>広告主ログイン | DSP</title>
<style>body{{font-family:sans-serif;background:#f4f6fb;display:flex;min-height:100vh;
margin:0;align-items:center;justify-content:center}}
.box{{background:#fff;padding:32px;border-radius:12px;box-shadow:0 4px 16px rgba(0,0,0,.08);width:320px}}
h1{{font-size:18px;margin:0 0 20px}}input{{width:100%;padding:10px;margin:6px 0;
border:1px solid #ccd;border-radius:6px;box-sizing:border-box}}
button{{width:100%;padding:11px;background:#0b5cff;color:#fff;border:0;border-radius:6px;
font-size:14px;cursor:pointer;margin-top:10px}}.err{{color:#c00;font-size:13px}}</style></head>
<body><form class="box" method="post" action="/dsp-engine/advertiser/login">
<h1>広告主ログイン</h1>{err}
<input name="login_id" placeholder="ログインID" required autofocus>
<input name="password" type="password" placeholder="パスワード" required>
<button type="submit">ログイン</button></form></body></html>"""


@router.get("/advertiser/login", response_class=HTMLResponse, summary="広告主ログイン画面")
async def advertiser_login_page():
    return HTMLResponse(_LOGIN_HTML.format(err=""))


@router.post("/advertiser/login", summary="広告主ログイン")
async def advertiser_login(
    login_id: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    campaign = await campaign_manager.get_campaign_by_login(db, login_id)
    if not campaign or not campaign.hashed_password or not verify_password(password, campaign.hashed_password):
        err = '<p class="err">ログインIDまたはパスワードが違います</p>'
        return HTMLResponse(_LOGIN_HTML.format(err=err), status_code=401)
    token = create_portal_token("advertiser", campaign.id)
    resp = RedirectResponse(url="/dsp-engine/advertiser/dashboard", status_code=303)
    resp.set_cookie(
        ADVERTISER_COOKIE, token, httponly=True, samesite="lax",
        secure=(settings.app_env != "development"), max_age=60 * 60 * 24 * 7,
    )
    return resp


@router.post("/advertiser/logout", summary="広告主ログアウト")
async def advertiser_logout():
    resp = RedirectResponse(url="/dsp-engine/advertiser/login", status_code=303)
    resp.delete_cookie(ADVERTISER_COOKIE)
    return resp


# ── 広告主ダッシュボード（読み取り専用） ────────────────────────

@router.get("/advertiser/dashboard", response_class=HTMLResponse, summary="広告主ダッシュボード")
async def advertiser_dashboard(
    request: Request,
    campaign_id: str = Depends(get_advertiser_campaign_id),
    db: AsyncSession = Depends(get_db),
):
    campaign = await campaign_manager.get_campaign(db, campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="キャンペーンが見つかりません")
    roas = await get_campaign_roas(db, campaign_id)
    return templates.TemplateResponse(
        "advertiser_dashboard.html",
        {"request": request, "campaign": campaign, "roas": roas},
    )


@router.get("/advertiser/api/stats", summary="広告主ダッシュボードKPI(JSON)")
async def advertiser_stats(
    campaign_id: str = Depends(get_advertiser_campaign_id),
    db: AsyncSession = Depends(get_db),
):
    return JSONResponse(await get_campaign_roas(db, campaign_id))


# ── 運用者: DSPキャンペーン管理 ─────────────────────────────────

@router.get("/admin/campaigns", response_class=HTMLResponse,
            summary="DSPキャンペーン管理", dependencies=[Depends(require_admin_ip)])
async def admin_campaigns_page(request: Request, db: AsyncSession = Depends(get_db)):
    campaigns = await campaign_manager.list_campaigns(db)
    rows = []
    for c in campaigns:
        roas = await get_campaign_roas(db, c.id)
        rows.append({"c": c, "roas": roas})
    return templates.TemplateResponse(
        "campaigns.html", {"request": request, "rows": rows}
    )


@router.post("/admin/campaigns", summary="DSPキャンペーン作成",
             dependencies=[Depends(require_admin_ip)])
async def admin_create_campaign(
    db: AsyncSession = Depends(get_db),
    advertiser_name: str = Form(...),
    campaign_name: str = Form(...),
    daily_budget_jpy: float = Form(0.0),
    total_budget_jpy: float = Form(0.0),
    target_roas: float = Form(300.0),
    margin_rate: float = Form(0.20),
    bid_floor_jpy: float = Form(100.0),
    bid_cap_jpy: float = Form(5000.0),
    avg_purchase_value_jpy: float = Form(3000.0),
    base_ctr: float = Form(0.01),
    target_cvr: float = Form(0.02),
    creative_title: str = Form(""),
    creative_body: str = Form(""),
    creative_image_url: str = Form(""),
    creative_click_url: str = Form(...),
    login_id: str = Form(""),
    password: str = Form(""),
):
    fields = dict(
        advertiser_name=advertiser_name, campaign_name=campaign_name,
        daily_budget_jpy=daily_budget_jpy, total_budget_jpy=total_budget_jpy,
        target_roas=target_roas, margin_rate=margin_rate,
        bid_floor_jpy=bid_floor_jpy, bid_cap_jpy=bid_cap_jpy,
        avg_purchase_value_jpy=avg_purchase_value_jpy,
        base_ctr=base_ctr, target_cvr=target_cvr,
        creative_title=creative_title, creative_body=creative_body or None,
        creative_image_url=creative_image_url or None,
        creative_click_url=creative_click_url, status="active",
    )
    if login_id:
        fields["login_id"] = login_id
    if password:
        fields["hashed_password"] = hash_password(password)
    campaign = await campaign_manager.create_campaign(db, **fields)
    # 主クリエイティブを DspCreativeDB として登録（#7。A/B の起点。
    # id を campaign.creative_id に揃え、creative 軸レポートと整合させる）。
    await campaign_manager.create_creative(
        db, id=campaign.creative_id, campaign_id=campaign.id, name="主素材",
        title=creative_title, body=creative_body or None,
        image_url=creative_image_url or None, click_url=creative_click_url,
        weight=100,
    )
    return RedirectResponse(url="/dsp-engine/admin/campaigns", status_code=303)


@router.post("/admin/campaigns/{campaign_id}", summary="DSPキャンペーン更新",
             dependencies=[Depends(require_admin_ip)])
async def admin_update_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    status_: Optional[str] = Form(None, alias="status"),
    daily_budget_jpy: Optional[float] = Form(None),
    margin_rate: Optional[float] = Form(None),
    bid_cap_jpy: Optional[float] = Form(None),
    holdout_rate: Optional[float] = Form(None),
):
    updated = await campaign_manager.update_campaign(
        db, campaign_id, status=status_, daily_budget_jpy=daily_budget_jpy,
        margin_rate=margin_rate, bid_cap_jpy=bid_cap_jpy, holdout_rate=holdout_rate,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="キャンペーンが見つかりません")
    return RedirectResponse(url="/dsp-engine/admin/campaigns", status_code=303)


# ── 運用者: クリエイティブ / A/B 実験管理（#7） ─────────────────

def _creative_dict(c) -> dict:
    return {
        "id": c.id, "campaign_id": c.campaign_id, "name": c.name,
        "title": c.title, "body": c.body, "image_url": c.image_url,
        "click_url": c.click_url, "width": c.width, "height": c.height,
        "status": c.status, "weight": c.weight,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _experiment_dict(e) -> dict:
    return {
        "id": e.id, "campaign_id": e.campaign_id, "name": e.name,
        "status": e.status, "winner_creative_id": e.winner_creative_id,
        "started_at": e.started_at.isoformat() if e.started_at else None,
        "concluded_at": e.concluded_at.isoformat() if e.concluded_at else None,
    }


@router.get("/admin/campaigns/{campaign_id}/creatives", summary="クリエイティブ/実験一覧(JSON)",
            dependencies=[Depends(require_admin_ip)])
async def admin_list_creatives(campaign_id: str, db: AsyncSession = Depends(get_db)):
    """キャンペーンのクリエイティブと A/B 実験を JSON で返す（#7）。"""
    campaign = await campaign_manager.get_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="キャンペーンが見つかりません")
    creatives = await campaign_manager.list_creatives(db, campaign_id)
    experiments = await campaign_manager.list_experiments(db, campaign_id)
    return JSONResponse({
        "campaign_id": campaign_id,
        "holdout_rate": campaign.holdout_rate,
        "creatives": [_creative_dict(c) for c in creatives],
        "experiments": [_experiment_dict(e) for e in experiments],
    })


@router.post("/admin/campaigns/{campaign_id}/creatives", summary="クリエイティブ作成",
             dependencies=[Depends(require_admin_ip)])
async def admin_create_creative(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    title: str = Form(""),
    body: str = Form(""),
    image_url: str = Form(""),
    click_url: str = Form(...),
    weight: int = Form(100),
):
    campaign = await campaign_manager.get_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="キャンペーンが見つかりません")
    creative = await campaign_manager.create_creative(
        db, campaign_id=campaign_id, name=name, title=title,
        body=body or None, image_url=image_url or None,
        click_url=click_url, weight=weight,
    )
    return JSONResponse({"status": "ok", "creative_id": creative.id})


@router.post("/admin/creatives/{creative_id}", summary="クリエイティブ更新",
             dependencies=[Depends(require_admin_ip)])
async def admin_update_creative(
    creative_id: str,
    db: AsyncSession = Depends(get_db),
    name: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    body: Optional[str] = Form(None),
    image_url: Optional[str] = Form(None),
    click_url: Optional[str] = Form(None),
    weight: Optional[int] = Form(None),
    status_: Optional[str] = Form(None, alias="status"),
):
    updated = await campaign_manager.update_creative(
        db, creative_id, name=name, title=title, body=body,
        image_url=image_url, click_url=click_url, weight=weight, status=status_,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="クリエイティブが見つかりません")
    return JSONResponse({"status": "ok"})


@router.post("/admin/campaigns/{campaign_id}/experiments", summary="A/B実験作成",
             dependencies=[Depends(require_admin_ip)])
async def admin_create_experiment(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
):
    campaign = await campaign_manager.get_campaign(db, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="キャンペーンが見つかりません")
    exp = await campaign_manager.create_experiment(db, campaign_id=campaign_id, name=name)
    return JSONResponse({"status": "ok", "experiment_id": exp.id})


@router.post("/admin/experiments/{experiment_id}/conclude", summary="A/B実験を終了(winner宣言)",
             dependencies=[Depends(require_admin_ip)])
async def admin_conclude_experiment(
    experiment_id: str,
    db: AsyncSession = Depends(get_db),
    winner_creative_id: str = Form(""),
):
    concluded = await campaign_manager.conclude_experiment(
        db, experiment_id, winner_creative_id=winner_creative_id or None
    )
    if concluded is None:
        raise HTTPException(status_code=404, detail="実験が見つかりません")
    return JSONResponse({"status": "ok"})


@router.get("/admin/campaigns/{campaign_id}/ab-report", summary="A/B実験レポート(JSON)",
            dependencies=[Depends(require_admin_ip)])
async def admin_ab_report(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """クリエイティブ別実績 + holdout 件数の A/B 実験レポート（#7）。"""
    today = date.today()
    try:
        d_from = date.fromisoformat(date_from) if date_from else today - timedelta(days=7)
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        raise HTTPException(status_code=400, detail="日付形式が不正です (YYYY-MM-DD)")
    report = await reporting.run_ab_experiment_report(
        db, campaign_id, date_from=d_from, date_to=d_to
    )
    return JSONResponse(report)


# ── 運用者: SSP連携・外部IDマッピング ───────────────────────────

@router.get("/admin/supply", response_class=HTMLResponse,
            summary="SSP連携画面", dependencies=[Depends(require_admin_ip)])
async def admin_supply_page(request: Request, db: AsyncSession = Depends(get_db)):
    await supply.ensure_self_ssp_node(db)
    connections = await supply.list_supply_connections(db)
    items = [
        {
            "conn": c,
            "platform_mapping": supply.parse_mapping(c.platform_mapping),
            "app_mapping": supply.parse_mapping(c.app_mapping),
        }
        for c in connections
    ]
    return templates.TemplateResponse(
        "ssp_integration.html",
        {"request": request, "items": items, "ssp_endpoint": settings.ssp_endpoint},
    )


@router.post("/admin/supply", summary="SSP連携接続を追加",
             dependencies=[Depends(require_admin_ip)])
async def admin_create_supply(
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    endpoint_url: str = Form(...),
    timeout_ms: int = Form(200),
    qps_limit: int = Form(0),
    api_secret: str = Form(""),
    exchange_asi: str = Form(""),
):
    await supply.create_supply_connection(
        db, name=name, endpoint_url=endpoint_url,
        timeout_ms=timeout_ms, qps_limit=qps_limit,
        api_secret=api_secret or None,
        exchange_asi=exchange_asi.strip() or None,
    )
    return RedirectResponse(url="/dsp-engine/admin/supply", status_code=303)


@router.post("/admin/supply/{conn_id}", summary="SSP連携接続を更新",
             dependencies=[Depends(require_admin_ip)])
async def admin_update_supply(
    conn_id: str,
    db: AsyncSession = Depends(get_db),
    active: Optional[bool] = Form(None),
    qps_limit: Optional[int] = Form(None),
):
    updated = await supply.update_supply_connection(
        db, conn_id, active=active, qps_limit=qps_limit
    )
    if not updated:
        raise HTTPException(status_code=404, detail="接続が見つかりません")
    return RedirectResponse(url="/dsp-engine/admin/supply", status_code=303)


@router.post("/admin/supply/{conn_id}/mapping", summary="外部IDマッピング保存",
             dependencies=[Depends(require_admin_ip)])
async def admin_save_mapping(
    conn_id: str,
    db: AsyncSession = Depends(get_db),
    external_id: str = Form(...),
    platform: str = Form(...),
):
    """外部サービスID 1件を platform（android/ios等）にマッピングして追記する。"""
    conn = await supply.get_supply_connection(db, conn_id)
    if not conn:
        raise HTTPException(status_code=404, detail="接続が見つかりません")
    mapping = supply.parse_mapping(conn.platform_mapping)
    mapping[external_id] = platform
    await supply.save_id_mapping(db, conn_id, platform_mapping=mapping)
    return RedirectResponse(url="/dsp-engine/admin/supply", status_code=303)


# ── 運用者: 多次元レポート（Combined型） ────────────────────────

@router.get("/admin/report", response_class=HTMLResponse,
            summary="多次元レポート画面", dependencies=[Depends(require_admin_ip)])
async def admin_report_page(request: Request):
    return templates.TemplateResponse(
        "report.html",
        {"request": request, "dimensions": reporting.AVAILABLE_DIMENSIONS},
    )


@router.get("/admin/report/api", summary="多次元レポート(JSON)",
            dependencies=[Depends(require_admin_ip)])
async def admin_report_api(
    db: AsyncSession = Depends(get_db),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    dimensions: str = Query("campaign"),
):
    today = date.today()
    try:
        d_from = date.fromisoformat(date_from) if date_from else today - timedelta(days=7)
        d_to = date.fromisoformat(date_to) if date_to else today
    except ValueError:
        raise HTTPException(status_code=400, detail="日付形式が不正です (YYYY-MM-DD)")

    dims = [d.strip() for d in dimensions.split(",") if d.strip()]
    rows = await reporting.run_report(db, date_from=d_from, date_to=d_to, dimensions=dims)

    # campaign ディメンションがあれば id → 名称に補強
    if "campaign" in dims:
        name_map = {c.id: c.campaign_name for c in await campaign_manager.list_campaigns(db)}
        for row in rows:
            row["campaign_name"] = name_map.get(row.get("campaign"), row.get("campaign"))

    return JSONResponse({
        "date_from": d_from.isoformat(),
        "date_to": d_to.isoformat(),
        "dimensions": [d for d in dims if d in reporting.AVAILABLE_DIMENSIONS] or ["campaign"],
        "rows": rows,
    })


@router.get("/admin/bid-logs/api", summary="入札判定ログ + no-bid 理由内訳(JSON)",
            dependencies=[Depends(require_admin_ip)])
async def admin_bid_logs_api(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
):
    """直近の入札判定ログと outcome/nbr 別件数を返す（運用者向け）。

    no-bid 理由コード nbr の内訳から「なぜ入札していないか」を切り分ける。
    """
    summary = await get_bid_log_summary(db, limit=limit)
    return JSONResponse(summary)


# ── 外部エクスチェンジ受信側（Phase 2） ─────────────────────────

@router.post("/exchange/{exchange_name}/bid", summary="外部エクスチェンジからのOpenRTB入札を受信")
async def inbound_bid(
    exchange_name: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """外部 SSP・エクスチェンジが OpenRTB 2.5 BidRequest を POST する受信口。

    エクスチェンジは SSP 連携画面で登録・有効化されている必要がある。
    ノービッドは OpenRTB 標準の HTTP 204 で返す。
    """
    exch = await exchange.get_active_exchange(db, exchange_name)
    if exch is None:
        return Response(status_code=204)  # 未登録/停止中 → ノービッド
    if not exchange.verify_exchange_secret(exch, request.headers.get("X-DSP-Secret")):
        return Response(status_code=401)  # 認証失敗（共有シークレット不一致）
    if not exchange.check_qps(exchange_name, exch.qps_limit):
        return Response(status_code=429)  # QPS 上限超過

    try:
        body = await request.json()
        bid_request = BidRequest.model_validate(body)
    except Exception as exc:
        logger.warning(f"inbound_bid: invalid OpenRTB from {exchange_name}: {exc}")
        return Response(status_code=204)

    # schain 構造検証（入札パス内・外部 I/O なし）。REJECT はノービッド(204)扱い。
    # 照合する asi は接続名(exchange_name)ではなくエクスチェンジ自身の asi ドメイン。
    schain_obj = extract_schain(bid_request)
    sc_result = verify_schain(
        schain_obj,
        exch.exchange_asi or "",
        supply.parse_allowed_asi_domains(exch.allowed_asi_domains),
        strict=bool(exch.schain_required),
    )
    if sc_result.verdict == SchainVerdict.REJECT:
        logger.warning(f"inbound_bid: schain rejected from {exchange_name}: {sc_result.reason}")
        return Response(status_code=204)
    if sc_result.verdict == SchainVerdict.WARN:
        logger.info(f"inbound_bid: schain warn from {exchange_name}: {sc_result.reason}")

    # sellers.json 突合（L1 キャッシュ参照のみ・外部 I/O なし）。
    # 当該エクスチェンジの sellers.json で検証できるのは asi が一致するノードのみ
    # （多段 schain の上流ノードは上流側 sellers.json に属する）。
    # キャッシュ未取得時は lookup_seller がフォールバックで通す。
    sc_nodes = verifiable_nodes(schain_obj, exch.exchange_asi or "")
    if sc_nodes:
        sellers = get_cached_sellers(exchange_name, exch.sellers_json_cache)
    for node in sc_nodes:
        if not lookup_seller(sellers, node.sid, node.asi):
            logger.warning(
                f"inbound_bid: seller not found from {exchange_name}: "
                f"asi={node.asi} sid={node.sid}"
            )
            if exch.schain_required:
                return Response(status_code=204)

    started = time.monotonic()
    resp = await handle_bid_request(bid_request, db, source=exchange_name)
    exchange.record_bid_stat(exchange_name, (time.monotonic() - started) * 1000.0)

    if resp is None:
        return Response(status_code=204)  # ノービッド
    return JSONResponse(resp.model_dump(exclude_none=True))


@router.get("/win", summary="落札通知（外部エクスチェンジ win notice / nurl）")
async def win_notice(
    ct: str = Query(..., description="click_token"),
    cid: str = Query(..., description="campaign_id"),
    src: str = Query("external", description="入札元エクスチェンジ名"),
    bid: float = Query(0.0, description="入札時のCPM(USD)"),
    price: str = Query("0", description="実落札価格CPM(USD)。${AUCTION_PRICE}マクロ"),
    sig: str = Query("", description="nurl 改竄防止の HMAC 署名"),
    crid: str = Query("", description="落札クリエイティブID（#7 レポート用ヒント）"),
    db: AsyncSession = Depends(get_db),
):
    """外部エクスチェンジが落札時に nurl を呼ぶ。消化・予算ペーシングを記録する。

    nurl は広告レスポンスに露出するため、第三者による spend 偽装を防ぐ目的で
    HMAC 署名(sig)を必須とする。署名対象は ct/cid/src/bid。price は
    ${AUCTION_PRICE} マクロのため署名できないので、bid を上限としてクランプする。
    """
    if not verify_win_notice(sig, ct=ct, cid=cid, src=src, bid=bid):
        logger.warning(f"win_notice: invalid signature (cid={cid} src={src})")
        raise HTTPException(status_code=403, detail="invalid win notice signature")

    try:
        cleared_usd = float(price)
    except (TypeError, ValueError):
        cleared_usd = 0.0  # マクロ未置換などは 0 として扱う
    # price は署名対象外。改竄による spend 水増しを防ぐため bid を上限にクランプ。
    if bid > 0:
        cleared_usd = min(cleared_usd, bid)

    try:
        await record_dsp_win(
            db, campaign_id=cid, click_token=ct, impression_id=None,
            cleared_price_usd=cleared_usd, bid_price_usd=bid or cleared_usd,
            platform="external", source=src, creative_id=crid or None,
        )
    except Exception as exc:
        logger.error(f"win_notice failed: {exc}")
        raise HTTPException(status_code=400, detail="win notice processing failed")

    exchange.record_win_stat(src)
    try:
        await exchange.persist_exchange_stats(db, src)
    except Exception:
        pass  # 統計の書き戻し失敗は落札記録の成否に影響させない
    return {"status": "ok"}
