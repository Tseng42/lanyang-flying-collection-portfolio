"""建立 can_export_full_data 權限後，指派給「典藏主管」群組。

權限由 Specimen.Meta.permissions 自動建立（見 0026）；此資料遷移只負責指派給
「典藏主管」。群組不存在則略過、不報錯（sync_groups 之後會補齊）。不指派給其他群組。

此權限與 can_publish_* 互相獨立，彼此無關。
"""

from django.db import migrations

CODENAME = "can_export_full_data"
PERM_NAME = "可匯出全部典藏資料"
GROUP_NAME = "典藏主管"


def assign(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")

    try:
        group = Group.objects.get(name=GROUP_NAME)
    except Group.DoesNotExist:
        return

    # can_export_full_data 掛在 Specimen 的 content type 底下
    content_type, _ = ContentType.objects.get_or_create(
        app_label="collection", model="specimen",
    )
    perm, _ = Permission.objects.get_or_create(
        content_type=content_type,
        codename=CODENAME,
        defaults={"name": PERM_NAME},
    )
    group.permissions.add(perm)


def unassign(apps, schema_editor):
    """回滾：僅將此權限從群組移除（不刪除權限本身）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    try:
        group = Group.objects.get(name=GROUP_NAME)
    except Group.DoesNotExist:
        return

    for perm in Permission.objects.filter(
        content_type__app_label="collection", codename=CODENAME,
    ):
        group.permissions.remove(perm)


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0026_alter_specimen_options"),
    ]

    operations = [
        migrations.RunPython(assign, unassign),
    ]
