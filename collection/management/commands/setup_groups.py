"""手動重建/校正權限群組：python manage.py setup_groups

與 post_migrate 時自動執行的是同一份邏輯（collection.permissions.sync_groups），
因此手動與自動結果一致。不需跑完整 migrate 也能重建群組。
"""

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from collection.permissions import GROUP_ACTIONS, sync_groups


class Command(BaseCommand):
    help = "建立/校正三個權限群組（登錄員／唯讀研究員／管理員）及其權限。"

    def handle(self, *args, **options):
        sync_groups()
        self.stdout.write(self.style.SUCCESS("已同步權限群組："))
        for name in GROUP_ACTIONS:
            group = Group.objects.get(name=name)
            actions = "、".join(GROUP_ACTIONS[name])
            count = group.permissions.count()
            self.stdout.write(f"  {name}：{count} 項權限（{actions}）")
