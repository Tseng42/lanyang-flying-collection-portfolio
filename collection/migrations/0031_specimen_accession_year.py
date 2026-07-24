"""第一步：新增 accession_year 欄位，暫時 null=True（供下一個 migration 回填）。"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('collection', '0030_assign_image_subtable_permissions'),
    ]

    operations = [
        migrations.AddField(
            model_name='specimen',
            name='accession_year',
            field=models.PositiveIntegerField(
                null=True,
                verbose_name='入藏年份',
                help_text='館方正式接受本件標本進入典藏的年度，非採集年。',
            ),
        ),
    ]
