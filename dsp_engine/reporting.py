"""
dsp_engine 多次元レポート（AppLovin「Combined」型）。

選択したディメンションで動的に GROUP BY を組み立て、dsp_spend_logs（消化）と
dsp_conversion_events（売上）を集計してマージする。

MVP のディメンション: day / campaign / source / platform。
（country / size はインベントリにメタが乗る Phase 2 で追加）
クリック計測は MVP では未実装のため CTR/clicks 列は持たない。
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspConversionEventDB, DspSpendLogDB

logger = logging.getLogger(__name__)

AVAILABLE_DIMENSIONS = ["day", "campaign", "source", "platform"]


def _day_expr(col):
    """タイムスタンプ列から "YYYY-MM-DD" を取り出す（SQLite/PostgreSQL 両対応）。"""
    return func.substr(cast(col, String), 1, 10)


# ディメンション名 → (spend テーブル列式, conv テーブル列式) を返すファクトリ
_DIM_COLUMNS = {
    "day": (lambda: _day_expr(DspSpendLogDB.logged_at),
            lambda: _day_expr(DspConversionEventDB.received_at)),
    "campaign": (lambda: DspSpendLogDB.campaign_id,
                 lambda: DspConversionEventDB.campaign_id),
    "source": (lambda: DspSpendLogDB.source,
               lambda: DspConversionEventDB.source),
    "platform": (lambda: DspSpendLogDB.platform,
                 lambda: DspConversionEventDB.platform),
}


def _empty_row(dims: list[str], key: tuple) -> dict:
    row = {dims[i]: key[i] for i in range(len(dims))}
    row.update(impressions=0, spend_jpy=0.0, conversions=0, revenue_jpy=0.0)
    return row


async def run_report(
    db: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    dimensions: list[str],
) -> list[dict]:
    """期間とディメンションを指定して多次元レポート行を返す。

    各行: {<各dim>, impressions, spend_jpy, conversions, revenue_jpy, roas(%), cpa(円)}
    spend_jpy 降順でソートして返す。
    """
    dims = [d for d in dimensions if d in AVAILABLE_DIMENSIONS] or ["campaign"]

    start = datetime(date_from.year, date_from.month, date_from.day)
    end = datetime(date_to.year, date_to.month, date_to.day) + timedelta(days=1)

    # ── 消化（dsp_spend_logs）集計 ──
    spend_cols = [_DIM_COLUMNS[d][0]().label(d) for d in dims]
    spend_rows = await db.execute(
        select(
            *spend_cols,
            func.count(DspSpendLogDB.id).label("impressions"),
            func.coalesce(func.sum(DspSpendLogDB.spend_jpy), 0.0).label("spend_jpy"),
        )
        .where(DspSpendLogDB.logged_at >= start, DspSpendLogDB.logged_at < end)
        .group_by(*spend_cols)
    )

    # ── 売上（dsp_conversion_events）集計 ──
    conv_cols = [_DIM_COLUMNS[d][1]().label(d) for d in dims]
    conv_rows = await db.execute(
        select(
            *conv_cols,
            func.count(DspConversionEventDB.id).label("conversions"),
            func.coalesce(func.sum(DspConversionEventDB.revenue_jpy), 0.0).label("revenue_jpy"),
        )
        .where(DspConversionEventDB.received_at >= start, DspConversionEventDB.received_at < end)
        .group_by(*conv_cols)
    )

    merged: dict[tuple, dict] = {}
    for row in spend_rows.all():
        key = tuple(getattr(row, d) for d in dims)
        merged.setdefault(key, _empty_row(dims, key))
        merged[key]["impressions"] = int(row.impressions or 0)
        merged[key]["spend_jpy"] = float(row.spend_jpy or 0.0)
    for row in conv_rows.all():
        key = tuple(getattr(row, d) for d in dims)
        merged.setdefault(key, _empty_row(dims, key))
        merged[key]["conversions"] = int(row.conversions or 0)
        merged[key]["revenue_jpy"] = float(row.revenue_jpy or 0.0)

    result: list[dict] = []
    for row in merged.values():
        spend, revenue, conv = row["spend_jpy"], row["revenue_jpy"], row["conversions"]
        row["roas"] = round(revenue / spend * 100.0, 2) if spend > 0 else 0.0
        row["cpa"] = round(spend / conv, 2) if conv > 0 else 0.0
        result.append(row)
    result.sort(key=lambda r: r["spend_jpy"], reverse=True)
    return result
