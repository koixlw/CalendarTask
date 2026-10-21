"""各服务商 CalDAV 配置预设。

三大主流:
  Google    — 用 Google 账号密码 + 应用专用密码 (App Password)
  iCloud    — Apple ID + 应用专用密码 (需先在 appleid.apple.com 生成)
  Outlook   — Microsoft 账号 + 应用密码 (需先在 account.microsoft.com 生成)

通用 CalDAV:
  Nextcloud / Baidu Calendar / 任何 RFC 4791 服务端 — 用户自填 URL

安全说明:
  - 应用专用密码 (App Password) 而非主账号密码 — 服务商明确推荐的做法
  - 凭据只落本地 DB (见 accounts.py 的 obfuscate/deobfuscate)
  - 不做 OAuth: OAuth 需 client_id/secret 申请, MVP 阶段先走 app password
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ProviderSpec:
    key: str
    label: str
    base_url: str
    username_hint: str = ""
    password_hint: str = ""
    needs_app_password: bool = True
    app_password_url: str = ""
    notes: str = ""


PRESETS: dict[str, ProviderSpec] = {
    "google": ProviderSpec(
        key="google",
        label="Google Calendar",
        # Google 的 CalDAV 端点需要先 discover, 这里给 principal URL
        base_url="https://apidata.googleusercontent.com/caldav/v2/",
        username_hint="你的 Gmail 地址 (user@gmail.com)",
        password_hint="应用专用密码 (16 位, 无空格)",
        needs_app_password=True,
        app_password_url="https://myaccount.google.com/apppasswords",
        notes=(
            "步骤:\n"
            "1. 开启两步验证: https://myaccount.google.com/security\n"
            "2. 生成应用专用密码: https://myaccount.google.com/apppasswords\n"
            "3. 选 'Calendar' → 生成 16 位密码\n"
            "4. 用户名填完整 Gmail, 密码填那 16 位 (不要空格)"
        ),
    ),
    "icloud": ProviderSpec(
        key="icloud",
        label="Apple iCloud",
        base_url="https://caldav.icloud.com/",
        username_hint="你的 Apple ID (需开启 iCloud 日历)",
        password_hint="应用专用密码",
        needs_app_password=True,
        app_password_url="https://account.apple.com/manage",
        notes=(
            "步骤:\n"
            "1. 确认已开启 iCloud 日历 (iPhone/iPad/Mac 上登录并启用)\n"
            "2. 生成应用专用密码: https://account.apple.com/manage\n"
            "3. 找 '应用专用密码' → 点 '创建新密码'\n"
            "4. 生成后立刻显示一次, 复制保存 (关掉就看不到了)"
        ),
    ),
    "outlook": ProviderSpec(
        key="outlook",
        label="Microsoft Outlook / 365",
        base_url="https://outlook.office365.com/caldav/v2/",
        username_hint="你的 Microsoft 账号邮箱",
        password_hint="应用密码",
        needs_app_password=True,
        app_password_url="https://account.microsoft.com/security",
        notes=(
            "步骤:\n"
            "1. 确认账号已启用两步验证\n"
            "2. 生成应用密码: https://account.microsoft.com/security\n"
            "3. 选 '创建新应用密码' → 复制生成的值\n"
            "4. 某些企业/教育账号被管理员禁用此功能, 需联系管理员"
        ),
    ),
    "nextcloud": ProviderSpec(
        key="nextcloud",
        label="Nextcloud / 通用 CalDAV",
        base_url="",
        username_hint="CalDAV 用户名",
        password_hint="CalDAV 密码或 token",
        needs_app_password=False,
        app_password_url="",
        notes=(
            "填写你服务器的 CalDAV URL。\n"
            "常见格式:\n"
            "  https://your-server/remote.php/dav/calendars/<user>/\n"
            "  https://your-server/remote.php/dav/calendars/<user>/personal/"
            "\n"
            "在 Nextcloud 里: 设置 → 集成 → 日历 → 查看 CalDAV 地址"
        ),
    ),
}


def get_preset(key: str) -> ProviderSpec:
    return PRESETS.get(key, PRESETS["nextcloud"])


__all__ = ["ProviderSpec", "PRESETS", "get_preset"]