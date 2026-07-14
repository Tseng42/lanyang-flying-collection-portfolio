"""SQLite 資料庫備份工具。

供 `backup_db` 管理指令與 pre_migrate 訊號共用。
使用 SQLite 官方 backup API，即使資料庫正被 runserver 連線中也能安全複製。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings


def backup_sqlite(label: str = "", keep: int = 20) -> Path | None:
    """複製一份目前的 SQLite 資料庫到 backups/，並清掉超過保留份數的舊檔。

    回傳新備份檔的路徑；若非 SQLite 或資料庫尚未建立則回傳 None。
    """
    db = settings.DATABASES["default"]
    if "sqlite3" not in db["ENGINE"]:
        return None

    src = Path(db["NAME"])
    if not src.exists():
        # 資料庫還沒建立（例如全新專案第一次 migrate 前），沒東西可備份
        return None

    backup_dir = Path(settings.BASE_DIR) / "backups"
    backup_dir.mkdir(exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    dest = backup_dir / f"db-{stamp}{suffix}.sqlite3"

    with sqlite3.connect(str(src)) as source, sqlite3.connect(str(dest)) as target:
        source.backup(target)

    # 僅保留最近 keep 份（依檔名時間戳排序）
    if keep and keep > 0:
        backups = sorted(backup_dir.glob("db-*.sqlite3"))
        for old in backups[:-keep]:
            old.unlink()

    return dest


def list_backups() -> list[Path]:
    """回傳 backups/ 內所有備份檔，新到舊排序。"""
    backup_dir = Path(settings.BASE_DIR) / "backups"
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("db-*.sqlite3"), reverse=True)


def restore_sqlite(src: Path) -> Path:
    """把指定備份檔的內容寫回目前的 SQLite 資料庫（覆蓋現況）。

    透過 SQLite backup API 寫入現有 db 檔，避免直接取代檔案在
    Windows 上因檔案鎖定失敗。回傳被還原的目標 db 路徑。
    """
    db = settings.DATABASES["default"]
    if "sqlite3" not in db["ENGINE"]:
        raise ValueError("此還原功能僅支援 SQLite。")

    target_path = Path(db["NAME"])
    with sqlite3.connect(str(src)) as source, \
            sqlite3.connect(str(target_path)) as target:
        source.backup(target)
    return target_path
