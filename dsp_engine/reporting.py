"""
dsp_engine 多次元レポート（AppLovin「Combined」型）。

選択したディメンションで動的に GROUP BY を組み立て、3 つのイベントテーブルを
それぞれの発生日時で集計してマージする:
  - dsp_spend_logs       … インプレッション数・消化額（logged_at 基準）
  - dsp_click_events     … クリック数（clicked_at 基準）
  - dsp_conversion_events… CV数・売上（received_at 基準）

day ディメンションでは各イベントを「そのイベントが起きた日」に計上するため、
配信日と別日のクリック/CVも正しい日付に出る。

ディメンション: day / campaign / source / platform に加え、#6 で
creative / publisher / app / placement / geo / deal_id を追加（落札時に
BidRequest + campaign から 3 イベントテーブルへ非正規化記録した列を集計する）。
"""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import (
    DspBidLogDB,
    DspClickEventDB,
    DspConversionEventDB,
    DspCreativeDB,
    DspSpendLogDB,
)
from dsp_engine.nbr import NBR_HOLDOUT

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

# #6 多次元軸（creative/publisher/app/placement/geo/deal_id）。
# 3 イベントテーブルが同名カラムを持つため (dim名, カラム名) から一括生成する。
for _dim, _col in [
    ("creative", "creative_id"),
    ("publisher", "publisher_id"),
    ("app", "app_id"),
    ("placement", "placement"),
    ("geo", "geo"),
    ("deal_id", "deal_id"),
]:
    _DIM_COLUMNS[_dim] = (
        (lambda c=_col: getattr(DspSpendLogDB, c)),
        (lambda c=_col: getattr(DspConversionEventDB, c)),
        (lambda c=_col: getattr(DspClickEventDB, c)),
    )
    AVAILABLE_DIMENSIONS.append(_dim)


def extract_report_dims(bid_request) -> dict:
    """BidRequest からレポート多次元軸を抽出する（#6）。

    publisher / app / placement / geo / deal_id を dict で返す。
    creative_id は campaign 由来のため本関数では扱わない（落札側で解決）。
    BidRequest を持たない経路（外部エクスチェンジ win_notice 等）では
    bid_request=None を渡してよく、その場合は全軸 None を返す。
    """
    dims = {
        "publisher_id": None,
        "app_id": None,
        "placement": None,
        "geo": None,
        "deal_id": None,
    }
    if bid_request is None:
        return dims

    site = getattr(bid_request, "site", None)
    app = getattr(bid_request, "app", None)

    # publisher: site.publisher.id を優先、無ければ app.publisher.id
    publisher = None
    if site is not None and getattr(site, "publisher", None) is not None:
        publisher = site.publisher.id
    if not publisher and app is not None and getattr(app, "publisher", None) is not None:
        publisher = app.publisher.id
    dims["publisher_id"] = publisher

    # app: id 優先、無ければ bundle
    if app is not None:
        dims["app_id"] = app.id or app.bundle

    # placement: imp[0].tagid / deal_id: imp[0].pmp.deals[0].id
    imps = getattr(bid_request, "imp", None) or []
    if imps:
        imp = imps[0]
        dims["placement"] = getattr(imp, "tagid", None)
        pmp = getattr(imp, "pmp", None)
        if pmp is not None and getattr(pmp, "deals", None):
            dims["deal_id"] = pmp.deals[0].id

    # geo: device.geo.country
    device = getattr(bid_request, "device", None)
    if device is not None and getattr(device, "geo", None) is not None:
        dims["geo"] = device.geo.country

    return dims


def _empty_row(dims: list[str], key: tuple) -> dict:
    row = {dims[i]: key[i] for i in range(len(dims))}
    row.update(impressions=0, clicks=0, spend_jpy=0.0, conversions=0, revenue_jpy=0.0)
    return row


# レポートの「1日」は JST 暦日（運用者が見る基準）。イベントのタイムスタンプ列は
# UTC で保存されるため、日付範囲フィルタは JST→UTC へ変換してから突合する。
JST = timezone(timedelta(hours=9))


