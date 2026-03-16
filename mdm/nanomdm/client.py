"""NanoMDM REST APIクライアント

NanoMDMはGoで書かれたMDMサーバーで、このSSPと並行して起動する。
FastAPIはNanoMDMのREST APIを呼び出してiOSデバイスにコマンドを送信する。

NanoMDM APIエンドポイント:
  GET  /version                     ← バージョン確認
  GET  /v1/enrollments              ← エンロール済みデバイス一覧
  PUT  /v1/commands/{udid}          ← コマンド送信
  GET  /v1/commands/{udid}          ← 保留コマンド確認
  DELETE /v1/commands/{udid}/{uuid} ← コマンド削除

NanoMDM起動方法:
  ./nanomdm -storage file -dsn ./nanomdm-data -api change-me-nanomdm-key
"""
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)


def _headers() -> dict:
    return {"Authorization": f"Basic {settings.nanomdm_api_key}"}


async def health_check() -> bool:
    """NanoMDMサーバーの起動確認"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.nanomdm_url}/version")
            return resp.status_code == 200
    except Exception:
        return False


async def push_command(udid: str, plist_xml: bytes) -> bool:
    """
    NanoMDM REST API経由でiOSデバイスにMDMコマンドをキューイングする。

    Args:
        udid: デバイスのUDID
        plist_xml: MDMコマンドplist（XML bytes）

    Returns:
        True: キューイング成功
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{settings.nanomdm_url}/v1/commands/{udid}",
                content=plist_xml,
                headers={**_headers(), "Content-Type": "application/xml"},
            )
            if resp.status_code in (200, 201, 204):
                logger.info(f"NanoMDM command queued | udid={udid[:8]}...")
                return True
            logger.warning(f"NanoMDM command failed | status={resp.status_code} | {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"NanoMDM client error | {e}")
        return False


async def get_enrollments() -> list[dict]:
    """エンロール済みデバイス一覧を取得する"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{settings.nanomdm_url}/v1/enrollments",
                headers=_headers(),
            )
            resp.raise_for_status()
            return resp.json() or []
    except Exception as e:
        logger.warning(f"NanoMDM enrollments error | {e}")
        return []


async def delete_command(udid: str, command_uuid: str) -> bool:
    """保留中のコマンドを削除する"""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.delete(
                f"{settings.nanomdm_url}/v1/commands/{udid}/{command_uuid}",
                headers=_headers(),
            )
            return resp.status_code in (200, 204)
    except Exception as e:
        logger.warning(f"NanoMDM delete command error | {e}")
        return False
