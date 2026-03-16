#!/usr/bin/env python3
"""
デモ用アフィリエイト案件・クリエイティブ・広告スロットのシードデータ投入スクリプト

Usage:
    python scripts/seed_campaigns.py
    python scripts/seed_campaigns.py --reset   # 既存データを削除してから投入
"""
import asyncio
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import delete, select
from config import settings
from db_models import AffiliateCampaignDB, CreativeDB, MdmAdSlotDB


# ── シードデータ定義 ──────────────────────────────────────────

CAMPAIGNS = [
    {
        "name": "NordVPN",
        "category": "vpn",
        "reward_type": "cps",
        "reward_amount": 2000.0,
        "destination_url": "https://nordvpn.com/",
        "gtm_container_id": None,
    },
    {
        "name": "マネーフォワード",
        "category": "fintech",
        "reward_type": "cpi",
        "reward_amount": 500.0,
        "destination_url": "https://moneyforward.com/",
        "gtm_container_id": None,
    },
    {
        "name": "Audible",
        "category": "app",
        "reward_type": "cpi",
        "reward_amount": 800.0,
        "destination_url": "https://www.amazon.co.jp/audible/",
        "gtm_container_id": None,
    },
    {
        "name": "ふるさと納税（さとふる）",
        "category": "ec",
        "reward_type": "cps",
        "reward_amount": 1500.0,
        "destination_url": "https://www.satofull.jp/",
        "gtm_container_id": None,
    },
]

# campaign name -> list of creative dicts
CREATIVES_BY_CAMPAIGN = {
    "NordVPN": [
        {
            "name": "NordVPN テキスト①",
            "type": "text",
            "title": "VPNで安全なネット接続",
            "body": "NordVPN - 世界60ヶ国サーバー",
            "click_url": "https://nordvpn.com/",
        },
        {
            "name": "NordVPN テキスト②",
            "type": "text",
            "title": "プライバシーを守る",
            "body": "30日間返金保証付き",
            "click_url": "https://nordvpn.com/",
        },
    ],
    "マネーフォワード": [
        {
            "name": "マネーフォワード テキスト①",
            "type": "text",
            "title": "家計簿アプリNo.1",
            "body": "マネーフォワード ME - 口座連携で自動記帳",
            "click_url": "https://moneyforward.com/",
        },
        {
            "name": "マネーフォワード テキスト②",
            "type": "text",
            "title": "資産管理を始めよう",
            "body": "銀行・証券・クレカを一括管理",
            "click_url": "https://moneyforward.com/",
        },
    ],
    "Audible": [
        {
            "name": "Audible テキスト①",
            "type": "text",
            "title": "本を耳で聴こう",
            "body": "Audible - 12万冊以上が聴き放題",
            "click_url": "https://www.amazon.co.jp/audible/",
        },
        {
            "name": "Audible テキスト②",
            "type": "text",
            "title": "30日間無料体験",
            "body": "プロのナレーターが読む本を今すぐ",
            "click_url": "https://www.amazon.co.jp/audible/",
        },
        {
            "name": "Audible テキスト③",
            "type": "text",
            "title": "通勤・家事しながら読書",
            "body": "ながら聴きで知識を増やそう",
            "click_url": "https://www.amazon.co.jp/audible/",
        },
    ],
    "ふるさと納税（さとふる）": [
        {
            "name": "さとふる テキスト①",
            "type": "text",
            "title": "ふるさと納税でお得に",
            "body": "さとふる - 最短翌日お届け返礼品あり",
            "click_url": "https://www.satofull.jp/",
        },
        {
            "name": "さとふる テキスト②",
            "type": "text",
            "title": "今年の控除枠を確認",
            "body": "かんたんシミュレーターで節税額を計算",
            "click_url": "https://www.satofull.jp/",
        },
    ],
}

MDM_SLOTS = [
    {
        "name": "ロック画面広告枠",
        "slot_type": "lockscreen",
        "floor_price_cpm": 500.0,
        "status": "active",
    },
    {
        "name": "ホーム画面ウィジェット広告枠",
        "slot_type": "widget",
        "floor_price_cpm": 300.0,
        "status": "active",
    },
    {
        "name": "プッシュ通知広告枠",
        "slot_type": "notification",
        "floor_price_cpm": 100.0,
        "status": "active",
    },
    {
        "name": "iOS WebClip広告枠",
        "slot_type": "webclip_ios",
        "floor_price_cpm": 200.0,
        "status": "active",
    },
]


