"""權限分級：自動建立三個使用者群組並指派權限。

透過 post_migrate 訊號在每次 migrate 後執行（見 apps.py），
確保模型權限已存在，且每次都會校正成下列設定（idempotent）。
"""

from django.contrib.auth.models import Group, Permission

# 本系統納入權限控管的六個模型（皆屬 collection app）
MANAGED_MODELS = [
    "species",        # 物種
    "specimen",       # 標本
    "observation",    # 觀察紀錄
    "identification",  # 鑑定歷程
    "movement",       # 異動紀錄
    "specimenimage",  # 標本影像
]

# 群組 → 允許的動作
GROUP_ACTIONS = {
    "登錄員": ["add", "change", "view"],
    "唯讀研究員": ["view"],
    "管理員": ["add", "change", "delete", "view"],
}


def _permissions_for(actions):
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


def sync_groups(**kwargs):
    """建立/更新三個群組，並把權限設定成 GROUP_ACTIONS 指定的樣子。"""
    for name, actions in GROUP_ACTIONS.items():
        group, _ = Group.objects.get_or_create(name=name)
        # 用 set() 覆寫，確保與設定一致（含日後新增模型時的校正）
        group.permissions.set(_permissions_for(actions))
