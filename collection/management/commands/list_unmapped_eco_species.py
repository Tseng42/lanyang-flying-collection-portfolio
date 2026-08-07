"""清查查詢頁「簡易版」生態分群無法歸類的公開物種（只清查、不修改資料）。

背景：簡易版以「屬名對照表 → 已填的目 → 其他」推導生態分群。若某物種的屬名
未收錄於對照表、且「目」欄位又空白，就只能落入「其他／特殊生態」，可能不是本意。
本指令列出這些「未對應」物種，供人工補上屬名對照（collection/ecological_groups.py）
或補填該物種的「目」欄位。

用法：
    python manage.py list_unmapped_eco_species          # 只列公開物種
    python manage.py list_unmapped_eco_species --all     # 連未公開物種一併清查
"""

from django.core.management.base import BaseCommand

from collection.ecological_groups import eco_group_of, is_unmapped
from collection.models import Species


class Command(BaseCommand):
    help = "列出簡易版生態分群無法歸類（屬名未收錄且目空白）的物種。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="連未公開（草稿／待審）物種一併清查；預設只看公開物種。",
        )

    def handle(self, *args, **options):
        qs = Species.objects.all() if options["all"] else Species.objects.published()
        qs = qs.order_by("scientific_name")

        unmapped = [s for s in qs if is_unmapped(s)]
        if not unmapped:
            self.stdout.write(self.style.SUCCESS("沒有未對應的物種，全部都能歸類。"))
            return

        self.stdout.write(
            self.style.WARNING(
                f"共 {len(unmapped)} 筆物種屬名未收錄且「目」空白，"
                f"目前一律落入「其他／特殊生態」："
            )
        )
        for s in unmapped:
            name = s.common_name or "（無中文名）"
            self.stdout.write(
                f"  - {name}｜{s.scientific_name}"
                f"（目：{s.order or '（空白）'}｜現分群：{eco_group_of(s)}）"
            )
        self.stdout.write(
            "\n處理方式二擇一："
            "\n  (1) 於 collection/ecological_groups.py 的屬名表補上對應屬名；"
            "\n  (2) 補填該物種的「目」欄位（雀形目／鷹形目…）。"
        )
