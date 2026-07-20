"""清查「資料庫有紀錄但實體檔案已不存在」的失效媒體檔。

背景：專案早期用本機 /media/ 儲存，Render 重新部署後檔案會消失，但資料庫
仍保有紀錄。本指令走訪所有含影像欄位的 model，逐筆檢查檔案是否真的存在，
只輸出報表，**絕不刪除或修改任何資料**。

用法：
    python manage.py check_missing_media            # 預設 dry-run，只清查
    python manage.py check_missing_media --http     # 改用 HTTP HEAD 檢查實際 URL
"""

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "清查資料庫有紀錄但實體檔案已不存在的失效媒體檔（只清查、不刪除）。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="只清查、不做任何變更（預設開啟，且本指令無論如何都不會刪除資料）。",
        )
        parser.add_argument(
            "--http",
            action="store_true",
            default=False,
            help="改用實際發 HTTP HEAD 請求檢查檔案 URL，而非 storage.exists()。",
        )

    def _targets(self):
        """回傳要清查的 (model, 欄位名, 取得館藏編號的函式) 清單。

        以延遲 import 避免載入設定期間的循環相依；若日後新增
        SpeciesImage / ObservationImage，只要在此加一列即可。
        """
        from collection.models import Specimen, SpecimenImage

        return [
            (Specimen, "image", lambda obj: obj.catalog_number),
            (
                SpecimenImage,
                "image",
                lambda obj: obj.specimen.catalog_number,
            ),
        ]

    def _file_exists(self, fieldfile, use_http):
        """檢查單一檔案是否存在。use_http=True 時發 HTTP HEAD，否則用 storage.exists()。"""
        if not use_http:
            return fieldfile.storage.exists(fieldfile.name)

        # HTTP HEAD：對實際對外 URL 發請求，2xx/3xx 視為存在
        try:
            url = fieldfile.url
        except Exception:  # noqa: BLE001 — 取不到 URL 即視為不存在
            return False
        req = Request(url, method="HEAD")
        try:
            with urlopen(req, timeout=10) as resp:  # noqa: S310 — 檢查自家媒體 URL
                return 200 <= resp.status < 400
        except HTTPError as exc:
            return 200 <= exc.code < 400
        except (URLError, ValueError):
            return False

    def handle(self, *args, **options):
        use_http = options["http"]
        method_desc = "HTTP HEAD 請求" if use_http else "storage.exists()"

        self.stdout.write("=" * 60)
        self.stdout.write("失效媒體檔清查報表（只清查、不刪除任何資料）")
        self.stdout.write(f"檢查方式：{method_desc}")
        self.stdout.write("=" * 60)

        grand_total = grand_missing = 0

        for model, field_name, catalog_of in self._targets():
            label = model._meta.verbose_name
            qs = model.objects.all()
            # 帶 FK 的 model 先 select_related，避免逐筆查館藏編號時 N+1
            if any(f.name == "specimen" for f in model._meta.get_fields()):
                qs = qs.select_related("specimen")

            total = exists = missing = 0
            missing_rows = []

            for obj in qs:
                fieldfile = getattr(obj, field_name)
                # 欄位為空（從未上傳影像）不算失效，直接略過不計入
                if not fieldfile:
                    continue
                total += 1
                if self._file_exists(fieldfile, use_http):
                    exists += 1
                else:
                    missing += 1
                    try:
                        catalog = catalog_of(obj) or "（無館藏編號）"
                    except Exception:  # noqa: BLE001 — 取不到編號不影響清查
                        catalog = "（無法取得館藏編號）"
                    missing_rows.append((obj.pk, catalog, fieldfile.name))

            grand_total += total
            grand_missing += missing

            self.stdout.write("")
            self.stdout.write(f"■ {label}（{model.__name__}.{field_name}）")
            self.stdout.write(f"    有影像紀錄總筆數：{total}")
            self.stdout.write(self.style.SUCCESS(f"    檔案存在：{exists}"))
            style = self.style.ERROR if missing else self.style.SUCCESS
            self.stdout.write(style(f"    檔案不存在（失效）：{missing}"))

            if missing_rows:
                self.stdout.write("    失效紀錄清單：")
                self.stdout.write("      pk    | 館藏編號              | 檔案路徑")
                self.stdout.write("      ------+-----------------------+" + "-" * 30)
                for pk, catalog, path in missing_rows:
                    self.stdout.write(
                        f"      {pk:<5} | {catalog:<21} | {path}"
                    )

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(
            f"合計：影像紀錄 {grand_total} 筆，其中失效 {grand_missing} 筆。"
        )
        self.stdout.write(
            self.style.WARNING("本指令僅清查、未刪除任何資料（dry-run）。")
        )
        self.stdout.write("=" * 60)
