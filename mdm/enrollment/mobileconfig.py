"""iOS .mobileconfig（構成プロファイル）の動的生成"""
import plistlib
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VPNConfig:
    server: str
    username: str
    password: str
    display_name: str = "VPN"


@dataclass
class WebClipConfig:
    url: str
    label: str
    full_screen: bool = True
    is_removable: bool = True


@dataclass
class MDMConfig:
    """
    NanoMDM向けのMDMペイロード設定。

    server_url:   NanoMDMのMDMエンドポイント（例: https://mdm.example.com/nanomdm/mdm）
    topic:        APNs MDMトピック（例: com.apple.mgmt.External.XXXXXXXX）
    identity_cert_pem: デバイス認証用証明書のPEM文字列（SCEP不使用の場合）

    ※ Apple Developer Portal でMDM証明書取得後に設定する。
    """
    server_url: str
    topic: str
    identity_cert_pem: Optional[str] = None  # None の場合はデバイス識別子認証のみ


def _vpn_payload(vpn: VPNConfig) -> dict:
    return {
        "PayloadType": "com.apple.vpn.managed",
        "PayloadVersion": 1,
        "PayloadIdentifier": f"com.platform.vpn.{uuid.uuid4()}",
        "PayloadUUID": str(uuid.uuid4()),
        "PayloadDisplayName": vpn.display_name,
        "UserDefinedName": vpn.display_name,
        "VPNType": "IKEv2",
        "IKEv2": {
            "RemoteAddress": vpn.server,
            "LocalIdentifier": "client",
            "RemoteIdentifier": vpn.server,
            "AuthenticationMethod": "SharedSecret",
            "ExtendedAuthEnabled": 1,
            "AuthName": vpn.username,
            "AuthPassword": vpn.password,
            "EnablePFS": False,
            "DeadPeerDetectionRate": "Medium",
            "DisableMOBIKE": 0,
            "DisableRedirect": 0,
            "EnableCertificateRevocationCheck": 0,
            "IKESecurityAssociationParameters": {
                "EncryptionAlgorithm": "AES-256",
                "IntegrityAlgorithm": "SHA2-256",
                "DiffieHellmanGroup": 14,
                "LifeTimeInMinutes": 1440,
            },
            "ChildSecurityAssociationParameters": {
                "EncryptionAlgorithm": "AES-256",
                "IntegrityAlgorithm": "SHA2-256",
                "DiffieHellmanGroup": 14,
                "LifeTimeInMinutes": 1440,
            },
        },
    }


def _webclip_payload(clip: WebClipConfig) -> dict:
    return {
        "PayloadType": "com.apple.webClip.managed",
        "PayloadVersion": 1,
        "PayloadIdentifier": f"com.platform.webclip.{uuid.uuid4()}",
        "PayloadUUID": str(uuid.uuid4()),
        "PayloadDisplayName": clip.label,
        "URL": clip.url,
        "Label": clip.label,
        "FullScreen": clip.full_screen,
        "IsRemovable": clip.is_removable,
    }


def _mdm_payload(mdm: MDMConfig) -> dict:
    """
    MDM管理プロファイルのペイロードを生成する。
    このペイロードをインストールするとデバイスがMDMサーバーに登録される。
    """
    payload: dict = {
        "PayloadType": "com.apple.mdm",
        "PayloadVersion": 1,
        "PayloadIdentifier": f"com.platform.mdm.enrollment.{uuid.uuid4()}",
        "PayloadUUID": str(uuid.uuid4()),
        "PayloadDisplayName": "MDMエンロールメント",
        "ServerURL": mdm.server_url,
        "CheckInURL": mdm.server_url,  # NanoMDMはCheckinURLとServerURLが同一
        "Topic": mdm.topic,
        "CheckOutWhenRemoved": True,
        "AccessRights": 8191,  # すべての管理権限
        "SignMessage": False,
    }
    if mdm.identity_cert_pem:
        payload["IdentityCertificateUUID"] = str(uuid.uuid4())
    return payload


def generate_mobileconfig(
    profile_name: str = "サービス設定",
    profile_org: str = "Platform",
    enrollment_token: Optional[str] = None,
    vpn: Optional[VPNConfig] = None,
    webclips: Optional[list[WebClipConfig]] = None,
    mdm: Optional[MDMConfig] = None,
) -> bytes:
    """
    .mobileconfig（plist XML）を生成して返す。
    VPN設定・Webクリップ・MDM管理ペイロードを動的に組み合わせる。

    Args:
        mdm: MDMConfig を渡すとMDM管理プロファイルを含むフル版を生成する。
             None の場合はVPN/Webクリップのみの軽量版。
    """
    payload_content = []

    if mdm:
        payload_content.append(_mdm_payload(mdm))

    if vpn:
        payload_content.append(_vpn_payload(vpn))

    for clip in (webclips or []):
        payload_content.append(_webclip_payload(clip))

    profile = {
        "PayloadContent": payload_content,
        "PayloadDisplayName": profile_name,
        "PayloadDescription": "VPN設定とホーム画面ショートカットを自動設定します",
        "PayloadIdentifier": f"com.platform.mdm.{enrollment_token or uuid.uuid4()}",
        "PayloadOrganization": profile_org,
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": str(uuid.uuid4()),
        "PayloadVersion": 1,
    }

    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)
