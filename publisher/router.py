"""パブリッシャー管理API（DB永続化 + JWT認証対応）"""
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, get_current_publisher_id, hash_password, verify_admin_key, verify_password
from database import get_db
from db_models import AdSlotDB, ImpressionDB, PublisherDB
from publisher.models import AdSlot, AdSlotCreate, Publisher, PublisherCreate
from publisher.tag_generator import generate_prebid_tag, generate_slot_div

router = APIRouter(tags=["publisher"])


# ── 認証 ───────────────────────────────────────────────────────

@router.post("/auth/register", response_model=dict, summary="パブリッシャー登録")
async def register(data: PublisherCreate, password: str, db: AsyncSession = Depends(get_db)):
    # ドメイン重複チェック
    existing = await db.scalar(select(PublisherDB).where(PublisherDB.domain == data.domain))
    if existing:
        raise HTTPException(status_code=400, detail="Domain already registered")

    pub = PublisherDB(
        id=str(uuid.uuid4()),
        name=data.name,
        domain=data.domain,
        contact_email=data.contact_email,
        site_category=json.dumps(data.site_category),
        floor_price=data.floor_price,
        api_key=uuid.uuid4().hex,
        hashed_password=hash_password(password),
    )
    db.add(pub)
    await db.commit()
    await db.refresh(pub)

    token = create_access_token(pub.id)
    return {
        "publisher_id": pub.id,
        "api_key": pub.api_key,
        "access_token": token,
        "token_type": "bearer",
        "message": "登録完了。審査後にステータスがactiveになります。",
    }


