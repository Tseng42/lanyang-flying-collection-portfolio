"""清查查詢頁「簡易版」棲域分群「無從判斷、只能靠預設歸陸域」的鳥類（只清查、不修改資料）。

背景：簡易版以「非鳥類 → 陸域例外屬名 → 水域屬名 → 已填的目 → 預設陸域」推導棲域。
由於陸域鳥種類遠多於水域、無法逐一列舉，最後一步刻意預設為陸域；代價是若某水鳥的
屬名漏收、「目」欄位又空白，就會被靜靜地誤歸陸域。本指令挑出這類真正無從判斷的個案。

**「目」已填但非水域目（例如雀形目）不會列出**——那代表可據以確定為陸域，並非未判定。

用法：
    python manage.py list_unverified_habitat_species          # 只清查公開物種
    python manage.py list_unverified_habitat_species --all     # 連未公開物種一併清查
"""

from collections import Counter

from django.core.management.base import BaseCommand

from collection.habitat_groups import HabitatGroup, habitat_group_of, is_unverified
from collection.models import Species


class Command(BaseCommand):
    help = "列出棲域分群無從判斷（屬名未收錄且「目」空白）而落預設陸域的鳥類物種。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="連未公開（草稿／待審）物種一併清查；預設只看公開物種。",
        )

    def handle(self, *args, **options):
        qs = Species.objects.all() if options["all"] else Species.objects.published()
        qs = qs.order_by("scientific_name")
        species = list(qs)

        # 先報總覽，讓人一眼看出三格的分布是否合理
        labels = dict(HabitatGroup.choices)
        counts = Counter(habitat_group_of(s) for s in species)
        self.stdout.write(f"共 {len(species)} 筆物種：")
        for value, label in HabitatGroup.choices:
            self.stdout.write(f"  {label}：{counts.get(value, 0)} 筆")

        unverified = [s for s in species if is_unverified(s)]
        if not unverified:
            self.stdout.write(
                self.style.SUCCESS("\n每筆鳥類都能由屬名或「目」判定棲域，無待複核項目。")
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"\n其中 {len(unverified)} 筆鳥類的屬名未收錄、「目」也空白，"
                f"目前一律落入預設的「{labels[HabitatGroup.TERRESTRIAL]}」，請人工複核："
            )
        )
        for s in unverified:
            name = s.common_name or "（無中文名）"
            self.stdout.write(f"  - {name}｜{s.scientific_name}")
        self.stdout.write(
            "\n處理方式三擇一："
            "\n  (1) 確認為水鳥 → 於 collection/habitat_groups.py 的 AQUATIC_GENERA 補上屬名；"
            "\n  (2) 補填該物種的「目」欄位（雀形目／鴴形目…），即可自動判定；"
            "\n  (3) 若其實不是鳥類 → 修正該物種的「分類群」欄位，會自動歸入「其他」。"
        )
