"""標本批次匯入（django-import-export）測試。

涵蓋：dry-run 不落地但算出正確彙總、實際匯入建立三張表、中文名去重、
自動建立佔位物種與鑑定狀態、既有物種不被覆寫、日期／保育等級／生命階段
對照、以及任一列出錯時整批 rollback。
"""

import datetime
import io

import tablib
from django.contrib.admin.sites import site
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import CollectionEvent, PublicationStatus, Species, Specimen
from .resources import PLACEHOLDER_PREFIX, TEMPLATE_HEADERS, SpecimenImportResource


def make_dataset(rows):
    """以範本標題列組出 tablib.Dataset；rows 為每列的值序列。"""
    data = tablib.Dataset(headers=TEMPLATE_HEADERS)
    for row in rows:
        data.append(list(row))
    return data


# 標題順序：中文名, 學名, 生命階段, 保育等級, 採集日期, 採集地點, 採集者, 備註, 照片檔名
ROW_EGRET = [
    "小白鷺", "Egretta garzetta", "成鳥", "一般類",
    "2026-05-01", "宜蘭縣蘇澳鎮", "王小明", "翼受傷", "IMG_0001.jpg",
]
ROW_UNKNOWN = [
    "不知名小鷸", "", "幼鳥", "",
    "2026-05-03", "宜蘭縣壯圍鄉", "陳小華", "待鑑定", "IMG_0002.jpg",
]


class DryRunTests(TestCase):
    def test_dry_run_reports_counts_without_persisting(self):
        resource = SpecimenImportResource()
        result = resource.import_data(
            make_dataset([ROW_EGRET, ROW_UNKNOWN]), dry_run=True,
        )
        self.assertFalse(result.has_errors())
        self.assertFalse(result.has_validation_errors())
        # 彙總數字正確
        self.assertEqual(result.lanyang_new_specimens, 2)
        self.assertEqual(result.lanyang_new_species, 2)
        self.assertEqual(result.lanyang_new_events, 2)
        # dry-run 不落地
        self.assertEqual(Specimen.objects.count(), 0)
        self.assertEqual(Species.objects.count(), 0)
        self.assertEqual(CollectionEvent.objects.count(), 0)


class ImportTests(TestCase):
    def test_import_creates_three_tables(self):
        resource = SpecimenImportResource()
        result = resource.import_data(make_dataset([ROW_EGRET]), dry_run=False)
        self.assertFalse(result.has_errors())

        self.assertEqual(Specimen.objects.count(), 1)
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(CollectionEvent.objects.count(), 1)

        specimen = Specimen.objects.get()
        # 典藏編號自動產生為 LYM-AV-2026-0001
        self.assertEqual(specimen.catalog_number, "LYM-AV-2026-0001")
        self.assertEqual(specimen.taxon_group, Specimen.TaxonGroup.BIRD)
        self.assertEqual(specimen.accession_year, datetime.date.today().year)
        self.assertEqual(
            specimen.preparation_status, Specimen.PreparationStatus.FROZEN_PENDING,
        )
        self.assertEqual(
            specimen.preservation_method, Specimen.PreservationMethod.FROZEN,
        )
        self.assertEqual(specimen.specimen_type, "")
        self.assertEqual(specimen.publication_status, PublicationStatus.DRAFT)
        self.assertIn("翼受傷", specimen.remarks)
        self.assertIn("IMG_0001.jpg", specimen.remarks)

        event = specimen.collection_event
        self.assertEqual(event.collection_location, "宜蘭縣蘇澳鎮")
        self.assertEqual(event.collection_date, datetime.date(2026, 5, 1))
        self.assertEqual(event.collector, "王小明")

    def test_auto_created_placeholder_species(self):
        resource = SpecimenImportResource()
        resource.import_data(make_dataset([ROW_UNKNOWN]), dry_run=False)

        species = Species.objects.get()
        self.assertTrue(species.is_auto_created)
        self.assertEqual(species.common_name, "不知名小鷸")
        self.assertEqual(
            species.scientific_name, f"{PLACEHOLDER_PREFIX}不知名小鷸",
        )
        self.assertEqual(species.taxon_group, Species.TaxonGroup.BIRD)
        self.assertEqual(species.publication_status, PublicationStatus.DRAFT)

        specimen = Specimen.objects.get()
        # 佔位物種 → 標本維持未鑑定
        self.assertEqual(
            specimen.identification_status,
            Specimen.IdentificationStatus.UNIDENTIFIED,
        )
        self.assertEqual(specimen.life_stage, Specimen.LifeStage.JUVENILE)

    def test_common_name_dedup_within_import(self):
        """同一次匯入中，多列相同中文名只建一筆物種、各自一件標本。"""
        rows = [
            ["八哥", "Acridotheres cristatellus", "成鳥", "一般類",
             "2026-06-01", "宜蘭市", "甲", "", ""],
            ["八哥", "", "成鳥", "一般類",
             "2026-06-02", "羅東鎮", "乙", "", ""],
        ]
        resource = SpecimenImportResource()
        resource.import_data(make_dataset(rows), dry_run=False)

        self.assertEqual(Species.objects.filter(common_name="八哥").count(), 1)
        self.assertEqual(Specimen.objects.count(), 2)
        # 第一列提供了真實學名 → 物種以真實學名建立（非佔位）
        species = Species.objects.get(common_name="八哥")
        self.assertEqual(species.scientific_name, "Acridotheres cristatellus")

    def test_existing_species_not_overwritten_and_to_species(self):
        """既有且有真實學名的物種：沿用、不覆寫，且標本鑑定狀態設為已鑑定至種。"""
        existing = Species.objects.create(
            common_name="麻雀", scientific_name="Passer montanus",
            taxon_group=Species.TaxonGroup.BIRD,
            conservation_status=Species.ConservationStatus.GENERAL,
            publication_status=PublicationStatus.PUBLISHED,
        )
        rows = [[
            "麻雀", "亂填的學名", "成鳥", "瀕臨絕種",
            "2026-06-05", "宜蘭市", "丙", "", "",
        ]]
        resource = SpecimenImportResource()
        resource.import_data(make_dataset(rows), dry_run=False)

        existing.refresh_from_db()
        # 學名與保育等級皆未被覆寫
        self.assertEqual(existing.scientific_name, "Passer montanus")
        self.assertEqual(
            existing.conservation_status, Species.ConservationStatus.GENERAL,
        )
        self.assertEqual(Species.objects.count(), 1)

        specimen = Specimen.objects.get()
        self.assertEqual(specimen.species_id, existing.pk)
        self.assertEqual(
            specimen.identification_status,
            Specimen.IdentificationStatus.TO_SPECIES,
        )

    def test_conservation_mapping_on_new_species(self):
        rows = [[
            "黑面琵鷺", "Platalea minor", "成鳥", "瀕臨絕種",
            "", "", "", "", "",
        ]]
        resource = SpecimenImportResource()
        resource.import_data(make_dataset(rows), dry_run=False)
        species = Species.objects.get()
        self.assertEqual(
            species.conservation_status, Species.ConservationStatus.ENDANGERED,
        )
        # 三項採集資訊皆空 → 不建採集事件
        self.assertEqual(CollectionEvent.objects.count(), 0)
        self.assertIsNone(Specimen.objects.get().collection_event)

    def test_blank_conservation_defaults_unverified(self):
        rows = [["某鳥", "", "", "", "", "", "", "", ""]]
        SpecimenImportResource().import_data(make_dataset(rows), dry_run=False)
        self.assertEqual(
            Species.objects.get().conservation_status,
            Species.ConservationStatus.UNVERIFIED,
        )


