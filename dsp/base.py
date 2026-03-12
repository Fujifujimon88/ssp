"""DSP接続の基底クラス"""
from abc import ABC, abstractmethod
from typing import Optional
from auction.openrtb import BidRequest, BidResponse


class BaseDSP(ABC):
    def __init__(self, dsp_id: str, name: str, endpoint: str):
        self.dsp_id = dsp_id
        self.name = name
        self.endpoint = endpoint

    @abstractmethod
    async def send_bid_request(self, bid_request: BidRequest) -> Optional[BidResponse]:
        """OpenRTB入札リクエストを送信し、レスポンスを返す"""
        ...
