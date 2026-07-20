"""讓標本可在尚未鑑定物種下獨立建檔。

- species 改為可空（null/blank）。
- 新增 identification_status（預設「未鑑定」）。
- 新增 taxon_group：以「三段式」安全上必填——
  先加為可空 → 逐筆由關聯物種的 taxon_group 回填（值相同，直接複製；
  不使用批次覆寫）→ 再改為必填。既有標本原本皆有物種，故回填後無空值。
- 資料回填的反向為 noop：不刪除任何資料。
"""

import django.db.models.deletion
from django.db import migrations, models


TAXON_GROUP_CHOICES = [
    ("bird", "鳥類"),
    ("insect", "昆蟲"),
    ("bat", "蝙蝠"),
    ("flying_squirrel", "飛鼠"),
    ("other", "其他"),
]

IDENTIFICATION_STATUS_CHOICES = [
    ("unidentified", "未鑑定"),
    ("in_progress", "鑑定中"),
    ("to_family", "已鑑定至科"),
    ("to_genus", "已鑑定至屬"),
    ("to_species", "已鑑定至種"),
    ("unidentifiable", "無法鑑定"),
]


def backfill_taxon_group(apps, schema_editor):
    """逐筆由關聯物種回填 taxon_group（值與 Species.taxon_group 相同）。"""
    Specimen = apps.get_model("collection", "Specimen")
    db = schema_editor.connection.alias
    qs = Specimen.objects.using(db).select_related("species")
    for sp in qs.iterator():
        if sp.species_id and not sp.taxon_group:
            sp.taxon_group = sp.species.taxon_group
            sp.save(update_fields=["taxon_group"])


def noop_reverse(apps, schema_editor):
    """反向不刪除、不改動任何資料。"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0014_alter_species_conservation_status_and_more"),
    ]

    operations = [
        # 1) 鑑定狀態（有預設，既有列自動填「未鑑定」）
        migrations.AddField(
            model_name="specimen",
            name="identification_status",
            field=models.CharField(
                choices=IDENTIFICATION_STATUS_CHOICES,
                default="unidentified", max_length=20, verbose_name="鑑定狀態",
            ),
        ),
        # 2) 類群：先加為可空，讓既有列可暫時為 NULL
        migrations.AddField(
            model_name="specimen",
            name="taxon_group",
            field=models.CharField(
                choices=TAXON_GROUP_CHOICES, max_length=20, null=True,
                help_text="即使尚未鑑定到種，仍須指定類群；此欄位決定館藏編號的類群代碼。",
                verbose_name="類群",
            ),
        ),
        # 3) 逐筆回填（可回溯：reverse 為 noop）
        migrations.RunPython(backfill_taxon_group, noop_reverse),
        # 4) 回填後改為必填（既有列已無空值）
        migrations.AlterField(
            model_name="specimen",
            name="taxon_group",
            field=models.CharField(
                choices=TAXON_GROUP_CHOICES, max_length=20,
                help_text="即使尚未鑑定到種，仍須指定類群；此欄位決定館藏編號的類群代碼。",
                verbose_name="類群",
            ),
        ),
        # 5) species 改為可空（尚未鑑定者可留白）
        migrations.AlterField(
            model_name="specimen",
            name="species",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="specimens", to="collection.species",
                verbose_name="學名",
                help_text="尚未鑑定者可留白，待鑑定後再補填。",
            ),
        ),
    ]
