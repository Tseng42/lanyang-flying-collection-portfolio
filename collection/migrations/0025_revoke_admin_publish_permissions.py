"""從「管理員」群組收回三個「設為公開」權限。

背景：資料遷移 0024 只把 can_publish_* 指派給「典藏主管」，並未寫給「管理員」；
但先前 permissions.py 的 GROUP_PUBLISH_PERMS 曾一併列入「管理員」，於 post_migrate
的 sync_groups() 執行時把權限指派給了「管理員」。現決定公開權只屬「典藏主管」，
permissions.py 已移除「管理員」，本遷移再明確收回其既有指派（不改動舊 migration）。

只移除「管理員」與這三個權限的關聯，不刪除權限本身，也不動其他群組。
"""

from django.db import migrations

PUBLISH_CODENAMES = [
    "can_publish_species",
    "can_publish_specimen",
    "can_publish_observation",
]

GROUP_NAME = "管理員"


def revoke_from_admin(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    try:
        group = Group.objects.get(name=GROUP_NAME)
    except Group.DoesNotExist:
        return

    for perm in Permission.objects.filter(
        content_type__app_label="collection", codename__in=PUBLISH_CODENAMES,
    ):
        group.permissions.remove(perm)


def restore_to_admin(apps, schema_editor):
    """回滾：把權限重新指派回「管理員」（還原本遷移前的狀態）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    try:
        group = Group.objects.get(name=GROUP_NAME)
    except Group.DoesNotExist:
        return

    for perm in Permission.objects.filter(
        content_type__app_label="collection", codename__in=PUBLISH_CODENAMES,
    ):
        group.permissions.add(perm)


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0024_assign_publish_permissions"),
    ]

    operations = [
        migrations.RunPython(revoke_from_admin, restore_to_admin),
    ]
