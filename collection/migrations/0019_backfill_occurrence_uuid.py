"""為既有 Specimen 逐筆回填各自不同的 occurrence_uuid，再加上 unique 約束。

- 逐筆指派（每列各呼叫一次 uuid.uuid4()），確保既有資料不共用同一個 uuid。
- 回填完成後才 AlterField 加上 unique=True。
- 反向為 noop：不刪除、不改動任何資料。
"""

import uuid

from django.db import migrations, models


def assign_unique_uuids(apps, schema_editor):
    Specimen = apps.get_model("collection", "Specimen")
    db = schema_editor.connection.alias
    for sp in Specimen.objects.using(db).all().iterator():
        sp.occurrence_uuid = uuid.uuid4()          # 每列各自產生，不共用
        sp.save(update_fields=["occurrence_uuid"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0018_specimen_occurrence_uuid"),
    ]

    operations = [
        migrations.RunPython(assign_unique_uuids, noop_reverse),
        migrations.AlterField(
            model_name="specimen",
            name="occurrence_uuid",
            field=models.UUIDField(
                default=uuid.uuid4, editable=False, unique=True, db_index=True,
                help_text="系統自動產生，用於對外發布與引用，產生後不得變更。",
                verbose_name="全球唯一識別碼",
            ),
        ),
    ]
