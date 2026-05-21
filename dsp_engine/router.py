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
from dsp_engine import campaign_manager, exchange, reporting, supply
from dsp_engine.attribution import (
    get_campaign_roas, normalize_conversion_payload, record_click, record_conversion,
)
from dsp_engine.bidder import click_through_url, handle_bid_request, record_dsp_win

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

    # 任意のシークレット検証（asp_postback_secret 設定時のみ）
    if settings.asp_postback_secret:
        if str(params.get("secret", "")) != settings.asp_postback_secret:
            raise HTTPException(status_code=401, detail="invalid secret")

    norm = normalize_conversion_payload(params)
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
            raw_payload=str(params)[:2000],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"status": "ok", "created": created, "conversion_id": event.id}


@router.get("/click", summary="クリック計測トラッカー（記録→LPへリダイレクト）")
async def click_redirect(
    ct: str = Query(..., description="click_token"),
    db: AsyncSession = Depends(get_db),
):
    """広告マークアップのクリックリンク先。クリックを記録し広告主 LP へ 302 する。"""
    log = await record_click(db, ct)
    if log is None:
        return RedirectResponse(url="/", status_code=302)  # 未知トークンは安全側に
    campaign = await campaign_manager.get_campaign(db, log.campaign_id)
    target = click_through_url(campaign, ct) if campaign else "/"
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
    await campaign_manager.create_campaign(db, **fields)
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
):
    updated = await campaign_manager.update_campaign(
        db, campaign_id, status=status_, daily_budget_jpy=daily_budget_jpy,
        margin_rate=margin_rate, bid_cap_jpy=bid_cap_jpy,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="キャンペーンが見つかりません")
    return RedirectResponse(url="/dsp-engine/admin/campaigns", status_code=303)


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
):
    await supply.create_supply_connection(
        db, name=name, endpoint_url=endpoint_url,
        timeout_ms=timeout_ms, qps_limit=qps_limit,
        api_secret=api_secret or None,
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
    db: AsyncSession = Depends(get_db),
):
    """外部エクスチェンジが落札時に nurl を呼ぶ。消化・予算ペーシングを記録する。"""
    try:
        cleared_usd = float(price)
    except (TypeError, ValueError):
        cleared_usd = 0.0  # マクロ未置換などは 0 として扱う

    try:
        await record_dsp_win(
            db, campaign_id=cid, click_token=ct, impression_id=None,
            cleared_price_usd=cleared_usd, bid_price_usd=bid or cleared_usd,
            platform="external", source=src,
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
