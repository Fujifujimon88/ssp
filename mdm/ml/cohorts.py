"""
ML-03 — 行動コホートセグメント

user_features テーブルをK-Meansクラスタリングして
デバイスを行動パターン別に分類する。
cohort_id は device_profiles.cohort_id に保存し、
OpenRTB BidRequest の user.data に含めることでDSP入札単価を向上させる。
"""
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# K-Means の k値（8〜12 の範囲で調整可）
N_CLUSTERS = 10

# 日本語コホートラベル（クラスタ重心の特徴から命名）
COHORT_LABELS = [
    "朝の通勤者",       # 7-9時 CTR高
    "深夜ゲーマー",     # 23-2時 滞留長
    "週末ショッパー",   # 土日 EC CTR高
    "ニュース閲覧者",   # 昼間 dismiss_swipe多
    "動画視聴者",       # 完了率高
    "アプリインストーラー",  # CPI CVR高
    "ライトユーザー",   # 全体的に低エンゲージ
    "ロイヤルユーザー", # CTR・滞留ともに高
    "朝プレミアム層",   # 7-8時 × 高CTR
    "夕方ブラウザー",   # 17-19時 アクティブ
]


def build_feature_matrix(user_features_list: list[dict]) -> Optional[np.ndarray]:
    """
    UserFeatureDB レコードのリストから特徴量行列を構築する。
    APPI準拠: デバイスIDは含まない（pseudonymous UUID のみ）。
    """
    if not user_features_list:
        return None

    rows = []
    for uf in user_features_list:
        row = [
            float(uf.get("ctr_30d") or 0),
            float(uf.get("avg_dwell_ms") or 0) / 10000.0,  # 正規化
            float(uf.get("preferred_hour") or 12) / 24.0,
            1.0 if uf.get("dominant_dismiss_type") == "cta_tap"    else 0.0,
            1.0 if uf.get("dominant_dismiss_type") == "auto_dismiss" else 0.0,
        ]
        rows.append(row)

    return np.array(rows, dtype=np.float32)


async def compute_cohorts(db) -> int:
    """
    全デバイスの特徴量を取得してK-Meansで分類し、
    device_profiles.cohort_id を更新する。
    更新デバイス数を返す。
    """
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        logger.warning("scikit-learn not installed — cohort segmentation skipped")
        return 0

    from sqlalchemy import select, update
    from db_models import UserFeatureDB, DeviceProfileDB

    # 特徴量取得
    rows = (await db.scalars(select(UserFeatureDB))).all()
    if len(rows) < N_CLUSTERS:
        logger.info(f"Not enough data for cohort segmentation: {len(rows)} < {N_CLUSTERS}")
        return 0

    feature_dicts = [
        {"device_id": r.device_id, "ctr_30d": r.ctr_30d,
         "avg_dwell_ms": r.avg_dwell_ms, "preferred_hour": r.preferred_hour,
         "dominant_dismiss_type": r.dominant_dismiss_type}
        for r in rows
    ]
    device_ids = [d["device_id"] for d in feature_dicts]
    X = build_feature_matrix(feature_dicts)
    if X is None:
        return 0

    # 標準化 + K-Means
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    km = KMeans(n_clusters=N_CLUSTERS, random_state=42, n_init=10)
    labels = km.fit_predict(X_scaled)

    # device_profiles を更新
    updated = 0
    for device_id, label in zip(device_ids, labels):
        cohort_label = COHORT_LABELS[int(label)] if int(label) < len(COHORT_LABELS) else f"コホート{label}"
        await db.execute(
            update(DeviceProfileDB)
            .where(DeviceProfileDB.device_id == device_id)
            .values(cohort_id=int(label), cohort_label=cohort_label)
        )
        updated += 1

    await db.commit()
    logger.info(f"Cohort segmentation complete: {updated} devices updated, k={N_CLUSTERS}")
    return updated


def get_iab_segment(cohort_id: Optional[int], cohort_label: Optional[str]) -> Optional[dict]:
    """
    OpenRTB BidRequest.user.data に含めるIABセグメント情報を返す。
    DSPはこれを使って入札単価を調整する。
    """
    if cohort_id is None:
        return None
    return {
        "id": "ssp-cohort",
        "name": "SSP Behavioral Cohort",
        "segment": [
            {"id": str(cohort_id), "name": cohort_label or f"cohort_{cohort_id}"}
        ]
    }
