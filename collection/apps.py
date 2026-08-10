from django.apps import AppConfig


class CollectionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'collection'

    def ready(self):
        from django.db.models.signals import post_migrate, pre_migrate, pre_save
        # 每次 migrate 前自動快照資料庫，避免結構變更造成資料遺失
        pre_migrate.connect(_backup_before_migrate, sender=self)
        # 每次 migrate 後自動建立/校正權限群組（權限此時已存在）
        from .permissions import sync_groups
        post_migrate.connect(sync_groups, sender=self)
        # 存檔前清掉「目／科／屬」頭尾空白，避免看不見的差異讓查詢頁下拉
        # 選單顯示出重複選項；用 pre_save 訊號而非覆寫 save()，是因為
        # loaddata 還原備份會呼叫 save_base(raw=True) 繞過 save()，
        # 但仍會發出 pre_save 訊號，這樣還原舊備份時也能一併清理。
        from .models import Species
        pre_save.connect(_normalize_species_taxonomy, sender=Species)


def _backup_before_migrate(sender, **kwargs):
    from collection.backup import backup_sqlite
    dest = backup_sqlite(label="pre-migrate")
    if dest is not None:
        print(f"[自動備份] migrate 前已快照資料庫：{dest}")


def _normalize_species_taxonomy(sender, instance, **kwargs):
    for field in ("order", "family", "genus"):
        value = getattr(instance, field)
        if value:
            setattr(instance, field, value.strip())
