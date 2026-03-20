"""S2S ポストバック送信モジュール（BKD-04）

DPC APKからインストール確認を受けた後、計測パートナー（AppsFlyer / Adjust）に
S2Sポストバックを送信してインストールイベントを計上する。
"""
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import (
    AffiliateCampaignDB,
    AndroidDeviceDB,
    InstallEventDB,
    MdmImpressionDB,
    PostbackLogDB,
)

logger = logging.getLogger(__name__)

APPSFLYER_S2S_URL = "https://s2s.appsflyer.com/api/v2/installs"
ADJUST_S2S_URL = "https://s2s.adjust.com/event"


async def send_appsflyer_postback(
    install_event: InstallEventDB,
    campaign: AffiliateCampaignDB,
    device: AndroidDeviceDB,
) -> bool:
    """AppsFlyerへS2Sインストールポストバックを送信する。

    POST https://s2s.appsflyer.com/api/v2/installs
    成功時（HTTP 200）は True を返す。
    """
    if not campaign.appsflyer_dev_key:
        return False

    app_id = campaign.destination_url  # app_id としてdestination_url（パッケージ名）を使用
    advertising_id = device.gaid or ""

    payload = {
        "advertising_id": advertising_id,
        "app_id": app_id,
        "af_events_api": "true",
        "eventName": "install",
        "af_customer_user_id": install_event.device_id,
        "timestamp": str(install_event.install_ts),
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                APPSFLYER_S2S_URL,
                params={"devkey": campaign.appsflyer_dev_key},
                json=payload,
            )
            success = resp.status_code == 200
            logger.info(
                f"AppsFlyer S2S postback | app={app_id} | device={install_event.device_id[:8]}... "
                f"| status={resp.status_code}"
            )
            return success
    except Exception as exc:
        logger.warning(f"AppsFlyer S2S postback error: {exc}")
        return False


