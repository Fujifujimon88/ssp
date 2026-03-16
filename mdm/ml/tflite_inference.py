"""
TFLite モデルのオンデバイス推論ラッパー。
PrefetchWorker から呼ばれ、候補クリエイティブをスコアリングする。
"""
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "models" / "two_tower.tflite"


def load_interpreter():
    try:
        import tflite_runtime.interpreter as tflite
        interp = tflite.Interpreter(model_path=str(MODEL_PATH))
        interp.allocate_tensors()
        return interp
    except Exception:
        try:
            import tensorflow as tf
            interp = tf.lite.Interpreter(model_path=str(MODEL_PATH))
            interp.allocate_tensors()
            return interp
        except Exception as e:
            logger.warning(f"TFLite interpreter unavailable: {e}")
            return None


_interpreter = None


def get_interpreter():
    global _interpreter
    if _interpreter is None and MODEL_PATH.exists():
        _interpreter = load_interpreter()
    return _interpreter


def predict_ctr(user_vec: np.ndarray, item_vec: np.ndarray) -> float:
    """
    User + Item ベクトルから予測CTRを返す。
    モデル不在時は -1 を返す（呼び出し元でフォールバック）。
    """
    interp = get_interpreter()
    if interp is None:
        return -1.0
    try:
        input_details  = interp.get_input_details()
        output_details = interp.get_output_details()
        interp.set_tensor(input_details[0]["index"], user_vec.astype(np.float32))
        interp.set_tensor(input_details[1]["index"], item_vec.astype(np.float32))
        interp.invoke()
        return float(interp.get_tensor(output_details[0]["index"])[0])
    except Exception as e:
        logger.warning(f"TFLite inference error: {e}")
        return -1.0


def hour_to_sincos(hour: int) -> tuple[float, float]:
    """時刻を周期的エンコーディングに変換 (sin/cos)"""
    angle = 2 * math.pi * hour / 24
    return math.sin(angle), math.cos(angle)