class ValidationRollbackTests(TestCase):
    def test_bad_date_reports_field_error(self):
        rows = [[
            "小白鷺", "Egretta garzetta", "成鳥", "一般類",
            "2026/13/40", "宜蘭縣", "王", "", "",
        ]]
        result = SpecimenImportResource().import_data(
            make_dataset(rows), dry_run=True,
        )
        self.assertTrue(result.has_validation_errors())
        invalid = result.invalid_rows[0]
        self.assertIn("採集日期", invalid.error.message_dict)

    def test_any_row_error_rolls_back_whole_batch(self):
        """第 2 列日期錯 → 整批 rollback，第 1 列也不落地。"""
        rows = [
            ROW_EGRET,
            ["壞列", "", "成鳥", "一般類", "not-a-date", "宜蘭", "乙", "", ""],
        ]
        # 模擬 admin 的設定（見 SpecimenAdmin.get_import_data_kwargs）
        result = SpecimenImportResource().import_data(
            make_dataset(rows), dry_run=False, rollback_on_validation_errors=True,
        )
        self.assertTrue(result.has_validation_errors())
        # 整批未落地
        self.assertEqual(Specimen.objects.count(), 0)
        self.assertEqual(Species.objects.count(), 0)
        self.assertEqual(CollectionEvent.objects.count(), 0)

    def test_missing_common_name_reports_field_error(self):
        rows = [["", "Egretta garzetta", "成鳥", "一般類", "", "", "", "", ""]]
        result = SpecimenImportResource().import_data(
            make_dataset(rows), dry_run=True,
        )
        self.assertTrue(result.has_validation_errors())
        invalid = result.invalid_rows[0]
        self.assertIn("中文名", invalid.error.message_dict)


class AdminImportViewTests(TestCase):
    """透過 HTTP 驗證後台匯入頁的權限、模板與 dry-run 預覽彙總。"""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            "loader", password="pw", is_staff=True,
        )
        cls.staff.user_permissions.add(
            Permission.objects.get(codename="add_specimen"),
            Permission.objects.get(codename="change_specimen"),
            Permission.objects.get(codename="view_specimen"),
            Permission.objects.get(codename="add_species"),
        )

    @staticmethod
    def _csv_format_index():
        admin_obj = site._registry[Specimen]
        for idx, fmt in enumerate(admin_obj.get_import_formats()):
            if fmt().get_title() == "csv":
                return idx
        raise AssertionError("CSV format not available")

    def test_changelist_shows_import_button(self):
        self.client.force_login(self.staff)
        url = reverse("admin:collection_specimen_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin:collection_specimen_import"))

    def test_no_import_permission_hides_feature(self):
        viewer = User.objects.create_user("viewer", password="pw", is_staff=True)
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_specimen"),
        )
        self.client.force_login(viewer)
        response = self.client.get(
            reverse("admin:collection_specimen_import")
        )
        self.assertEqual(response.status_code, 403)

    def test_dry_run_preview_shows_summary(self):
        self.client.force_login(self.staff)
        csv_body = (
            ",".join(TEMPLATE_HEADERS) + "\n"
            + ",".join(ROW_EGRET) + "\n"
            + ",".join(ROW_UNKNOWN) + "\n"
        ).encode("utf-8")
        upload = SimpleUploadedFile(
            "import.csv", csv_body, content_type="text/csv",
        )
        response = self.client.post(
            reverse("admin:collection_specimen_import"),
            {"format": self._csv_format_index(), "import_file": upload},
        )
        self.assertEqual(response.status_code, 200)
        # 彙總橫幅出現，且尚未落地
        self.assertContains(response, "本次將新增")
        self.assertContains(response, "件標本")
        self.assertEqual(Specimen.objects.count(), 0)
