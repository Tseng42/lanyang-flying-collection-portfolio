"""把 speciesimage／observationimage 的模型權限指派給三個群組（比照 specimenimage）。

- 典藏主管：add / change / delete / view
- 登錄員：  add / change / view（不含 delete）
- 唯讀研究員：view

冪等：重複執行只會確保權限存在、不會出錯（用 add()，重複加入同一權限為 no-op）。
群組不存在則略過、不報錯。permissions.py 的 MANAGED_MODELS 已一併納入這兩個模型，
故 post_migrate 的 sync_groups 也會維持相同指派（.set() 不會再把它清掉）。
"""

from django.db import migrations

GROUP_ACTIONS = {
    "典藏主管": ("add", "change", "delete", "view"),
    "登錄員": ("add", "change", "view"),
    "唯讀研究員": ("view",),
}
IMAGE_MODELS = ("speciesimage", "observationimage")
ALL_ACTIONS = ("add", "change", "delete", "view")


def _ensure_permissions_exist():
    """全新部署時本遷移在 post_migrate 之前執行，這兩個模型的權限可能尚未建立，
    主動建立以確保下方抓得到；失敗也不中斷 migrate（sync_groups 之後會補齊）。"""
    try:
        from django.apps import apps as global_apps
        from django.contrib.auth.management import create_permissions
        create_permissions(
            global_apps.get_app_config("collection"), verbosity=0,
        )
    except Exception:  # noqa: BLE001 — 保底，不讓建權失敗中斷部署
        pass


def assign_image_perms(apps, schema_editor):
    _ensure_permissions_exist()
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    for group_name, actions in GROUP_ACTIONS.items():
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            continue
        codenames = [f"{a}_{m}" for m in IMAGE_MODELS for a in actions]
        perms = Permission.objects.filter(
            content_type__app_label="collection", codename__in=codenames,
        )
        group.permissions.add(*perms)


def remove_image_perms(apps, schema_editor):
    """回滾：把這兩個模型的權限自三個群組移除（不刪權限本身）。"""
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    codenames = [f"{a}_{m}" for m in IMAGE_MODELS for a in ALL_ACTIONS]
    perms = list(
        Permission.objects.filter(
            content_type__app_label="collection", codename__in=codenames,
        )
    )
    for group_name in GROUP_ACTIONS:
        group = Group.objects.filter(name=group_name).first()
        if group is None:
            continue
        group.permissions.remove(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0029_alter_species_taxon_group"),
    ]

    operations = [
        migrations.RunPython(assign_image_perms, remove_image_perms),
    ]
