"""
デモデータ生成スクリプト
過去7日分のインプレッションデータをDBに投入する。

使い方:
    python scripts/seed_demo.py
    python scripts/seed_demo.py --days 14   # 過去14日分
    python scripts/seed_demo.py --clear     # 既存データ削除後に生成
"""
import argparse
import asyncio
import random
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from database import engine, Base
from db_models import AdSlotDB, ImpressionDB, PublisherDB

DSP_IDS = ["mock-ttd", "mock-xandr", "mock-dv360", "mock-low"]
DSP_WEIGHTS = [0.35, 0.28, 0.22, 0.15]   # 落札シェア
DSP_CPMS    = [2.5,  2.0,  3.0,  0.8]    # 基準CPM


async def seed(days: int = 7, clear: bool = False):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.connect() as conn:
        # パブリッシャー一覧取得
        result = await conn.execute(select(PublisherDB))
        publishers = result.fetchall()

    if not publishers:
        print("❌ パブリッシャーが登録されていません。先に /auth/register でパブリッシャーを作成してください。")
        return

    async with engine.connect() as conn:
        # スロット一覧取得
        result = await conn.execute(select(AdSlotDB).where(AdSlotDB.active == True))
        slots = result.fetchall()

    if not slots:
        print("❌ 広告スロットがありません。先に /api/slots でスロットを作成してください。")
        return

    async with AsyncSession(engine, expire_on_commit=False) as session:
        if clear:
            await session.execute(delete(ImpressionDB))
            await session.commit()
            print("[clear] 既存インプレッションデータを削除しました")

        total = 0
        for d in range(days, 0, -1):
            target_date = date.today() - timedelta(days=d - 1)
            base_count = 800 if target_date.weekday() < 5 else 400
            daily_count = random.randint(int(base_count * 0.7), int(base_count * 1.3))

            imps = []
            for _ in range(daily_count):
                slot = random.choice(slots)
                pub_id = slot.publisher_id
                filled = random.random() < 0.72

                winning_dsp = None
                clearing_price = 0.0
                if filled:
                    winning_dsp = random.choices(DSP_IDS, weights=DSP_WEIGHTS)[0]
                    idx = DSP_IDS.index(winning_dsp)
                    clearing_price = round(DSP_CPMS[idx] * random.uniform(0.75, 1.25), 4)

                ts = datetime(
                    target_date.year, target_date.month, target_date.day,
                    random.randint(6, 23), random.randint(0, 59), random.randint(0, 59),
                    tzinfo=timezone.utc,
                )
                imps.append(ImpressionDB(
                    id=str(uuid.uuid4()),
                    auction_id=str(uuid.uuid4()),
                    imp_id=str(uuid.uuid4()),
                    slot_id=slot.id,
                    publisher_id=pub_id,
                    winning_dsp=winning_dsp,
                    clearing_price=clearing_price,
                    bid_count=random.randint(1, 5),
                    duration_ms=round(random.uniform(10, 75), 1),
                    filled=filled,
                    timestamp=ts,
                ))

            session.add_all(imps)
            await session.commit()
            total += daily_count
            fill_count = sum(1 for i in imps if i.filled)
            rev = sum(i.clearing_price for i in imps if i.filled) / 1000
            print(
                f"  {target_date}  {daily_count:4d}インプ  "
                f"フィル {fill_count/daily_count*100:.0f}%  "
                f"収益 ${rev:.4f}"
            )

        print(f"\n[done] 合計 {total:,} 件のインプレッションデータを生成しました（過去{days}日分）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="デモデータ生成")
    parser.add_argument("--days", type=int, default=7, help="生成する日数（デフォルト: 7）")
    parser.add_argument("--clear", action="store_true", help="既存データを削除してから生成")
    args = parser.parse_args()

    asyncio.run(seed(days=args.days, clear=args.clear))
