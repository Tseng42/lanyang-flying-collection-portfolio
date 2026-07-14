"""從 backups/ 還原資料庫。

用法：
  python manage.py restore_db --list                 只列出可用備份
  python manage.py restore_db --latest               預覽還原最新一份
  python manage.py restore_db <檔名>                  預覽還原指定備份
  python manage.py restore_db <檔名> --yes            實際執行還原

還原前會自動先備份目前資料庫（label=pre-restore），故還原本身也可回復。
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from collection.backup import backup_sqlite, list_backups, restore_sqlite


class Command(BaseCommand):
    help = "從 backups/ 還原 SQLite 資料庫（還原前會自動先備份現況）。"

    def add_arguments(self, parser):
        parser.add_argument(
            "filename", nargs="?", default=None,
            help="要還原的備份檔名（位於 backups/）或完整路徑；省略時可搭配 --latest。",
        )
        parser.add_argument(
            "--latest", action="store_true", help="還原最新一份備份。",
        )
        parser.add_argument(
            "--list", action="store_true", help="只列出可用備份，不還原。",
        )
        parser.add_argument(
            "--yes", action="store_true",
            help="確認實際執行還原；未加時僅預覽不會變更資料。",
        )

    def handle(self, *args, **opts):
        backups = list_backups()

        # 只列出，或未指定來源時，先把可用備份印出來
        if opts["list"] or (not opts["filename"] and not opts["latest"]):
            if not backups:
                self.stdout.write("backups/ 內目前沒有任何備份。")
                return
            self.stdout.write("可用備份（新 → 舊）：")
            for b in backups:
                self.stdout.write(f"  {b.name}")
            if opts["list"]:
                return
            raise CommandError("請指定要還原的檔名，或加 --latest 還原最新一份。")

        # 決定來源備份
        if opts["latest"]:
            if not backups:
                raise CommandError("沒有可還原的備份。")
            src = backups[0]
        else:
            given = Path(opts["filename"])
            src = given if given.is_absolute() else (
                Path(settings.BASE_DIR) / "backups" / given
            )
            if not src.exists():
                raise CommandError(f"找不到備份檔：{src}")

        # 預設只預覽，避免手滑覆蓋資料
        if not opts["yes"]:
            self.stdout.write(self.style.WARNING(
                "[預覽] 將以下列備份覆蓋目前資料庫：\n"
                f"  {src}\n"
                "還原前會自動先備份目前資料庫（label=pre-restore）。\n"
                "確認無誤後，請重跑並加上 --yes 實際執行。"
            ))
            return

        # 先備份現況，讓還原本身也能回復
        safety = backup_sqlite(label="pre-restore")
        if safety is not None:
            self.stdout.write(f"已先備份目前資料庫：{safety}")

        target = restore_sqlite(src)
        self.stdout.write(self.style.SUCCESS(
            f"已從 {src.name} 還原到 {target}"
        ))
