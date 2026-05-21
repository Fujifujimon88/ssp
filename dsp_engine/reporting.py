"""
dsp_engine 多次元レポート（AppLovin「Combined」型）。

選択したディメンションで動的に GROUP BY を組み立て、3 つのイベントテーブルを
それぞれの発生日時で集計してマージする:
  - dsp_spend_logs       … インプレッション数・消化額（logged_at 基準）
  - dsp_click_events     … クリック数（clicked_at 基準）
  - dsp_conversion_events… CV数・売上（received_at 基準）

day ディメンションでは各イベントを「そのイベントが起きた日」に計上するため、
配信日と別日のクリック/CVも正しい日付に出る。

MVP のディメンション: day / campaign / source / platform。
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DspClickEventDB, DspConversionEventDB, DspSpendLogDB

logger = logging.getLogger(__name__)

AVAILABLE_DIMENSIONS = ["day", "campaign", "source", "platform"]


def _day_expr(col):
    """タイムスタンプ列から "YYYY-MM-DD" を取り出す（SQLite/PostgreSQL 両対応）。"""
    return func.substr(cast(col, String), 1, 10)


# ディメンション名 → (spend列, conv列, click列) を返すファクトリ。
# day は各テーブルの「イベント発生日時」を使う。
_DIM_COLUMNS = {
    "day": (
        lambda: _day_expr(DspSpendLogDB.logged_at),
        lambda: _day_expr(DspConversionEventDB.received_at),
        lambda: _day_expr(DspClickEventDB.clicked_at),
    ),
    "campaign": (
        lambda: DspSpendLogDB.campaign_id,
        lambda: DspConversionEventDB.campaign_id,
        lambda: DspClickEventDB.campaign_id,
    ),
    "source": (
        lambda: DspSpendLogDB.source,
        lambda: DspConversionEventDB.source,
        lambda: DspClickEventDB.source,
    ),
    "platform": (
        lambda: DspSpendLogDB.platform,
        lambda: DspConversionEventDB.platform,
        lambda: DspClickEventDB.platform,
    ),
}


def _empty_row(dims: list[str], key: tuple) -> dict:
    row = {dims[i]: key[i] for i in range(len(dims))}
    row.update(impressions=0, clicks=0, spend_jpy=0.0, conversions=0, revenue_jpy=0.0)
    return row


async def run_report(
    db: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    dimensions: list[str],
) -> list[dict]:
    """期間とディメンションを指定して多次元レポート行を返す。

    各行: {<各dim>, impressions, clicks, spend_jpy, conversions,
           revenue_jpy, roas(%), cpa(円), ctr(%)}
    spend_jpy 降順でソートして返す。
    """
    dims = [d for d in dimensions if d in AVAILABLE_DIMENSIONS] or ["campaign"]

    start = datetime(date_from.year, date_from.month, date_from.day)
    end = datetime(date_to.year, date_to.month, date_to.day) + timedelta(days=1)

    merged: dict[tuple, dict] = {}

    # ── 消化（dsp_spend_logs）: インプレッション・消化額 ──
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
    for row in spend_rows.all():
        key = tuple(getattr(row, d) for d in dims)
        merged.setdefault(key, _empty_row(dims, key))
        merged[key]["impressions"] = int(row.impressions or 0)
        merged[key]["spend_jpy"] = float(row.spend_jpy or 0.0)

    # ── クリック（dsp_click_events）: clicked_at 基準で集計 ──
    click_cols = [_DIM_COLUMNS[d][2]().label(d) for d in dims]
    click_rows = await db.execute(
        select(*click_cols, func.count(DspClickEventDB.id).label("clicks"))
        .where(DspClickEventDB.clicked_at >= start, DspClickEventDB.clicked_at < end)
        .group_by(*click_cols)
    )
    for row in click_rows.all():
        key = tuple(getattr(row, d) for d in dims)
        merged.setdefault(key, _empty_row(dims, key))
        merged[key]["clicks"] = int(row.clicks or 0)

    # ── 売上（dsp_conversion_events）: received_at 基準で集計 ──
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
    for row in conv_rows.all():
        key = tuple(getattr(row, d) for d in dims)
        merged.setdefault(key, _empty_row(dims, key))
        merged[key]["conversions"] = int(row.conversions or 0)
        merged[key]["revenue_jpy"] = float(row.revenue_jpy or 0.0)

    result: list[dict] = []
    for row in merged.values():
        spend, revenue, conv = row["spend_jpy"], row["revenue_jpy"], row["conversions"]
        imp, clk = row["impressions"], row["clicks"]
        row["roas"] = round(revenue / spend * 100.0, 2) if spend > 0 else 0.0
        row["cpa"] = round(spend / conv, 2) if conv > 0 else 0.0
        row["ctr"] = round(clk / imp * 100.0, 2) if imp > 0 else 0.0
        result.append(row)
    result.sort(key=lambda r: r["spend_jpy"], reverse=True)
    return result
