"""部署時自動建立管理員帳號（idempotent、可安全重複執行）。

帳號資訊一律從環境變數讀取，不寫死在程式碼：
    DJANGO_SUPERUSER_USERNAME
    DJANGO_SUPERUSER_EMAIL      （可選）
    DJANGO_SUPERUSER_PASSWORD

規則：
- 未設 USERNAME 或 PASSWORD → 略過（不報錯），避免本機／未設定時中斷部署。
- 已存在任何 superuser，或同名使用者已存在 → 略過，不重複建立。
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "若尚無 superuser 且已設相關環境變數，則自動建立一個管理員帳號。"

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

        if not username or not password:
            self.stdout.write(
                "未設定 DJANGO_SUPERUSER_USERNAME／DJANGO_SUPERUSER_PASSWORD，"
                "略過自動建立管理員。"
            )
            return

        User = get_user_model()

        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("已存在超級使用者，略過建立。")
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(
                f"使用者「{username}」已存在（非超級使用者），略過建立以免衝突。"
            )
            return

        User.objects.create_superuser(
            username=username, email=email, password=password
        )
        self.stdout.write(self.style.SUCCESS(f"已自動建立管理員帳號：{username}"))
