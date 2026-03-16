"""GTMコンテナID自動埋め込み

広告主がGTMコンテナIDを登録すると、WebクリップLPにGTMスニペットが
自動挿入される。広告主の既存GA4/CVタグがそのまま動作する。
"""

_GTM_SNIPPET_HEAD = """\
<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){{w[l]=w[l]||[];w[l].push({{'gtm.start':
new Date().getTime(),event:'gtm.js'}});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
}})(window,document,'script','dataLayer','{container_id}');</script>
<!-- End Google Tag Manager -->"""

_GTM_SNIPPET_BODY = """\
<!-- Google Tag Manager (noscript) -->
<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={container_id}"
height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>
<!-- End Google Tag Manager (noscript) -->"""

_LP_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  {gtm_head}
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Hiragino Sans', sans-serif;
      background: #f5f5f7; color: #1d1d1f; min-height: 100vh;
    }}
    .container {{ max-width: 480px; margin: 0 auto; padding: 24px 20px 40px; }}
    .hero {{
      background: #fff; border-radius: 16px; padding: 32px 24px;
      box-shadow: 0 2px 12px rgba(0,0,0,0.08); margin-bottom: 16px; text-align: center;
    }}
    .hero h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 12px; }}
    .hero p {{ font-size: 14px; color: #6e6e73; line-height: 1.6; }}
    .cta {{
      display: block; width: 100%; padding: 18px; font-size: 17px;
      font-weight: 700; text-align: center; background: #007aff; color: #fff;
      border: none; border-radius: 14px; text-decoration: none;
      margin-top: 24px; cursor: pointer;
    }}
    .note {{ font-size: 12px; color: #6e6e73; text-align: center; margin-top: 12px; }}
    .token-hidden {{ display: none; }}
  </style>
</head>
<body>
  {gtm_body}
  <div class="container">
    <div class="hero">
      <h1>{title}</h1>
      <p>{description}</p>
      <a href="{cta_url}" class="cta" id="cta-btn"
         onclick="trackClick()">{cta_label}</a>
      <p class="note">タップするとサービスページに移動します</p>
    </div>
  </div>
  <input type="hidden" class="token-hidden" id="enrollment-token" value="{enrollment_token}">
  <input type="hidden" class="token-hidden" id="click-token" value="{click_token}">
  <script>
    function trackClick() {{
      // GTMのdataLayerにクリックイベントをpush（広告主のCVタグが反応する）
      if (window.dataLayer) {{
        window.dataLayer.push({{
          event: 'affiliate_click',
          campaign_id: '{campaign_id}',
          enrollment_token: document.getElementById('enrollment-token').value,
        }});
      }}
    }}
    // ページビューをdataLayerに記録
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({{
      event: 'page_view',
      campaign_id: '{campaign_id}',
      platform: /iPhone|iPad/.test(navigator.userAgent) ? 'ios' : 'android',
    }});
  </script>
</body>
</html>"""


def build_lp_html(
    campaign_id: str,
    title: str,
    description: str,
    cta_url: str,
    cta_label: str = "今すぐ申し込む",
    gtm_container_id: str | None = None,
    enrollment_token: str = "",
    click_token: str = "",
) -> str:
    """
    GTMスニペット付きランディングページHTMLを生成する。

    Args:
        campaign_id:      アフィリエイト案件ID
        title:            LPのタイトル（例: 「NordVPN を試してみませんか？」）
        description:      説明テキスト
        cta_url:          CTAボタンの遷移先URL（広告主LP or App Store）
        cta_label:        CTAボタンのラベル
        gtm_container_id: GTM-XXXXXX形式。Noneの場合はGTMなし。
        enrollment_token: デバイストークン（追跡用）
        click_token:      クリックトークン（追跡用）

    Returns:
        HTML文字列
    """
    if gtm_container_id:
        gtm_head = _GTM_SNIPPET_HEAD.format(container_id=gtm_container_id)
        gtm_body = _GTM_SNIPPET_BODY.format(container_id=gtm_container_id)
    else:
        gtm_head = ""
        gtm_body = ""

    return _LP_TEMPLATE.format(
        title=title,
        description=description,
        cta_url=cta_url,
        cta_label=cta_label,
        gtm_head=gtm_head,
        gtm_body=gtm_body,
        campaign_id=campaign_id,
        enrollment_token=enrollment_token,
        click_token=click_token,
    )
