"""把不符合新格式的舊典藏編號修正為 LYM-類群-年份-流水號。

用法：
  python manage.py fix_catalog_numbers            # 預覽（不修改）
  python manage.py fix_catalog_numbers --apply    # 實際修正（會先自動備份）

典藏編號是主鍵，修正時會一併更新關聯子表（異動／影像／鑑定）的外鍵，
確保關聯不斷裂。
"""

import re

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from collection.backup import backup_sqlite
from collection.models import (
    CATALOG_NUMBER_RE, Identification, Movement, Specimen, SpecimenImage,
)


def _compute_new_number(specimen):
    """依標本類群與舊編號中的年份/流水號，算出新格式編號。"""
    code = Specimen.GROUP_CODE[specimen.taxon_group]
    match = re.search(r"(\d{4})-(\d+)$", specimen.catalog_number)
    if match:
        year, serial = match.group(1), int(match.group(2))
        candidate = f"{Specimen.CATALOG_PREFIX}-{code}-{year}-{serial:04d}"
        year_int = int(year)
    else:
        candidate, year_int = None, None

    # 無法解析、或新編號已被占用 → 改用該類群該年度的下一個可用號。
    # next_catalog_number 已不再取系統時鐘，年份需明確提供：優先用舊編號解析出的
    # 年份，解析不到則退回該標本的入藏年份 accession_year。
    if not candidate or (
        candidate != specimen.catalog_number
        and Specimen.objects.filter(pk=candidate).exists()
    ):
        candidate = Specimen.next_catalog_number(
            specimen.taxon_group, year=year_int or specimen.accession_year
        )
    return candidate


def _rename_pk(old, new):
    """安全地更新標本主鍵及其子表外鍵（SQLite 暫時關閉外鍵檢查）。"""
    child_tables = [
        Movement._meta.db_table,
        SpecimenImage._meta.db_table,
        Identification._meta.db_table,
    ]
    specimen_table = Specimen._meta.db_table
    is_sqlite = connection.vendor == "sqlite"

    if is_sqlite:
        connection.cursor().execute("PRAGMA foreign_keys=OFF")
    try:
        with transaction.atomic():
            with connection.cursor() as cur:
                for table in child_tables:
                    cur.execute(
                        f"UPDATE {table} SET specimen_id=%s WHERE specimen_id=%s",
                        [new, old],
                    )
                cur.execute(
                    f"UPDATE {specimen_table} SET catalog_number=%s "
                    f"WHERE catalog_number=%s",
                    [new, old],
                )
    finally:
        if is_sqlite:
            connection.cursor().execute("PRAGMA foreign_keys=ON")


class Command(BaseCommand):
    help = "把不符合新格式的舊典藏編號修正為 LYM-類群-年份-流水號。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="實際執行修正（未加則僅預覽）。",
        )

    def handle(self, *args, **options):
        targets = [
            s for s in Specimen.objects.select_related("species").all()
            if not CATALOG_NUMBER_RE.match(s.catalog_number)
        ]

        if not targets:
            self.stdout.write(self.style.SUCCESS(
                "所有典藏編號皆已符合新格式，無需修正。"))
            return

        plan = [(s.catalog_number, _compute_new_number(s)) for s in targets]
        self.stdout.write(f"發現 {len(plan)} 筆需修正：")
        for old, new in plan:
            self.stdout.write(f"  {old}  →  {new}")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING(
                "以上為預覽。確認無誤請重跑並加上 --apply 實際修正。"))
            return

        backup = backup_sqlite(label="before-catalog-fix")
        if backup:
            self.stdout.write(f"已先備份資料庫：{backup}")

        for old, new in plan:
            _rename_pk(old, new)
            self.stdout.write(self.style.SUCCESS(f"已修正：{old} → {new}"))

        self.stdout.write(self.style.SUCCESS("完成。"))