# ── メイン処理 ────────────────────────────────────────────────

async def seed(reset: bool = False) -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        if reset:
            print("既存データを削除中...")
            await session.execute(delete(CreativeDB))
            await session.execute(delete(AffiliateCampaignDB))
            await session.execute(delete(MdmAdSlotDB))
            await session.commit()
            print("削除完了")

        campaigns_created = 0
        creatives_created = 0
        slots_created = 0

        # ── AffiliateCampaign 投入 ────────────────────────────
        campaign_id_map: dict[str, str] = {}

        for camp_data in CAMPAIGNS:
            # 同名が既に存在する場合はスキップ
            result = await session.execute(
                select(AffiliateCampaignDB).where(AffiliateCampaignDB.name == camp_data["name"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                print(f"  [SKIP] Campaign already exists: {camp_data['name']}")
                campaign_id_map[camp_data["name"]] = existing.id
                continue

            campaign = AffiliateCampaignDB(
                name=camp_data["name"],
                category=camp_data["category"],
                reward_type=camp_data["reward_type"],
                reward_amount=camp_data["reward_amount"],
                destination_url=camp_data["destination_url"],
                gtm_container_id=camp_data.get("gtm_container_id"),
                status="active",
            )
            session.add(campaign)
            await session.flush()  # IDを確定させる
            campaign_id_map[camp_data["name"]] = campaign.id
            campaigns_created += 1
            print(f"  [CREATE] Campaign: {camp_data['name']} (id={campaign.id})")

        # ── Creative 投入 ─────────────────────────────────────
        for camp_name, creatives in CREATIVES_BY_CAMPAIGN.items():
            campaign_id = campaign_id_map.get(camp_name)
            if not campaign_id:
                print(f"  [WARN] Campaign not found for creatives: {camp_name}")
                continue

            for creative_data in creatives:
                # 同名クリエイティブが既に存在する場合はスキップ
                result = await session.execute(
                    select(CreativeDB).where(
                        CreativeDB.campaign_id == campaign_id,
                        CreativeDB.name == creative_data["name"],
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    print(f"  [SKIP] Creative already exists: {creative_data['name']}")
                    continue

                creative = CreativeDB(
                    campaign_id=campaign_id,
                    name=creative_data["name"],
                    type=creative_data["type"],
                    title=creative_data["title"],
                    body=creative_data.get("body"),
                    click_url=creative_data["click_url"],
                    status="active",
                )
                session.add(creative)
                creatives_created += 1
                print(f"  [CREATE] Creative: {creative_data['name']}")

        # ── MdmAdSlot 投入 ────────────────────────────────────
        for slot_data in MDM_SLOTS:
            # 同一 slot_type が既に存在する場合はスキップ
            result = await session.execute(
                select(MdmAdSlotDB).where(MdmAdSlotDB.slot_type == slot_data["slot_type"])
            )
            existing = result.scalar_one_or_none()
            if existing:
                print(f"  [SKIP] MdmAdSlot already exists: {slot_data['slot_type']}")
                continue

            slot = MdmAdSlotDB(
                name=slot_data["name"],
                slot_type=slot_data["slot_type"],
                floor_price_cpm=slot_data["floor_price_cpm"],
                status=slot_data["status"],
            )
            session.add(slot)
            slots_created += 1
            print(f"  [CREATE] MdmAdSlot: {slot_data['slot_type']} (floor={slot_data['floor_price_cpm']})")

        await session.commit()

    await engine.dispose()

    print("")
    print(f"Created {campaigns_created} campaigns, {creatives_created} creatives, {slots_created} slots")


def main() -> None:
    parser = argparse.ArgumentParser(description="シードデータ投入スクリプト")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="既存のキャンペーン・クリエイティブ・スロットを全削除してから投入する",
    )
    args = parser.parse_args()

    print("シードデータ投入開始...")
    asyncio.run(seed(reset=args.reset))


if __name__ == "__main__":
    main()
