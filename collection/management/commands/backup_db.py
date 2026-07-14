"""手動備份資料庫：python manage.py backup_db [--label 說明] [--keep N]"""

from django.core.management.base import BaseCommand, CommandError

from collection.backup import backup_sqlite


class Command(BaseCommand):
    help = "備份 SQLite 資料庫到 backups/ 目錄（保留最近 N 份）。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep", type=int, default=20,
            help="保留最近幾份備份，其餘自動刪除（預設 20）。",
        )
        parser.add_argument(
            "--label", default="",
            help="備份檔名附加的標籤，方便辨識（例如 before-import）。",
        )

    def handle(self, *args, **options):
        dest = backup_sqlite(label=options["label"], keep=options["keep"])
        if dest is None:
            raise CommandError(
                "沒有可備份的資料庫（非 SQLite 或資料庫檔尚未建立）。"
            )
        self.stdout.write(self.style.SUCCESS(f"已備份：{dest}"))