async def send_adjust_postback(
    install_event: InstallEventDB,
    campaign: AffiliateCampaignDB,
    device: AndroidDeviceDB,
) -> bool:
    """AdjustへS2Sイベントポストバックを送信する。

    POST https://s2s.adjust.com/event
    成功時（HTTP 200）は True を返す。
    """
    if not campaign.adjust_app_token:
        return False

    params = {
        "app_token": campaign.adjust_app_token,
        "event_token": campaign.adjust_event_token or "",
        "gps_adid": device.gaid or "",
        "s2s": "1",
        "created_at": str(install_event.install_ts),
        "partner_params[device_id]": install_event.device_id,
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(ADJUST_S2S_URL, params=params)
            success = resp.status_code == 200
            logger.info(
                f"Adjust S2S postback | app={campaign.adjust_app_token} "
                f"| device={install_event.device_id[:8]}... | status={resp.status_code}"
            )
            return success
    except Exception as exc:
        logger.warning(f"Adjust S2S postback error: {exc}")
        return False


async def send_direct_asp_postback(
    install_event: InstallEventDB,
    campaign: AffiliateCampaignDB,
    device: AndroidDeviceDB,
    event_type: str = "install",
) -> bool:
    """postback_url_template を使って ASP（A8.net / smaad / Felmat等）に直接ポストバックを送信する。

    テンプレート変数:
      {device_id} {enrollment_token} {dealer_id} {store_id}
      {amount} {install_ts} {package_name} {event_type}
    """
    if not campaign.postback_url_template:
        return False

    try:
        url = campaign.postback_url_template.format(
            device_id=urllib.parse.quote(install_event.device_id, safe=""),
            enrollment_token=urllib.parse.quote(device.enrollment_token or "", safe=""),
            dealer_id=urllib.parse.quote(install_event.dealer_id or "", safe=""),
            store_id=urllib.parse.quote(install_event.store_id or "", safe=""),
            amount=int(install_event.cpi_amount),
            install_ts=install_event.install_ts,
            package_name=urllib.parse.quote(install_event.package_name, safe=""),
            event_type=urllib.parse.quote(event_type, safe=""),
        )
    except KeyError as exc:
        logger.warning(f"send_direct_asp_postback: invalid template variable {exc}")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            success = 200 <= resp.status_code < 300
            logger.info(
                f"Direct ASP postback | event_type={event_type} | device={install_event.device_id[:8]}... "
                f"| status={resp.status_code}"
            )
            return success
    except Exception as exc:
        logger.warning(f"send_direct_asp_postback error: {exc}")
        return False


async def trigger_postbacks(
    install_event_id: str,
    db: AsyncSession,
    event_type: str = "install",
) -> None:
    """インストールイベントに対して登録済み計測パートナーへポストバックを送信する。

    - install_event / campaign / device をロード
    - appsflyer_dev_key が設定されていれば AppsFlyer へ送信
    - adjust_app_token が設定されていれば Adjust へ送信
    - postback_url_template が設定されていれば直接ASPへ送信
    - 各送信結果を PostbackLogDB に記録
    - install_event の postback_status / billing_status を更新する
    """
    install_event = await db.get(InstallEventDB, install_event_id)
    if install_event is None:
        logger.error(f"trigger_postbacks: install_event not found | id={install_event_id}")
        return

    campaign = await db.get(AffiliateCampaignDB, install_event.campaign_id)
    if campaign is None:
        logger.error(f"trigger_postbacks: campaign not found | id={install_event.campaign_id}")
        return

    device = await db.scalar(
        select(AndroidDeviceDB).where(AndroidDeviceDB.device_id == install_event.device_id)
    )
    if device is None:
        logger.warning(f"trigger_postbacks: android device not found | device_id={install_event.device_id}")
        # デバイスが見つからなくても空のダミーを使ってポストバックを試みる
        device = AndroidDeviceDB(device_id=install_event.device_id)

    results: list[bool] = []

    # AppsFlyer
    if campaign.appsflyer_dev_key:
        af_success = await send_appsflyer_postback(install_event, campaign, device)
        log = PostbackLogDB(
            install_event_id=install_event_id,
            provider="appsflyer",
            request_url=APPSFLYER_S2S_URL,
            response_status=200 if af_success else None,
            success=af_success,
            attempted_at=datetime.now(timezone.utc),
        )
        db.add(log)
        results.append(af_success)

    # Adjust
    if campaign.adjust_app_token:
        adj_success = await send_adjust_postback(install_event, campaign, device)
        log = PostbackLogDB(
            install_event_id=install_event_id,
            provider="adjust",
            request_url=ADJUST_S2S_URL,
            response_status=200 if adj_success else None,
            success=adj_success,
            attempted_at=datetime.now(timezone.utc),
        )
        db.add(log)
        results.append(adj_success)

    # 直接ASPポストバック（A8.net / smaad / Felmat / ValueCommerce等）
    if campaign.postback_url_template:
        asp_success = await send_direct_asp_postback(install_event, campaign, device, event_type)
        log = PostbackLogDB(
            install_event_id=install_event_id,
            provider="direct_asp",
            request_url=campaign.postback_url_template[:500],
            response_status=200 if asp_success else None,
            success=asp_success,
            attempted_at=datetime.now(timezone.utc),
        )
        db.add(log)
        results.append(asp_success)

    install_event.postback_attempts += 1

    if not results:
        # 計測パートナー未設定 → 即 billable
        install_event.postback_status = "success"
        install_event.billing_status = "billable"
        install_event.cpi_amount = campaign.reward_amount
    elif all(results):
        install_event.postback_status = "success"
        install_event.billing_status = "billable"
        install_event.cpi_amount = campaign.reward_amount
    else:
        install_event.postback_status = "failed"
        # billing_status は pending のまま（リトライ余地を残す）

    await db.commit()
    logger.info(
        f"trigger_postbacks done | install_event={install_event_id} "
        f"| postback_status={install_event.postback_status} "
        f"| billing_status={install_event.billing_status}"
    )


async def check_vta(
    device_id: str,
    package_name: str,
    campaign_id: str,
    install_event_id: str,
    db: AsyncSession,
) -> None:
    """View-Through Attribution（BKD-09）。

    クリックアトリビューションが存在しない場合に、キャンペーンのVTAウィンドウ内で
    該当デバイスのインプレッションを探し、マッチすれば InstallEventDB を更新する。

    1. 同一 (device_id, package_name) のclick attributionがないことを確認
    2. キャンペーンの vta_window_hours 以内の MdmImpressionDB を検索
    3. マッチすれば attribution_type="view_through" に更新し、
       cpi_amount = reward_amount * vta_cpi_rate で再計算する
    """
    install_event = await db.get(InstallEventDB, install_event_id)
    if install_event is None:
        logger.warning(f"check_vta: install_event not found | id={install_event_id}")
        return

    # 既にクリックアトリビューションが設定されている場合はスキップ
    if install_event.attribution_type == "click":
        # click由来のポストバックが成功していれば VTA は不要
        if install_event.postback_status == "success":
            return

    campaign = await db.get(AffiliateCampaignDB, campaign_id)
    if campaign is None:
        logger.warning(f"check_vta: campaign not found | id={campaign_id}")
        return

    # VTAウィンドウの開始時刻を計算
    window_start = datetime.now(timezone.utc) - timedelta(hours=campaign.vta_window_hours)

    # デバイスのインプレッション履歴を検索（キャンペーン紐付きクリエイティブ経由）
    from db_models import CreativeDB
    imp = await db.scalar(
        select(MdmImpressionDB)
        .join(CreativeDB, MdmImpressionDB.creative_id == CreativeDB.id)
        .where(
            MdmImpressionDB.device_id == device_id,
            CreativeDB.campaign_id == campaign_id,
            MdmImpressionDB.served_at >= window_start,
        )
        .order_by(MdmImpressionDB.served_at.desc())
        .limit(1)
    )

    if imp is None:
        logger.info(
            f"check_vta: no VTA match | device={device_id[:8]}... "
            f"| pkg={package_name} | campaign={campaign_id}"
        )
        return

    # VTAマッチ: attribution_type と cpi_amount を更新
    install_event.attribution_type = "view_through"
    install_event.vta_impression_id = imp.id
    install_event.cpi_amount = campaign.reward_amount * campaign.vta_cpi_rate
    install_event.billing_status = "billable"

    await db.commit()
    logger.info(
        f"check_vta: VTA match | install_event={install_event_id} "
        f"| impression={imp.id} | device={device_id[:8]}... | cpi={install_event.cpi_amount}"
    )
