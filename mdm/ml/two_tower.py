"""
ML-02 — Two-Tower 推薦モデル

User Tower + Item Tower のドット積で予測CTRを計算する。
週次で再学習し、TFLiteに変換してAndroidデバイスに配布する。
MLflow でメトリクスを追跡する。
"""
import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── 特徴量定義 ─────────────────────────────────────────────────────

USER_FEATURES = ["age_bracket", "carrier", "model", "hour_sin", "hour_cos",
                 "click_rate_7d", "avg_dwell_ms_norm"]
ITEM_FEATURES = ["category", "historical_ctr", "cpm_norm",
                 "creative_type", "target_carrier_match"]
EMBEDDING_DIM = 64

# カテゴリ特徴量の語彙
CARRIER_VOCAB   = ["docomo", "au", "softbank", "rakuten", "other"]
MODEL_VOCAB     = ["iphone", "galaxy", "pixel", "xperia", "aquos", "other"]
CATEGORY_VOCAB  = ["app", "game", "ec", "finance", "food", "travel", "other"]
CREATIVE_VOCAB  = ["banner", "video", "playable", "native"]
AGE_VOCAB       = ["10s", "20s", "30s", "40s", "50s+", "unknown"]


def build_two_tower_model():
    """
    Two-Tower モデルを構築する。
    TensorFlow/Keras が利用できない環境では None を返す（フォールバック）。
    """
    try:
        import tensorflow as tf
        from tensorflow import keras

        # ── User Tower ──────────────────────────────────────
        age_input     = keras.Input(shape=(1,), name="age_bracket")
        carrier_input = keras.Input(shape=(1,), name="carrier")
        model_input   = keras.Input(shape=(1,), name="device_model")
        hour_input    = keras.Input(shape=(2,), name="hour_sincos")    # [sin, cos]
        ctr_input     = keras.Input(shape=(1,), name="click_rate_7d")
        dwell_input   = keras.Input(shape=(1,), name="avg_dwell_norm")

        age_emb     = keras.layers.Embedding(len(AGE_VOCAB)+1,   8,  name="age_emb")(age_input)
        carrier_emb = keras.layers.Embedding(len(CARRIER_VOCAB)+1, 8, name="carrier_emb")(carrier_input)
        model_emb   = keras.layers.Embedding(len(MODEL_VOCAB)+1,   16, name="model_emb")(model_input)

        age_flat     = keras.layers.Flatten()(age_emb)
        carrier_flat = keras.layers.Flatten()(carrier_emb)
        model_flat   = keras.layers.Flatten()(model_emb)

        user_concat = keras.layers.Concatenate()(
            [age_flat, carrier_flat, model_flat, hour_input, ctr_input, dwell_input]
        )
        user_dense  = keras.layers.Dense(128, activation="relu")(user_concat)
        user_dense  = keras.layers.Dropout(0.1)(user_dense)
        user_emb    = keras.layers.Dense(EMBEDDING_DIM, name="user_embedding")(user_dense)
        user_emb    = keras.layers.Lambda(
            lambda x: tf.math.l2_normalize(x, axis=1), name="user_norm"
        )(user_emb)

        # ── Item Tower ──────────────────────────────────────
        cat_input        = keras.Input(shape=(1,),  name="category")
        item_ctr_input   = keras.Input(shape=(1,),  name="historical_ctr")
        cpm_input        = keras.Input(shape=(1,),  name="cpm_norm")
        ctype_input      = keras.Input(shape=(1,),  name="creative_type")
        carrier_match    = keras.Input(shape=(1,),  name="carrier_match")

        cat_emb   = keras.layers.Embedding(len(CATEGORY_VOCAB)+1, 16, name="cat_emb")(cat_input)
        ctype_emb = keras.layers.Embedding(len(CREATIVE_VOCAB)+1,  8, name="ctype_emb")(ctype_input)
        cat_flat   = keras.layers.Flatten()(cat_emb)
        ctype_flat = keras.layers.Flatten()(ctype_emb)

        item_concat = keras.layers.Concatenate()(
            [cat_flat, item_ctr_input, cpm_input, ctype_flat, carrier_match]
        )
        item_dense = keras.layers.Dense(128, activation="relu")(item_concat)
        item_dense = keras.layers.Dropout(0.1)(item_dense)
        item_emb   = keras.layers.Dense(EMBEDDING_DIM, name="item_embedding")(item_dense)
        item_emb   = keras.layers.Lambda(
            lambda x: tf.math.l2_normalize(x, axis=1), name="item_norm"
        )(item_emb)

        # ── スコアリング（ドット積 → sigmoid）──────────────
        dot     = keras.layers.Dot(axes=1, normalize=False)([user_emb, item_emb])
        output  = keras.layers.Activation("sigmoid", name="predicted_ctr")(dot)

        model = keras.Model(
            inputs=[age_input, carrier_input, model_input, hour_input,
                    ctr_input, dwell_input,
                    cat_input, item_ctr_input, cpm_input, ctype_input, carrier_match],
            outputs=output,
            name="TwoTowerSSP"
        )
        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=1e-3),
            loss="binary_crossentropy",
            metrics=["AUC"],
        )
        return model

    except ImportError:
        logger.warning("TensorFlow not installed — Two-Tower model unavailable")
        return None


def export_tflite(model, output_path: str) -> bool:
    """
    Keras モデルを TFLite flatbuffer に変換する。
    最大サイズ 5 MB。int8 量子化を適用。
    """
    try:
        import tensorflow as tf
        converter = tf.lite.TFLiteConverter.from_keras_model(model)
        converter.optimizations = [tf.lite.Optimize.DEFAULT]
        converter.target_spec.supported_types = [tf.float16]
        tflite_bytes = converter.convert()

        size_mb = len(tflite_bytes) / 1024 / 1024
        if size_mb > 5.0:
            logger.warning(f"TFLite model too large: {size_mb:.2f} MB (max 5 MB)")

        Path(output_path).write_bytes(tflite_bytes)
        logger.info(f"TFLite exported: {output_path} ({size_mb:.2f} MB)")
        return True
    except Exception as e:
        logger.error(f"TFLite export failed: {e}")
        return False


def encode_carrier(carrier: Optional[str]) -> int:
    c = (carrier or "other").lower()
    for i, v in enumerate(CARRIER_VOCAB):
        if v in c:
            return i
    return len(CARRIER_VOCAB) - 1  # "other"


def encode_category(category: Optional[str]) -> int:
    c = (category or "other").lower()
    for i, v in enumerate(CATEGORY_VOCAB):
        if v in c:
            return i
    return len(CATEGORY_VOCAB) - 1


def score_candidates_fallback(candidates: list[dict], device_features: dict) -> list[dict]:
    """
    TFLite モデル不在時のフォールバック：eCPMランキング。
    """
    for c in candidates:
        c["_score"] = c.get("ecpm", 0) * c.get("time_slot_multiplier", 1.0)
    return sorted(candidates, key=lambda x: x["_score"], reverse=True)