def _jst_day_range(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """JST 暦日 [date_from, date_to] を UTC の [start, end) 半開区間に変換する。

    logged_at / received_at は tz-aware、clicked_at は naive と UTC 保存列が
    混在する。列の naive/aware に左右されないよう、JST 0 時境界を UTC へ変換し
    naive-UTC（tzinfo を外した UTC 壁時計）に正規化して返す。
    """
    start = (datetime(date_from.year, date_from.month, date_from.day, tzinfo=JST)
             .astimezone(timezone.utc).replace(tzinfo=None))
    end = ((datetime(date_to.year, date_to.month, date_to.day, tzinfo=JST)
            + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None))
    return start, end


async def run_report(
    db: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    dimensions: list[str],
    campaign_id: Optional[str] = None,
) -> list[dict]:
    """期間とディメンションを指定して多次元レポート行を返す。

    各行: {<各dim>, impressions, clicks, spend_jpy, conversions,
           revenue_jpy, roas(%), cpa(円), ctr(%)}
    spend_jpy 降順でソートして返す。

    campaign_id を渡すと、そのキャンペーンだけに WHERE で絞り込む（#7。
    A/B 実験レポートが全キャンペーンを集計してから捨てるのを避けるため）。
    """
    dims = [d for d in dimensions if d in AVAILABLE_DIMENSIONS] or ["campaign"]

    start, end = _jst_day_range(date_from, date_to)

    merged: dict[tuple, dict] = {}

    # ── 消化（dsp_spend_logs）: インプレッション・消化額 ──
    spend_cols = [_DIM_COLUMNS[d][0]().label(d) for d in dims]
    spend_q = (
        select(
            *spend_cols,
            func.count(DspSpendLogDB.id).label("impressions"),
            func.coalesce(func.sum(DspSpendLogDB.spend_jpy), 0.0).label("spend_jpy"),
        )
        .where(DspSpendLogDB.logged_at >= start, DspSpendLogDB.logged_at < end)
    )
    if campaign_id is not None:
        spend_q = spend_q.where(DspSpendLogDB.campaign_id == campaign_id)
    spend_rows = await db.execute(spend_q.group_by(*spend_cols))
    for row in spend_rows.all():
        key = tuple(getattr(row, d) for d in dims)
        merged.setdefault(key, _empty_row(dims, key))
        merged[key]["impressions"] = int(row.impressions or 0)
        merged[key]["spend_jpy"] = float(row.spend_jpy or 0.0)

    # ── クリック（dsp_click_events）: clicked_at 基準で集計 ──
    click_cols = [_DIM_COLUMNS[d][2]().label(d) for d in dims]
    click_q = (
        select(*click_cols, func.count(DspClickEventDB.id).label("clicks"))
        .where(DspClickEventDB.clicked_at >= start, DspClickEventDB.clicked_at < end)
    )
    if campaign_id is not None:
        click_q = click_q.where(DspClickEventDB.campaign_id == campaign_id)
    click_rows = await db.execute(click_q.group_by(*click_cols))
    for row in click_rows.all():
        key = tuple(getattr(row, d) for d in dims)
        merged.setdefault(key, _empty_row(dims, key))
        merged[key]["clicks"] = int(row.clicks or 0)

    # ── 売上（dsp_conversion_events）: received_at 基準で集計 ──
    conv_cols = [_DIM_COLUMNS[d][1]().label(d) for d in dims]
    conv_q = (
        select(
            *conv_cols,
            func.count(DspConversionEventDB.id).label("conversions"),
            func.coalesce(func.sum(DspConversionEventDB.revenue_jpy), 0.0).label("revenue_jpy"),
        )
        .where(
            DspConversionEventDB.received_at >= start,
            DspConversionEventDB.received_at < end,
            DspConversionEventDB.attributed == True,  # noqa: E712
        )
    )
    if campaign_id is not None:
        conv_q = conv_q.where(DspConversionEventDB.campaign_id == campaign_id)
    conv_rows = await db.execute(conv_q.group_by(*conv_cols))
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


async def run_ab_experiment_report(
    db: AsyncSession,
    campaign_id: str,
    *,
    date_from: date,
    date_to: date,
) -> dict:
    """A/B テスト実験レポート（#7）。

    指定キャンペーンのクリエイティブ別実績（A/B 各 variant の比較）と、
    同期間に holdout で意図的にノービッドした件数を返す。

    Returns:
        {
          "campaign_id": str,
          "creatives": [ {creative, creative_name, impressions, clicks,
                          spend_jpy, conversions, revenue_jpy, roas, cpa, ctr}, ... ],
          "holdout_requests": int,   # DspBidLogDB nbr=NBR_HOLDOUT の件数
        }
        実績の無い active クリエイティブもゼロ行で含む。
    """
    # creative 軸の集計を campaign で WHERE 絞り込み（全件集計後に捨てない）
    creative_rows: list[dict] = await run_report(
        db, date_from=date_from, date_to=date_to,
        dimensions=["creative"], campaign_id=campaign_id,
    )

    # active クリエイティブで実績ゼロのものもゼロ行として含める
    seen = {r.get("creative") for r in creative_rows}
    creatives = (await db.scalars(
        select(DspCreativeDB).where(DspCreativeDB.campaign_id == campaign_id)
    )).all()
    name_map = {c.id: c.name for c in creatives}
    for creative in creatives:
        if creative.id not in seen:
            creative_rows.append({
                "creative": creative.id, "impressions": 0, "clicks": 0,
                "spend_jpy": 0.0, "conversions": 0, "revenue_jpy": 0.0,
                "roas": 0.0, "cpa": 0.0, "ctr": 0.0,
            })
    for row in creative_rows:
        row["creative_name"] = name_map.get(row.get("creative"), "")
    creative_rows.sort(key=lambda r: r["spend_jpy"], reverse=True)

    # holdout 件数（期間内・当該キャンペーン）
    start, end = _jst_day_range(date_from, date_to)
    holdout_requests = await db.scalar(
        select(func.count(DspBidLogDB.id)).where(
            DspBidLogDB.campaign_id == campaign_id,
            DspBidLogDB.nbr == NBR_HOLDOUT,
            DspBidLogDB.logged_at >= start,
            DspBidLogDB.logged_at < end,
        )
    ) or 0

    return {
        "campaign_id": campaign_id,
        "creatives": creative_rows,
        "holdout_requests": int(holdout_requests),
    }
