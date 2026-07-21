"""新增 Specimen.occurrence_uuid（先不設 unique）。

此步僅加欄位；既有列會由 default=uuid.uuid4 得到值（Django 對既有列一次性套用
同一預設值）。真正逐筆指派不同 uuid 與加上 unique 約束於下一個 migration 進行。
"""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0017_species_public_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="specimen",
            name="occurrence_uuid",
            field=models.UUIDField(
                default=uuid.uuid4, editable=False, db_index=True,
                help_text="系統自動產生，用於對外發布與引用，產生後不得變更。",
                verbose_name="全球唯一識別碼",
            ),
        ),
    ]
