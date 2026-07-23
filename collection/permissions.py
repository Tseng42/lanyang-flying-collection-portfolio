"""權限分級：自動建立使用者群組、自訂能力權限，並指派權限。

透過 post_migrate 訊號在每次 migrate 後執行（見 apps.py），
確保模型權限已存在，且每次都會校正成下列設定（idempotent）。
"""

from django.contrib.auth.models import Group, Permission

# 本系統納入權限控管的模型（皆屬 collection app）。
# 影像子表 speciesimage／observationimage 一併納入，權限比照 specimenimage，
# 讓典藏主管／登錄員能新增編修物種影像與觀察影像（見 GROUP_ACTIONS）。
MANAGED_MODELS = [
    "species",         # 物種
    "specimen",        # 標本
    "observation",     # 觀察紀錄
    "identification",  # 鑑定歷程
    "movement",        # 異動紀錄
    "specimenimage",   # 標本影像
    "speciesimage",    # 物種影像
    "observationimage",  # 觀察紀錄影像
]

# 自訂「能力」權限（與單一模型無關，掛在 Specimen 的 content type 底下）
CUSTOM_PERMISSIONS = [
    ("can_backup_database", "可以備份資料庫（匯出完整資料）"),
    ("can_restore_database", "可以還原資料庫"),
]

# 群組 → 對六個模型允許的動作
GROUP_ACTIONS = {
    "登錄員": ["add", "change", "view"],
    "唯讀研究員": ["view"],
    "典藏主管": ["add", "change", "delete", "view"],
    "管理員": ["add", "change", "delete", "view"],
}

# 群組 → 額外的自訂能力權限（codename）
# 註：can_restore_database 目前不指派給任何群組 → 只有 superuser 可還原；
#     日後要開放給典藏主管，只需在此為「典藏主管」加入 "can_restore_database" 一行。
GROUP_EXTRA_PERMS = {
    "典藏主管": ["can_backup_database"],
    "管理員": ["can_backup_database"],
}

# 三個「設為公開」自訂權限（由各模型 Meta.permissions 自動建立，分屬各自的
# content type）。因 sync_groups 以 .set() 覆寫群組權限，這裡必須一併納入，
# 否則 post_migrate 會把 0024 資料遷移指派的公開權限重新清掉。
PUBLISH_PERMISSIONS = [
    "can_publish_species",
    "can_publish_specimen",
    "can_publish_observation",
]

# 群組 → 「設為公開」權限。權限分級為 Superuser／典藏主管／登錄員／唯讀研究員
# 四層，公開權只屬「典藏主管」。「管理員」不在此四層設計內（用途待釐清），
# 故不指派公開權；其餘角色亦不得設為公開。
GROUP_PUBLISH_PERMS = {
    "典藏主管": PUBLISH_PERMISSIONS,
}

# 群組 → 「全欄位匯出」權限（Specimen.Meta.permissions 的 can_export_full_data）。
# 與公開權互相獨立；只指派給「典藏主管」。同樣必須在此登記，否則 sync_groups
# 以 .set() 覆寫時會清掉 0027 資料遷移的指派。
GROUP_EXPORT_PERMS = {
    "典藏主管": ["can_export_full_data"],
}


def _publish_permissions(codenames):
    """依 codename 取回「設為公開」權限（跨三個模型的 content type）。"""
    if not codenames:
        return []
    return list(
        Permission.objects.filter(
            content_type__app_label="collection",
            codename__in=codenames,
        )
    )


def _model_permissions(actions):
    codenames = [
        f"{action}_{model}"
        for model in MANAGED_MODELS
        for action in actions
    ]
    return list(
        Permission.objects.filter(
            content_type__app_label="collection",
            codename__in=codenames,
        )
    )


def _ensure_custom_permissions():
    """建立自訂能力權限，回傳 {codename: Permission}。"""
    from django.contrib.contenttypes.models import ContentType

    from .models import Specimen

    content_type = ContentType.objects.get_for_model(Specimen)
    result = {}
    for codename, name in CUSTOM_PERMISSIONS:
        perm, _ = Permission.objects.get_or_create(
            codename=codename,
            content_type=content_type,
            defaults={"name": name},
        )
        result[codename] = perm
    return result


def sync_groups(**kwargs):
    """建立/更新群組與能力權限，並把每個群組的權限校正成上述設定。"""
    custom = _ensure_custom_permissions()
    for name, actions in GROUP_ACTIONS.items():
        group, _ = Group.objects.get_or_create(name=name)
        perms = _model_permissions(actions)
        perms += [
            custom[codename]
            for codename in GROUP_EXTRA_PERMS.get(name, [])
            if codename in custom
        ]
        # 「設為公開」權限（分屬三個模型的 content type，非 custom 那批）
        perms += _publish_permissions(GROUP_PUBLISH_PERMS.get(name, []))
        # 「全欄位匯出」權限（掛在 Specimen content type，依 codename 取回）
        perms += _publish_permissions(GROUP_EXPORT_PERMS.get(name, []))
        # 用 set() 覆寫，確保與設定一致（含日後調整時的校正）
        group.permissions.set(perms)
