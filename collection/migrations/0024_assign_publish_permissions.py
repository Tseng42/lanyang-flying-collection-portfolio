"""建立三個「設為公開」權限後，指派給「典藏主管」群組。

權限本身由各模型的 Meta.permissions 自動建立（見 0023）；此資料遷移負責把它們
指派給名為「典藏主管」的 Group。若該 Group 尚不存在（例如全新資料庫、群組稍後才由
post_migrate 的 sync_groups 建立），則略過、不報錯——後續 sync_groups 會再補齊
（見 collection/permissions.py），兩處保持一致（idempotent）。
"""

from django.db import migrations

# (model, codename, 權限名稱)
PUBLISH_PERMS = [
    ("species", "can_publish_species", "可將物種設為公開"),
    ("specimen", "can_publish_specimen", "可將標本設為公開"),
    ("observation", "can_publish_observation", "可將觀察紀錄設為公開"),
]

GROUP_NAME = "典藏主管"


def assign_publish_perms(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    try:
        group = Group.objects.get(name=GROUP_NAME)
    except Group.DoesNotExist:
        # 群組尚不存在 → 略過，不報錯（sync_groups 之後會補齊）
        return

    for model_name, codename, name in PUBLISH_PERMS:
        # 權限依附於各自模型的 content type；此時通常已由自動建權建立，
        # 保險起見以 get_or_create 確保存在。
        content_type, _ = ContentType.objects.get_or_create(
            app_label="collection", model=model_name,
        )
        perm, _ = Permission.objects.get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={"name": name},
        )
        group.permissions.add(perm)


def remove_publish_perms(apps, schema_editor):
    """回滾：僅將權限從群組移除（不刪除權限本身，避免影響其他指派）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    try:
        group = Group.objects.get(name=GROUP_NAME)
    except Group.DoesNotExist:
        return

    codenames = [codename for _, codename, _ in PUBLISH_PERMS]
    for perm in Permission.objects.filter(
        content_type__app_label="collection", codename__in=codenames,
    ):
        group.permissions.remove(perm)


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0023_alter_observation_options_alter_species_options_and_more"),
    ]

    operations = [
        migrations.RunPython(assign_publish_perms, remove_publish_perms),
    ]
