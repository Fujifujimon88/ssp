/**
 * SSP Platform - Prebid.js カスタムビッダーアダプター
 *
 * 使い方:
 *   1. このファイルを prebid.js のモジュールディレクトリに配置
 *   2. adUnits の bids 設定で bidder: 'ssp_adapter' と指定
 *   3. SSP Platform の /v1/bid エンドポイントへリクエストが飛ぶ
 *
 * Prebid.js 公式ドキュメント:
 *   https://docs.prebid.org/dev-docs/bidder-adaptor-guide.html
 */

import { registerBidder } from '../src/adapters/bidderFactory.js';
import { BANNER } from '../src/mediaTypes.js';
import { ajax } from '../src/ajax.js';

const BIDDER_CODE = 'ssp_adapter';
const DEFAULT_ENDPOINT = 'https://ssp.yourdomain.com/v1/bid';  // 本番URLに変更

export const spec = {
  code: BIDDER_CODE,
  supportedMediaTypes: [BANNER],

  /**
   * 入札リクエストが有効かチェック
   */
  isBidRequestValid(bid) {
    return !!(
      bid.params &&
      bid.params.publisherId &&
      bid.params.slotId
    );
  },

  /**
   * Prebid → SSP への入札リクエスト変換
   */
  buildRequests(validBidRequests, bidderRequest) {
    return validBidRequests.map((bid) => {
      const sizes = bid.mediaTypes?.banner?.sizes || [[300, 250]];
      const endpoint = bid.params.endpoint || DEFAULT_ENDPOINT;

      return {
        method: 'POST',
        url: endpoint,
        data: JSON.stringify({
          publisherId: bid.params.publisherId,
          slotId:      bid.params.slotId,
          floorPrice:  bid.params.floorPrice || 0.5,
          sizes:       sizes,
          bidId:       bid.bidId,
          pageUrl:     bidderRequest?.refererInfo?.page,
          referer:     bidderRequest?.refererInfo?.ref,
          gdpr:        bidderRequest?.gdprConsent?.gdprApplies || false,
          consentStr:  bidderRequest?.gdprConsent?.consentString || '',
        }),
        options: {
          contentType: 'application/json',
          withCredentials: false,
        },
        bidId: bid.bidId,
      };
    });
  },

  /**
   * SSP レスポンス → Prebid 入札オブジェクト変換
   */
  interpretResponse(serverResponse, request) {
    const responseBody = serverResponse.body;
    if (!responseBody?.bids?.length) return [];

    return responseBody.bids.map((bid) => ({
      requestId:   request.bidId,
      cpm:         bid.cpm,
      currency:    'USD',
      width:       bid.width,
      height:      bid.height,
      ad:          bid.ad,
      ttl:         bid.ttl || 30,
      creativeId:  bid.winToken,
      netRevenue:  bid.netRevenue,
      meta: {
        advertiserDomains: [],
      },
    }));
  },

  /**
   * 落札通知（Prebid が自動でコール）
   */
  onBidWon(bid) {
    if (!bid.creativeId) return;
    const endpoint = bid.params?.endpoint || DEFAULT_ENDPOINT;
    const winUrl = endpoint.replace('/v1/bid', '/v1/win') +
      `?token=${bid.creativeId}&price=${bid.cpm}`;
    ajax(winUrl, null, undefined, { method: 'GET' });
  },
};

registerBidder(spec);
