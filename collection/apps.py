from django.apps import AppConfig


class CollectionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'collection'

    def ready(self):
        from django.db.models.signals import post_migrate, pre_migrate
        # 每次 migrate 前自動快照資料庫，避免結構變更造成資料遺失
        pre_migrate.connect(_backup_before_migrate, sender=self)
        # 每次 migrate 後自動建立/校正權限群組（權限此時已存在）
        from .permissions import sync_groups
        post_migrate.connect(sync_groups, sender=self)


def _backup_before_migrate(sender, **kwargs):
    from collection.backup import backup_sqlite
    dest = backup_sqlite(label="pre-migrate")
    if dest is not None:
        print(f"[自動備份] migrate 前已快照資料庫：{dest}")
