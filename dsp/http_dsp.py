"""
実際のDSPへHTTPでOpenRTBリクエストを送る汎用クラス
本番DSP接続時（The Trade Desk, Xandr等）に使用
"""
import logging
from typing import Optional

import httpx

from auction.openrtb import BidRequest, BidResponse
from dsp.base import BaseDSP

logger = logging.getLogger(__name__)

TIMEOUT_SEC = 0.075  # 75ms（80msより少し余裕を持たせる）


class HttpDSP(BaseDSP):
    """
    OpenRTB HTTPエンドポイントを持つDSPへの接続。
    DSP申請が通った後、このクラスに差し替える。

    Args:
        dsp_id:   DSP識別子
        name:     DSP名
        endpoint: DSPのbidエンドポイントURL
        api_key:  認証トークン（DSPによって異なる）
    """

    def __init__(self, dsp_id: str, name: str, endpoint: str, api_key: str = ""):
        super().__init__(dsp_id=dsp_id, name=name, endpoint=endpoint)
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and self._client.is_closed:
            await self._client.aclose()
            self._client = None
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(TIMEOUT_SEC),
                headers={
                    "Content-Type": "application/json",
                    "x-openrtb-version": "2.5",
                    **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
                },
            )
        return self._client

    async def send_bid_request(self, bid_request: BidRequest) -> Optional[BidResponse]:
        client = await self._get_client()
        try:
            resp = await client.post(
                self.endpoint,
                content=bid_request.model_dump_json(exclude_none=True),
            )
            if resp.status_code == 204:  # No Content = No Bid
                return None
            resp.raise_for_status()
            return BidResponse.model_validate_json(resp.content)
        except httpx.TimeoutException:
            logger.warning(f"DSP {self.dsp_id} timeout")
            return None
        except Exception as e:
            logger.error(f"DSP {self.dsp_id} error: {e}")
            return None

    async def close(self):
        if self._client:
            await self._client.aclose()
