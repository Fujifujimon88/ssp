"""
Prebid.js ヘッダービディングタグの自動生成
パブリッシャーが<head>に貼るだけで複数DSPへの入札が有効になる
"""
from publisher.models import AdSlot, Publisher


SSP_ENDPOINT = "https://ssp.yourdomain.com"  # 本番エンドポイント（要変更）


def generate_prebid_tag(publisher: Publisher, slots: list[AdSlot]) -> str:
    """
    Prebid.js タグHTML文字列を生成する。

    Returns:
        パブリッシャーが <head> に貼り付けるHTMLスクリプトタグ
    """
    ad_units = _build_ad_units(publisher, slots)
    return f"""<!-- SSP Header Bidding Tag (generated) -->
<!-- Publisher: {publisher.name} | ID: {publisher.id} -->
<script async src="https://cdn.jsdelivr.net/npm/prebid.js@latest/dist/not-for-prod/prebid.js"></script>
<script>
var SSP_CONFIG = {{
  publisherId: "{publisher.id}",
  endpoint:    "{SSP_ENDPOINT}"
}};

var adUnits = {ad_units};

var pbjs = pbjs || {{}};
pbjs.que = pbjs.que || [];

pbjs.que.push(function() {{
  pbjs.addAdUnits(adUnits);
  pbjs.requestBids({{
    bidsBackHandler: function(bids) {{
      pbjs.setTargetingForGPTAsync();
      googletag.pubads().refresh();
    }},
    timeout: 1000
  }});
}});
</script>
<!-- Google Publisher Tag（AdSenseと併用可） -->
<script async src="https://securepubads.g.doubleclick.net/tag/js/gpt.js"></script>
<script>
var googletag = googletag || {{}};
googletag.cmd = googletag.cmd || [];
googletag.cmd.push(function() {{
  googletag.pubads().enableSingleRequest();
  googletag.enableServices();
}});
</script>"""


def generate_slot_div(slot: AdSlot) -> str:
    """広告スロットに対応する<div>タグを生成する"""
    import json as _json
    sizes = slot.effective_sizes()
    primary = sizes[0]
    w, h = primary[0], primary[1]
    sizes_json = _json.dumps(sizes)
    return f"""<!-- 広告スロット: {slot.name} (sizes: {sizes_json}) -->
<div id='ssp-slot-{slot.tag_id}' style='width:{w}px;height:{h}px;'>
  <script>
  googletag.cmd.push(function() {{
    googletag.defineSlot('/your-network-code/ssp-{slot.tag_id}', {sizes_json}, 'ssp-slot-{slot.tag_id}')
      .addService(googletag.pubads());
    googletag.display('ssp-slot-{slot.tag_id}');
  }});
  </script>
</div>"""


def _build_ad_units(publisher: Publisher, slots: list[AdSlot]) -> str:
    """Prebid.jsのadUnits配列を文字列で生成"""
    import json as _json
    units = []
    for slot in slots:
        if not slot.active:
            continue
        sizes = slot.effective_sizes()
        floor = slot.floor_price or publisher.floor_price
        sizes_json = _json.dumps(sizes)

        unit = f"""  {{
    code: 'ssp-slot-{slot.tag_id}',
    mediaTypes: {{
      banner: {{
        sizes: {sizes_json}
      }}
    }},
    bids: [{{
      bidder: 'ssp_adapter',
      params: {{
        publisherId: '{publisher.id}',
        slotId:      '{slot.tag_id}',
        floorPrice:  {floor},
        sizes:       {sizes_json},
        endpoint:    '{SSP_ENDPOINT}/v1/bid'
      }}
    }}]
  }}"""
        units.append(unit)

    return "[\n" + ",\n".join(units) + "\n]"
