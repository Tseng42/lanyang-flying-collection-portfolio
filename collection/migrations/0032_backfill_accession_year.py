"""第二步：回填 accession_year 後，將欄位改為 null=False。

回填規則（依序嘗試，不自動修正異常年份，一律以編號為準）：
  a. 從 catalog_number 以正規表示式解析出四位年份
  b. 解析失敗 → 取 created_at 的年份
  c. 再失敗 → 填入 2026
"""

import re

import collection.models
import django.core.validators
from django.db import migrations, models

# 典藏編號中的四位年份（第三段）；不符標準格式者交由後續 fallback 處理
CATALOG_YEAR_RE = re.compile(r"^LYM-[A-Z]{2}-(\d{4})-\d{4}$")


def backfill_accession_year(apps, schema_editor):
    Specimen = apps.get_model("collection", "specimen")
    for specimen in Specimen.objects.all():
        match = CATALOG_YEAR_RE.match(specimen.catalog_number or "")
        if match:
            year = int(match.group(1))          # a. 以編號為準
        elif specimen.created_at:
            year = specimen.created_at.year      # b. 退回建檔年
        else:
            year = 2026                          # c. 最終預設
        specimen.accession_year = year
        specimen.save(update_fields=["accession_year"])


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0031_specimen_accession_year"),
    ]

    operations = [
        migrations.RunPython(backfill_accession_year, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="specimen",
            name="accession_year",
            field=models.PositiveIntegerField(
                default=collection.models.current_year,
                help_text="館方正式接受本件標本進入典藏的年度，非採集年。",
                validators=[
                    django.core.validators.MinValueValidator(1900),
                    django.core.validators.MaxValueValidator(
                        collection.models.max_accession_year
                    ),
                ],
                verbose_name="入藏年份",
            ),
        ),
    ]
