"""資料清理：去除「目／科／屬」三欄的頭尾空白。

背景：這三欄是自由輸入欄位（非 choices），若資料是透過還原備份（loaddata）
寫入，會繞過表單與 `Species.save()` 的清理邏輯（見 apps.py 的
`_normalize_species_taxonomy` 訊號），導致頭尾多了看不見的空白字元
（半形／全形空白等）。這類值在資料庫層級跟「乾淨」的值是不同字串，
公開查詢頁的下拉選單以 `.distinct()` 取值時，就會把同一個目／科／屬
列成好幾筆看起來相同卻「不同」的選項。

本遷移只做 strip，不合併、不臆測拼字錯誤；若 strip 後仍有重複值屬於
資料內容問題（例如同一個目被打成不同名稱），不在此處理，留給人工核校。
具冪等性，可安全重跑；反向遷移設為 no-op（無法安全還原「原本就有的空白」）。
"""

from django.db import migrations

LOG_PREFIX = "[strip-taxonomy-whitespace]"
FIELDS = ("order", "family", "genus")


def strip_taxonomy_whitespace(apps, schema_editor):
    Species = apps.get_model("collection", "species")

    changed = 0
    for sp in Species.objects.all():
        updates = {}
        for field in FIELDS:
            value = getattr(sp, field) or ""
            stripped = value.strip()
            if stripped != value:
                updates[field] = stripped
        if updates:
            for field, value in updates.items():
                setattr(sp, field, value)
            sp.save(update_fields=list(updates.keys()))
            changed += 1
            done = "／".join(f"{k}={v!r}" for k, v in updates.items())
            print(f"{LOG_PREFIX} 清理 {sp.scientific_name}：{done}")

    print(f"{LOG_PREFIX} 完成：清理 {changed} 筆物種。")


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0036_backfill_bird_taxonomy"),
    ]

    operations = [
        migrations.RunPython(
            strip_taxonomy_whitespace, migrations.RunPython.noop
        ),
    ]
