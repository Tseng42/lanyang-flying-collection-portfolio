"""一次性緊急管理員存取指令（供 Render 免費方案無 Shell 時診斷/重設管理員）。

設計原則：
- **永遠不會讓部署失敗**：所有邏輯包在 try/except，任何例外印訊息後正常結束，
  退出碼一律 0，絕不 raise。
- 一律先印使用者診斷；再視環境變數決定是否重設/建立。
- 密碼只來自環境變數 EMERGENCY_ADMIN_PASSWORD，程式碼不含任何預設或明文密碼，
  且絕不將密碼本身、長度或任何可推測密碼的資訊印出或寫檔。

用完務必移除 Build Command 的附加片段與這兩個環境變數。
"""

import os

from django.core.management.base import BaseCommand


def _mask_email(email):
    """只顯示第一個字元與網域，例如 tseng@example.com -> t***@example.com。"""
    if not email:
        return "(無)"
    if "@" not in email:
        return f"{email[0]}***"        # 非標準格式：僅露首字
    local, domain = email.split("@", 1)
    first = local[0] if local else ""
    return f"{first}***@{domain}"


class Command(BaseCommand):
    help = (
        "緊急管理員存取：診斷使用者，並在提供環境變數時重設/建立管理員。"
        "永不使部署失敗、絕不印出密碼。"
    )

    def handle(self, *args, **options):
        # 最外層攔截一切例外 → 印訊息後正常結束（退出碼 0），絕不 raise。
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 — 緊急指令，絕不可讓部署失敗
            self.stdout.write(
                f"[emergency_admin_access] 發生例外，已忽略，不影響部署：{exc!r}"
            )
        # 不呼叫 sys.exit(非 0)、不 raise → Django 正常結束，退出碼 0

    def _run(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        uname_field = User.USERNAME_FIELD

        # ── 1) 一律先印使用者診斷 ──
        self.stdout.write("[emergency_admin_access] === 使用者診斷 ===")
        total = 0
        for u in User.objects.all().order_by(uname_field):
            total += 1
            self.stdout.write(
                "  "
                f"username={getattr(u, uname_field, '?')} | "
                f"is_active={u.is_active} | "
                f"is_staff={u.is_staff} | "
                f"is_superuser={u.is_superuser} | "
                f"has_usable_password={u.has_usable_password()} | "
                f"last_login={u.last_login} | "
                f"email={_mask_email(getattr(u, 'email', ''))}"
            )
        self.stdout.write(f"[emergency_admin_access] 使用者總數：{total}")
        if total == 0:
            self.stdout.write(
                "[emergency_admin_access] ⚠ 警告：線上資料庫沒有任何使用者。"
            )

        # ── 2) 讀取重設參數（密碼只來自環境變數，無預設）──
        username = os.environ.get("EMERGENCY_ADMIN_USERNAME")
        password = os.environ.get("EMERGENCY_ADMIN_PASSWORD")
        if not username or not password:
            self.stdout.write(
                "[emergency_admin_access] 未提供重設參數，僅執行診斷"
            )
            return

        # ── 3) 重設既有帳號或建立新 superuser（不印任何密碼資訊）──
        existing = User.objects.filter(**{uname_field: username}).first()
        if existing is not None:
            existing.set_password(password)
            existing.is_active = True
            existing.is_staff = True
            existing.is_superuser = True
            existing.save()
            self.stdout.write(f"[emergency_admin_access] 已重設帳號：{username}")
        else:
            User.objects.create_superuser(**{uname_field: username, "password": password})
            self.stdout.write(f"[emergency_admin_access] 已建立帳號：{username}")
