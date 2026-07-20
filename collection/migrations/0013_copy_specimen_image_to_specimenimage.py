"""將已停用的 Specimen.image（單張）逐筆搬移到 SpecimenImage（可多張）。

- 逐筆處理有單張影像的標本，不使用 .all() 批次操作。
- 沿用相同 storage key（image.name），不重新上傳檔案。
- 具冪等性：同一標本、同一檔名已存在 SpecimenImage 時跳過，避免重複。
- 反向（reverse）為 noop：不自動刪除任何資料，以免誤刪館方後續編輯的影像。
- 本機無資料時整支為 no-op；正式庫的實際搬移於 push 後由 Render 自動 migrate 執行。
"""

from django.db import migrations


def copy_specimen_image(apps, schema_editor):
    Specimen = apps.get_model("collection", "Specimen")
    SpecimenImage = apps.get_model("collection", "SpecimenImage")
    db = schema_editor.connection.alias

    # 只取有單張 image 的標本（排除空字串與 NULL）；逐筆處理
    qs = (
        Specimen.objects.using(db)
        .exclude(image="")
        .exclude(image__isnull=True)
    )
    for sp in qs.iterator():
        name = sp.image.name
        if not name:
            continue
        # 已有相同檔案的對應紀錄就跳過（冪等）
        already = (
            SpecimenImage.objects.using(db)
            .filter(specimen_id=sp.pk, image=name)
            .exists()
        )
        if already:
            continue
        SpecimenImage.objects.using(db).create(
            specimen_id=sp.pk,
            image=name,            # 沿用相同 storage key，不重新上傳
            image_type="body",     # 標本本體
            # is_public / is_primary / license 等沿用欄位預設值
        )


def noop_reverse(apps, schema_editor):
    """反向不刪除任何資料（遵循「絕不批次刪除」原則）。"""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0012_alter_specimenimage_options_specimenimage_created_at_and_more"),
    ]

    operations = [
        migrations.RunPython(copy_specimen_image, noop_reverse),
    ]