@router.post("/auth/token", summary="ログイン（JWT取得）")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    pub = await db.scalar(select(PublisherDB).where(PublisherDB.domain == form.username))
    if not pub or not verify_password(form.password, pub.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {
        "access_token": create_access_token(pub.id),
        "token_type": "bearer",
    }


# ── パブリッシャー情報 ──────────────────────────────────────────

@router.get("/api/publishers/me", response_model=Publisher, summary="自分の情報取得")
async def get_me(
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    pub = await db.get(PublisherDB, publisher_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")
    return _to_publisher(pub)


# ── 広告スロット ───────────────────────────────────────────────

@router.post("/api/slots", response_model=AdSlot, summary="広告スロット作成")
async def create_slot(
    data: AdSlotCreate,
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    if data.publisher_id != publisher_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    slot = AdSlotDB(
        id=str(uuid.uuid4()),
        publisher_id=publisher_id,
        name=data.name,
        format=data.format.value,
        width=data.width,
        height=data.height,
        floor_price=data.floor_price,
        position=data.position,
        tag_id=uuid.uuid4().hex[:16],
        active=True,
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return _to_slot(slot)


@router.get("/api/slots", response_model=list[AdSlot], summary="スロット一覧")
async def list_slots(
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AdSlotDB).where(AdSlotDB.publisher_id == publisher_id))
    return [_to_slot(s) for s in result.scalars().all()]


@router.get("/api/slots/{slot_id}/tag", summary="Prebid.jsタグ取得")
async def get_tag(
    slot_id: str,
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    slot = await db.get(AdSlotDB, slot_id)
    if not slot or slot.publisher_id != publisher_id:
        raise HTTPException(status_code=404, detail="Slot not found")

    pub = await db.get(PublisherDB, publisher_id)
    pub_model = _to_publisher(pub)
    slot_model = _to_slot(slot)

    return {
        "head_tag": generate_prebid_tag(pub_model, [slot_model]),
        "body_tag": generate_slot_div(slot_model),
        "instructions": (
            "① <head>内に head_tag を貼る\n"
            "② 広告表示したい箇所に body_tag を貼る"
        ),
    }


@router.get("/api/admin/stats", summary="管理画面用集計統計")
async def admin_stats(_: None = Depends(verify_admin_key), db: AsyncSession = Depends(get_db)):
    today = date.today()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today, datetime.max.time())

    # 本日の基本集計
    row = (await db.execute(
        select(
            func.count(ImpressionDB.id).label("total"),
            func.sum(case((ImpressionDB.filled == True, 1), else_=0)).label("filled"),
            func.sum(ImpressionDB.clearing_price).label("revenue"),
        ).where(ImpressionDB.timestamp >= start, ImpressionDB.timestamp <= end)
    )).one()

    total = row.total or 0
    filled = row.filled or 0
    revenue_cpm_sum = float(row.revenue or 0)
    revenue_usd = round(revenue_cpm_sum / 1000, 4)
    fill_rate = round(filled / total * 100, 1) if total > 0 else 0.0
    ecpm = round(revenue_cpm_sum / filled, 4) if filled > 0 else 0.0

    # 時間帯別インプレッション（0〜23時）
    hourly_rows = (await db.execute(
        select(
            extract("hour", ImpressionDB.timestamp).label("hour"),
            func.count(ImpressionDB.id).label("cnt"),
        )
        .where(ImpressionDB.timestamp >= start, ImpressionDB.timestamp <= end)
        .group_by("hour")
    )).all()
    hourly_map = {int(r.hour): r.cnt for r in hourly_rows}
    hourly = [hourly_map.get(h, 0) for h in range(24)]

    # DSP別落札数
    dsp_rows = (await db.execute(
        select(
            ImpressionDB.winning_dsp,
            func.count(ImpressionDB.id).label("wins"),
        )
        .where(
            ImpressionDB.filled == True,
            ImpressionDB.timestamp >= start,
            ImpressionDB.timestamp <= end,
        )
        .group_by(ImpressionDB.winning_dsp)
        .order_by(func.count(ImpressionDB.id).desc())
    )).all()
    dsp_breakdown = [{"dsp_id": r.winning_dsp, "wins": r.wins} for r in dsp_rows]

    return {
        "impressions": total,
        "fill_rate": fill_rate,
        "revenue_usd": revenue_usd,
        "ecpm": ecpm,
        "hourly": hourly,
        "dsp_breakdown": dsp_breakdown,
    }


@router.get("/api/admin/publishers/{pub_id}/tags", summary="管理用タグ取得")
async def admin_get_tags(pub_id: str, _: None = Depends(verify_admin_key), db: AsyncSession = Depends(get_db)):
    pub = await db.get(PublisherDB, pub_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")
    result = await db.execute(
        select(AdSlotDB).where(AdSlotDB.publisher_id == pub_id, AdSlotDB.active == True)
    )
    slots = result.scalars().all()
    pub_model = _to_publisher(pub)
    slot_models = [_to_slot(s) for s in slots]
    return {
        "head_tag": generate_prebid_tag(pub_model, slot_models),
        "body_tags": {s.name: generate_slot_div(s) for s in slot_models},
    }


@router.put("/api/admin/publishers/{pub_id}/status", summary="パブリッシャーステータス変更（管理用）")
async def update_publisher_status(
    pub_id: str,
    status: str,
    _: None = Depends(verify_admin_key),
    db: AsyncSession = Depends(get_db),
):
    from publisher.models import PublisherStatus
    if status not in [s.value for s in PublisherStatus]:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    pub = await db.get(PublisherDB, pub_id)
    if not pub:
        raise HTTPException(status_code=404, detail="Publisher not found")
    pub.status = status
    await db.commit()
    return {"publisher_id": pub_id, "status": status}


@router.delete("/api/slots/{slot_id}", summary="広告スロット停止")
async def deactivate_slot(
    slot_id: str,
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    slot = await db.get(AdSlotDB, slot_id)
    if not slot or slot.publisher_id != publisher_id:
        raise HTTPException(status_code=404, detail="Slot not found")
    slot.active = False
    await db.commit()
    return {"status": "deactivated", "slot_id": slot_id}


@router.get("/api/tags/full", summary="全スロットタグ一括取得")
async def get_full_tags(
    publisher_id: str = Depends(get_current_publisher_id),
    db: AsyncSession = Depends(get_db),
):
    pub = await db.get(PublisherDB, publisher_id)
    result = await db.execute(
        select(AdSlotDB).where(AdSlotDB.publisher_id == publisher_id, AdSlotDB.active == True)
    )
    slots = result.scalars().all()
    pub_model = _to_publisher(pub)
    slot_models = [_to_slot(s) for s in slots]

    return {
        "head_tag": generate_prebid_tag(pub_model, slot_models),
        "body_tags": {s.name: generate_slot_div(s) for s in slot_models},
    }


# ── ヘルパー ───────────────────────────────────────────────────

def _to_publisher(pub: PublisherDB) -> Publisher:
    from publisher.models import PublisherStatus
    return Publisher(
        id=pub.id,
        name=pub.name,
        domain=pub.domain,
        contact_email=pub.contact_email,
        site_category=json.loads(pub.site_category or "[]"),
        floor_price=pub.floor_price,
        status=PublisherStatus(pub.status),
        api_key=pub.api_key,
        created_at=pub.created_at,
        monthly_revenue_usd=pub.monthly_revenue_usd,
    )


def _to_slot(slot: AdSlotDB) -> AdSlot:
    from publisher.models import AdFormat
    return AdSlot(
        id=slot.id,
        publisher_id=slot.publisher_id,
        name=slot.name,
        format=AdFormat(slot.format),
        width=slot.width,
        height=slot.height,
        floor_price=slot.floor_price,
        position=slot.position,
        tag_id=slot.tag_id,
        active=slot.active,
        created_at=slot.created_at,
    )
