"""iOS MDM コマンド plist ビルダー

MDMプロトコルのコマンドをplist形式で生成する。
NanoMDMはこのplistをiOSデバイスへ配信する。

サポートするコマンド:
  add_web_clip                - ホーム画面にWebクリップ追加
  install_profile             - 構成プロファイルインストール
  remove_profile              - 構成プロファイル削除
  device_info                 - デバイス情報取得
  profile_list                - インストール済みプロファイル一覧取得
  device_lock                 - デバイスロック
  install_application         - App Store / エンタープライズアプリインストール
  install_enterprise_application - In-Houseアプリインストール
  send_app_clip_invite        - App Clip起動URL送信
"""
import plistlib
import uuid


def _base_command(request_type: str, command_uuid: str | None = None) -> dict:
    return {
        "Command": {"RequestType": request_type},
        "CommandUUID": command_uuid or str(uuid.uuid4()),
    }


def add_web_clip(url: str, label: str, full_screen: bool = True, command_uuid: str | None = None) -> bytes:
    """
    ホーム画面にWebクリップを追加するMDMコマンドを生成する。
    MDM管理のWebクリップはユーザーが削除できない（IsRemovable=False）。

    Apple MDM仕様: WebClipはInstallProfile + com.apple.webClip.managed ペイロードで配信する。
    （InstallMedia はBooks/PDF用であり、WebClipには使用できない）

    Args:
        url:   WebクリップのURL
        label: ホーム画面に表示するラベル（最大12文字推奨）
        full_screen: フルスクリーン表示（Safariバー非表示）

    Returns:
        plist XML bytes
    """
    profile_uuid = str(uuid.uuid4())
    webclip_profile = {
        "PayloadContent": [{
            "PayloadType": "com.apple.webClip.managed",
            "PayloadVersion": 1,
            "PayloadIdentifier": f"com.platform.webclip.{uuid.uuid4()}",
            "PayloadUUID": str(uuid.uuid4()),
            "PayloadDisplayName": label,
            "URL": url,
            "Label": label,
            "FullScreen": full_screen,
            "IsRemovable": False,
        }],
        "PayloadDisplayName": f"Webクリップ: {label}",
        "PayloadIdentifier": f"com.platform.webclip.profile.{profile_uuid}",
        "PayloadOrganization": "Platform",
        "PayloadType": "Configuration",
        "PayloadUUID": profile_uuid,
        "PayloadVersion": 1,
    }
    cmd = _base_command("InstallProfile", command_uuid)
    cmd["Command"]["Payload"] = webclip_profile
    return plistlib.dumps(cmd, fmt=plistlib.FMT_XML)


def install_configuration_profile(profile_plist: bytes, command_uuid: str | None = None) -> bytes:
    """
    構成プロファイル（VPN/証明書/Wi-Fi等）をインストールするMDMコマンド。

    Args:
        profile_plist: インストールする .mobileconfig の plist bytes

    Returns:
        plist XML bytes
    """
    cmd = _base_command("InstallProfile", command_uuid)
    cmd["Command"]["Payload"] = plistlib.loads(profile_plist)
    return plistlib.dumps(cmd, fmt=plistlib.FMT_XML)


def remove_profile(profile_identifier: str, command_uuid: str | None = None) -> bytes:
    """
    構成プロファイルをアンインストールするMDMコマンド。

    Args:
        profile_identifier: プロファイルのPayloadIdentifier
    """
    cmd = _base_command("RemoveProfile", command_uuid)
    cmd["Command"]["Identifier"] = profile_identifier
    return plistlib.dumps(cmd, fmt=plistlib.FMT_XML)


def get_device_info(command_uuid: str | None = None) -> bytes:
    """デバイス情報を取得するMDMコマンド（機種/OS/名前/容量等）"""
    cmd = _base_command("DeviceInformation", command_uuid)
    cmd["Command"]["Queries"] = [
        "DeviceName", "OSVersion", "BuildVersion",
        "ModelName", "Model", "ProductName",
        "SerialNumber", "UDID", "IMEI",
        "AvailableDeviceCapacity", "DeviceCapacity",
        "BatteryLevel",
    ]
    return plistlib.dumps(cmd, fmt=plistlib.FMT_XML)


def get_profile_list(command_uuid: str | None = None) -> bytes:
    """インストール済みプロファイル一覧を取得するMDMコマンド"""
    cmd = _base_command("ProfileList", command_uuid)
    return plistlib.dumps(cmd, fmt=plistlib.FMT_XML)


def install_application(
    manifest_url: str,
    management_flags: int = 1,
    command_uuid: str | None = None,
) -> bytes:
    """
    InstallApplication MDMコマンド。
    App Store / エンタープライズ配布アプリをデバイスにインストールする。
    management_flags=1: App is managed (removed on unenroll)
    """
    cmd = _base_command("InstallApplication", command_uuid)
    cmd["Command"].update({
        "ManifestURL": manifest_url,
        "ManagementFlags": management_flags,
        "Options": {"PurchaseMethod": 0},
    })
    return plistlib.dumps(cmd)


def install_enterprise_application(
    manifest_url: str,
    command_uuid: str | None = None,
) -> bytes:
    """
    エンタープライズ（In-House）アプリのインストール。
    App Storeを経由せず直接配布する場合に使用。
    """
    cmd = _base_command("InstallEnterpriseApplication", command_uuid)
    cmd["Command"].update({
        "ManifestURL": manifest_url,
    })
    return plistlib.dumps(cmd)


def send_app_clip_invite(
    app_clip_url: str,
    command_uuid: str | None = None,
) -> bytes:
    """
    App Clip起動URLをデバイスに送信する（通知経由）。
    """
    cmd = _base_command("Settings", command_uuid)
    cmd["Command"]["Settings"] = [{
        "Item": "ApplicationAttributes",
        "Identifier": "com.apple.AppClip",
        "Attributes": {"AppClipURL": app_clip_url},
    }]
    return plistlib.dumps(cmd)


def device_lock(pin: str | None = None, message: str = "", phone: str = "", command_uuid: str | None = None) -> bytes:
    """
    デバイスをロックするMDMコマンド（紛失時の管理用）。

    Args:
        pin:     新しいパスコード（6桁）。None の場合は既存パスコードを維持。
        message: ロック画面に表示するメッセージ
        phone:   連絡先電話番号
    """
    cmd = _base_command("DeviceLock", command_uuid)
    if message:
        cmd["Command"]["Message"] = message
    if phone:
        cmd["Command"]["PhoneNumber"] = phone
    if pin:
        cmd["Command"]["PIN"] = pin
    return plistlib.dumps(cmd, fmt=plistlib.FMT_XML)
