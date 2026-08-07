"""公開狀態控制（publication_status）相關測試。

涵蓋：對外頁面只撈公開資料、未公開物種 404、後台依權限限制可選狀態與批次動作、
以及 Darwin Core 匯出「含未公開資料」的權限控管。
"""

import datetime
import io
import struct
import zlib

from django.contrib.admin.sites import site
from django.contrib.auth.models import Group, Permission, User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .admin import SpecimenAdmin, SpecimenAdminForm
from .models import (
    CatalogNumberChange, CollectionEvent, Identification, Movement,
    Observation, PublicationStatus, Species, Specimen, SpecimenImage,
)
from .validators import (
    INVALID_IMAGE_MESSAGE, MAX_MEGAPIXELS, MAX_UPLOAD_BYTES,
    validate_specimen_image,
)


class PublicationFilterTests(TestCase):
    """對外頁面只顯示 publication_status='published' 的資料。"""

    @classmethod
    def setUpTestData(cls):
        cls.pub = Species.objects.create(
            common_name="小白鷺", scientific_name="Egretta garzetta",
            taxon_group=Species.TaxonGroup.BIRD,
            conservation_status=Species.ConservationStatus.GENERAL,
            publication_status=PublicationStatus.PUBLISHED,
        )
        cls.draft = Species.objects.create(
            common_name="秘密鳥", scientific_name="Secretus avis",
            taxon_group=Species.TaxonGroup.BIRD,
            conservation_status=Species.ConservationStatus.GENERAL,
            publication_status=PublicationStatus.DRAFT,
        )

    def test_list_shows_only_published(self):
        resp = self.client.get(reverse("public_species_list"))
        self.assertContains(resp, "Egretta garzetta")
        self.assertNotContains(resp, "Secretus avis")

    def test_research_view_also_filters_published(self):
        resp = self.client.get(reverse("public_species_list"), {"view": "research"})
        self.assertNotContains(resp, "Secretus avis")

    def test_detail_published_ok(self):
        resp = self.client.get(
            reverse("public_species_detail", args=[self.pub.pk])
        )
        self.assertEqual(resp.status_code, 200)

    def test_detail_draft_returns_404(self):
        resp = self.client.get(
            reverse("public_species_detail", args=[self.draft.pk])
        )
        self.assertEqual(resp.status_code, 404)

    def test_detail_only_published_specimens(self):
        pub_sp = Specimen.objects.create(
            taxon_group=Specimen.TaxonGroup.BIRD, species=self.pub,
            specimen_type=Specimen.SpecimenType.TAXIDERMY,
            publication_status=PublicationStatus.PUBLISHED,
        )
        Specimen.objects.create(
            taxon_group=Specimen.TaxonGroup.BIRD, species=self.pub,
            specimen_type=Specimen.SpecimenType.TAXIDERMY,
            publication_status=PublicationStatus.DRAFT,
        )
        resp = self.client.get(
            reverse("public_species_detail", args=[self.pub.pk])
        )
        # 只計入公開標本
        self.assertEqual(resp.context["specimen_count"], 1)
        self.assertContains(resp, pub_sp.catalog_number)

    def test_public_stats_counts_only_published(self):
        resp = self.client.get(reverse("public_stats"))
        self.assertEqual(resp.context["species_total"], 1)


class PublishPermissionAdminTests(TestCase):
    """後台：依 can_publish_* 權限限制可選狀態與批次動作。"""

    @classmethod
    def setUpTestData(cls):
        # 具全部權限的員工（可變更 + 可公開標本）
        cls.publisher = User.objects.create_user(
            "publisher", password="x", is_staff=True,
        )
        change = Permission.objects.get(
            content_type__app_label="collection", codename="change_specimen",
        )
        view = Permission.objects.get(
            content_type__app_label="collection", codename="view_specimen",
        )
        publish = Permission.objects.get(
            content_type__app_label="collection", codename="can_publish_specimen",
        )
        cls.publisher.user_permissions.add(change, view, publish)

        # 只能變更、不能公開的員工
        cls.editor = User.objects.create_user(
            "editor", password="x", is_staff=True,
        )
        cls.editor.user_permissions.add(change, view)

    def _admin(self):
        return SpecimenAdmin(Specimen, site)

    def _request(self, user):
        req = RequestFactory().get("/admin/collection/specimen/")
        req.user = user
        return req

    def test_editor_cannot_choose_published(self):
        admin = self._admin()
        field = Specimen._meta.get_field("publication_status")
        formfield = admin.formfield_for_choice_field(field, self._request(self.editor))
        values = [v for v, _ in formfield.choices]
        self.assertNotIn(PublicationStatus.PUBLISHED, values)
        self.assertIn(PublicationStatus.DRAFT, values)

    def test_publisher_can_choose_published(self):
        admin = self._admin()
        field = Specimen._meta.get_field("publication_status")
        formfield = admin.formfield_for_choice_field(field, self._request(self.publisher))
        values = [v for v, _ in formfield.choices]
        self.assertIn(PublicationStatus.PUBLISHED, values)

    def test_published_record_readonly_for_editor(self):
        admin = self._admin()
        obj = Specimen.objects.create(
            taxon_group=Specimen.TaxonGroup.BIRD,
            specimen_type=Specimen.SpecimenType.TAXIDERMY,
            publication_status=PublicationStatus.PUBLISHED,
        )
        ro = admin.get_readonly_fields(self._request(self.editor), obj)
        self.assertIn("publication_status", ro)
        # 有權限者則可改動
        ro2 = admin.get_readonly_fields(self._request(self.publisher), obj)
        self.assertNotIn("publication_status", ro2)

    def test_publish_action_hidden_without_permission(self):
        admin = self._admin()
        editor_actions = admin.get_actions(self._request(self.editor))
        self.assertNotIn("make_published", editor_actions)
        self.assertNotIn("export_darwin_core_csv_with_unpublished", editor_actions)
        # 有權限者看得到
        publisher_actions = admin.get_actions(self._request(self.publisher))
        self.assertIn("make_published", publisher_actions)
        self.assertIn("export_darwin_core_csv_with_unpublished", publisher_actions)
        # 不需公開權限的動作，兩者都看得到
        self.assertIn("make_draft", editor_actions)


class ReadonlyResearcherAdminTests(TestCase):
    """以實際的「唯讀研究員」群組帳號驗證後端把關（含真正的 HTTP POST）。

    唯讀研究員群組由 post_migrate 的 sync_groups 建立，僅具 view_* 權限、
    無 change 亦無 can_publish_*。
    """

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.get(name="唯讀研究員")
        cls.user = User.objects.create_user(
            "researcher", password="pw", is_staff=True,
        )
        cls.user.groups.add(cls.group)
        cls.specimen = Specimen.objects.create(
            taxon_group=Specimen.TaxonGroup.BIRD,
            specimen_type=Specimen.SpecimenType.TAXIDERMY,
            publication_status=PublicationStatus.DRAFT,
        )

    def _admin(self):
        return SpecimenAdmin(Specimen, site)

    def _request(self):
        # 重新讀取 user 以取得群組權限（避免 has_perm 快取）
        req = RequestFactory().get("/admin/collection/specimen/")
        req.user = User.objects.get(pk=self.user.pk)
        return req

    def test_group_has_no_publish_permission(self):
        u = User.objects.get(pk=self.user.pk)
        self.assertFalse(u.has_perm("collection.can_publish_specimen"))
        self.assertTrue(u.has_perm("collection.view_specimen"))

    def test_status_dropdown_excludes_published(self):
        formfield = self._admin().formfield_for_choice_field(
            Specimen._meta.get_field("publication_status"), self._request()
        )
        values = [v for v, _ in formfield.choices]
        self.assertNotIn(PublicationStatus.PUBLISHED, values)

    def test_actions_exclude_publish_variants(self):
        actions = self._admin().get_actions(self._request())
        self.assertNotIn("make_published", actions)
        self.assertNotIn("export_darwin_core_csv_with_unpublished", actions)

    def test_post_changeform_with_published_is_blocked(self):
        """直接 POST 含 published 的變更請求 → 後端拒絕，資料不變。"""
        self.client.force_login(self.user)
        url = reverse(
            "admin:collection_specimen_change", args=[self.specimen.pk]
        )
        resp = self.client.post(url, {
            "publication_status": PublicationStatus.PUBLISHED,
            "taxon_group": Specimen.TaxonGroup.BIRD,
            "specimen_type": Specimen.SpecimenType.TAXIDERMY,
        })
        # 無 change 權限 → 403（後端擋下，非僅前端隱藏）
        self.assertEqual(resp.status_code, 403)
        self.specimen.refresh_from_db()
        self.assertEqual(
            self.specimen.publication_status, PublicationStatus.DRAFT
        )

    def test_post_make_published_action_is_ignored(self):
        """POST 觸發 make_published 批次動作 → 動作不在可用清單，資料不變。"""
        self.client.force_login(self.user)
        url = reverse("admin:collection_specimen_changelist")
        resp = self.client.post(url, {
            "action": "make_published",
            "_selected_action": [self.specimen.pk],
        }, follow=True)
        self.specimen.refresh_from_db()
        self.assertEqual(
            self.specimen.publication_status, PublicationStatus.DRAFT
        )


class FullExportViewTests(TestCase):
    """後台全欄位匯出：權限控管與檔案產出。"""

    XLSX_CT = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    @classmethod
    def setUpTestData(cls):
        # 有匯出權限者
        cls.exporter = User.objects.create_user(
            "exporter", password="x", is_staff=True,
        )
        cls.exporter.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="collection",
                codename="can_export_full_data",
            )
        )
        # 無匯出權限者（唯讀研究員群組）
        cls.researcher = User.objects.create_user(
            "researcher2", password="x", is_staff=True,
        )
        cls.researcher.groups.add(Group.objects.get(name="唯讀研究員"))
        # 一些跨公開狀態的資料，確認匯出不做過濾
        sp = Species.objects.create(
            common_name="測試鳥", scientific_name="Testus avis",
            taxon_group=Species.TaxonGroup.BIRD,
            conservation_status=Species.ConservationStatus.GENERAL,
            publication_status=PublicationStatus.DRAFT,
        )
        Specimen.objects.create(
            taxon_group=Specimen.TaxonGroup.BIRD, species=sp,
            specimen_type=Specimen.SpecimenType.TAXIDERMY,
            publication_status=PublicationStatus.PUBLISHED,
        )

    def test_export_permission_independent_of_publish(self):
        """can_export_full_data 與 can_publish_* 互相獨立。"""
        u = User.objects.get(pk=self.exporter.pk)
        self.assertTrue(u.has_perm("collection.can_export_full_data"))
        self.assertFalse(u.has_perm("collection.can_publish_specimen"))

    def test_page_forbidden_without_permission(self):
        self.client.force_login(self.researcher)
        self.assertEqual(
            self.client.get(reverse("full_export")).status_code, 403
        )

    def test_download_forbidden_without_permission(self):
        self.client.force_login(self.researcher)
        self.assertEqual(
            self.client.get(reverse("full_export_download")).status_code, 403
        )

    def test_page_ok_with_permission(self):
        self.client.force_login(self.exporter)
        self.assertEqual(self.client.get(reverse("full_export")).status_code, 200)

    def test_download_returns_xlsx_with_permission(self):
        self.client.force_login(self.exporter)
        resp = self.client.get(reverse("full_export_download"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], self.XLSX_CT)
        self.assertIn("filename*=UTF-8''", resp["Content-Disposition"])
        # xlsx 是 zip 容器，開頭為 PK
        self.assertEqual(resp.content[:2], b"PK")

    def test_export_includes_all_publication_statuses(self):
        """匯出全部資料，草稿與公開都要計入（不依 publication_status 過濾）。"""
        import io

        import openpyxl

        from .full_export import build_full_export
        counts = {}
        wb = openpyxl.load_workbook(io.BytesIO(build_full_export(counts)))
        # 1 個 draft 物種 + 1 個 published 標本 皆須計入
        self.assertEqual(counts["物種"], 1)
        self.assertEqual(counts["標本"], 1)
        self.assertIn("公開狀態", [c.value for c in wb["標本"][1]])


class LabelCsvExportTests(TestCase):
    """標籤列印用 CSV 匯出。"""

    def _admin(self):
        return SpecimenAdmin(Specimen, site)

    def _make_specimen(self, **overrides):
        species = Species.objects.create(
            common_name=overrides.pop("common_name", "東方環頸鴴"),
            scientific_name=overrides.pop("scientific_name", "Charadrius alexandrinus"),
            taxon_group=Species.TaxonGroup.BIRD,
            conservation_status=Species.ConservationStatus.GENERAL,
        )
        event = CollectionEvent.objects.create(
            collection_date=overrides.pop("collection_date", datetime.date(2026, 3, 15)),
            collection_location=overrides.pop("location", "宜蘭縣五結鄉蘭陽溪口北岸"),
            collector=overrides.pop("collector", "王小明"),
        )
        return Specimen.objects.create(
            catalog_number=overrides.pop("catalog_number", "LYM-AV-2026-0001"),
            taxon_group=Specimen.TaxonGroup.BIRD,
            species=species,
            specimen_type=Specimen.SpecimenType.TAXIDERMY,
            collection_event=event,
            acquisition_date=overrides.pop("acquisition_date", datetime.date(2026, 4, 1)),
            publication_status=overrides.pop(
                "publication_status", PublicationStatus.DRAFT
            ),
        )

    def test_qr_content_matches_spec_example(self):
        s = self._make_specimen()
        row = self._admin()._label_row(s)
        expected_qr = (
            "LYM-AV-2026-0001｜東方環頸鴴｜Charadrius alexandrinus｜"
            "採集 2026-03-15｜典藏 2026-04-01｜宜蘭縣五結鄉蘭陽溪口北岸｜王小明"
        )
        # 格式由下方 assertEqual 驗證；原本此處有 print() 供人工目視，
        # 但在 Windows cp950 主控台會對中文拋 UnicodeEncodeError，故移除。
        self.assertEqual(row[7], expected_qr)
        # 前七欄為原始文字（日期無「採集／典藏」前綴）
        self.assertEqual(row[:7], [
            "LYM-AV-2026-0001", "東方環頸鴴", "Charadrius alexandrinus",
            "2026-03-15", "2026-04-01", "宜蘭縣五結鄉蘭陽溪口北岸", "王小明",
        ])

    def test_empty_items_are_skipped_no_double_separator(self):
        s = self._make_specimen(collector="", location="", common_name="")
        qr = self._admin()._label_row(s)[7]
        self.assertNotIn("｜｜", qr)          # 無連續分隔符
        self.assertFalse(qr.endswith("｜"))   # 無結尾分隔符
        self.assertNotIn("東方環頸鴴", qr)
        self.assertIn("Charadrius alexandrinus", qr)

    def test_qr_has_no_newline(self):
        s = self._make_specimen(location="宜蘭縣\n五結鄉")
        qr = self._admin()._label_row(s)[7]
        self.assertNotIn("\n", qr)
        self.assertNotIn("\r", qr)
        self.assertIn("宜蘭縣 五結鄉", qr)

    def test_export_ignores_publication_status(self):
        """草稿也要能匯出（不依 publication_status 過濾）。"""
        self._make_specimen(publication_status=PublicationStatus.DRAFT)
        user = User.objects.create_user("labeler", password="x", is_staff=True)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="collection", codename="change_specimen"
            ),
            Permission.objects.get(
                content_type__app_label="collection", codename="view_specimen"
            ),
        )
        self.client.force_login(user)
        resp = self.client.post(
            reverse("admin:collection_specimen_changelist"),
            {"action": "export_label_csv_utf8",
             "_selected_action": ["LYM-AV-2026-0001"]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp["Content-Type"].startswith("text/csv"))
        self.assertEqual(resp.content[:3], b"\xef\xbb\xbf")   # UTF-8 BOM
        self.assertIn("東方環頸鴴", resp.content.decode("utf-8-sig"))

    def test_big5_replaces_unencodable_without_error(self):
        # 🦋 無法以 Big5 編碼 → 以 ? 取代，不得拋例外
        s = self._make_specimen(collector="王小明🦋", catalog_number="LYM-AV-2026-0002")
        resp = self._admin()._label_csv_response(
            self._request_with_messages(), Specimen.objects.filter(pk=s.pk), big5=True
        )
        self.assertEqual(resp["Content-Type"], "text/csv; charset=big5")
        # 內容可用 big5 解碼（未中斷），且含取代字元 ?
        decoded = resp.content.decode("big5")
        self.assertIn("王小明", decoded)
        self.assertIn("?", decoded)

    def _request_with_messages(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        req = RequestFactory().post("/admin/collection/specimen/")
        req.session = SessionStore()
        req._messages = FallbackStorage(req)
        return req

    def test_actions_require_change_permission(self):
        view_only = User.objects.create_user("viewer", password="x", is_staff=True)
        view_only.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="collection", codename="view_specimen"
            )
        )
        changer = User.objects.create_user("changer", password="x", is_staff=True)
        changer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="collection", codename="change_specimen"
            )
        )
        admin = self._admin()

        def actions_for(user):
            req = RequestFactory().get("/admin/collection/specimen/")
            req.user = User.objects.get(pk=user.pk)
            return admin.get_actions(req)

        self.assertNotIn("export_label_csv_utf8", actions_for(view_only))
        self.assertNotIn("export_label_csv_big5", actions_for(view_only))
        self.assertIn("export_label_csv_utf8", actions_for(changer))
        self.assertIn("export_label_csv_big5", actions_for(changer))


def _png_bytes(width, height):
    """組出「只有檔頭有效」的極小 PNG：宣告尺寸為 width×height，但不含完整像素，
    僅供 PIL.Image.open().size 讀取檔頭尺寸使用（不會佔用大量記憶體）。"""
    def chunk(typ, data):
        return (
            struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


class ImageValidatorTests(TestCase):
    """標本影像上傳驗證：大小／像素／無法開啟三種情況與指標歸零。"""

    def test_oversized_file_rejected_with_message(self):
        content = b"\x00" * (6 * 1024 * 1024)   # 6 MB > 5 MB 上限
        f = SimpleUploadedFile("big.jpg", content, content_type="image/jpeg")
        with self.assertRaises(ValidationError) as cm:
            validate_specimen_image(f)
        msg = str(cm.exception)
        self.assertIn("影像檔案過大", msg)
        self.assertIn("目前 6.0 MB", msg)      # 動態帶入實際大小
        self.assertIn("上限 5 MB", msg)
        self.assertIn("標本影像作業規範", msg)   # 告訴使用者怎麼處理

    def test_high_megapixels_rejected_with_message(self):
        f = SimpleUploadedFile(
            "huge.png", _png_bytes(6000, 5000), content_type="image/png",
        )  # 30 MP > 25 MP，但檔案僅數十位元組
        with self.assertRaises(ValidationError) as cm:
            validate_specimen_image(f)
        msg = str(cm.exception)
        self.assertIn("影像像素過高", msg)
        self.assertIn("目前 30.0 百萬畫素", msg)
        self.assertIn("上限 25 百萬畫素", msg)
        self.assertIn("2000 px", msg)

    def test_unreadable_format_rejected_with_message(self):
        f = SimpleUploadedFile(
            "photo.heic", b"not a real image", content_type="image/heic",
        )
        with self.assertRaises(ValidationError) as cm:
            validate_specimen_image(f)
        msg = str(cm.exception)
        # 與 admin invalid_image 訊息完全一致
        self.assertIn(INVALID_IMAGE_MESSAGE, msg)
        self.assertIn("HEIC", msg)
        self.assertIn("檔案損毀", msg)
        self.assertIn("JPEG", msg)
        self.assertIn("標本影像作業規範", msg)

    def test_valid_image_passes_and_resets_pointer(self):
        f = SimpleUploadedFile(
            "ok.png", _png_bytes(100, 100), content_type="image/png",
        )
        # 不應拋錯
        validate_specimen_image(f)
        # 指標必須歸零，否則後續上傳 Cloudinary 會拿到空檔案
        self.assertEqual(f.tell(), 0)

    def test_pointer_reset_even_when_unreadable(self):
        f = SimpleUploadedFile("bad.heic", b"xxxx", content_type="image/heic")
        with self.assertRaises(ValidationError):
            validate_specimen_image(f)
        self.assertEqual(f.tell(), 0)

    def test_attached_to_three_image_fields(self):
        for model_name in ("SpecimenImage", "SpeciesImage", "ObservationImage"):
            model = __import__(
                "collection.models", fromlist=[model_name]
            ).__dict__[model_name]
            validators = model._meta.get_field("image").validators
            self.assertIn(
                validate_specimen_image, validators,
                f"{model_name}.image 未掛上 validate_specimen_image",
            )

    def test_constants(self):
        self.assertEqual(MAX_UPLOAD_BYTES, 5 * 1024 * 1024)
        self.assertEqual(MAX_MEGAPIXELS, 25)


class AdminImageInlineMessageTests(TestCase):
    """三個影像 inline 的 admin 表單覆寫 invalid_image 訊息（含 unfold 樣式保留檢查）。"""

    def _inlines(self):
        from .admin import (
            ObservationImageInline, SpeciesImageInline, SpecimenImageInline,
        )
        return [
            (SpecimenImageInline, Specimen),
            (SpeciesImageInline, Species),
            (ObservationImageInline, Observation),
        ]

    def test_all_three_inlines_override_invalid_image(self):
        from django import forms as djforms

        su = User.objects.create_superuser("su", "su@example.com", "x")
        req = RequestFactory().get("/")
        req.user = su

        for inline_cls, parent_model in self._inlines():
            inline = inline_cls(parent_model, site)
            formset = inline.get_formset(req)
            form = formset.form()   # 工廠已把 model 設好，__init__ 會套用覆寫
            field = form.fields["image"]
            # 訊息已被覆寫成統一文字
            self.assertEqual(
                field.error_messages["invalid_image"], INVALID_IMAGE_MESSAGE,
                f"{inline_cls.__name__} 未覆寫 invalid_image",
            )
            # 欄位仍是 ImageField（沒有被重新宣告取代）
            self.assertIsInstance(field, djforms.ImageField)
            # unfold widget 仍生效（widget 由 formfield_callback 套用，未被動到）
            self.assertIn(
                "unfold", type(field.widget).__module__,
                f"{inline_cls.__name__} 的 image widget 不是 unfold 樣式",
            )

    def test_message_matches_validator_message(self):
        # validators.py 情況 (c) 與 admin 覆寫共用同一常數 → 兩條路徑一致
        self.assertIn("無法讀取此影像檔", INVALID_IMAGE_MESSAGE)
        self.assertIn("HEIC", INVALID_IMAGE_MESSAGE)
        self.assertIn("標本影像作業規範", INVALID_IMAGE_MESSAGE)


class _FakeSpecimenForm:
    """替 save_model 準備最小可用的表單替身。

    save_model 只會讀取 form.initial（取原始主鍵）與 new_species_name／
    resolved_species（學名解析結果）；此處學名不變動，兩者皆設 None。
    """

    def __init__(self, original_catalog_number):
        self.initial = {"catalog_number": original_catalog_number}
        self.new_species_name = None
        self.resolved_species = None


class CatalogNumberRelocationTests(TestCase):
    """superuser 變更典藏編號（主鍵）時，子表關聯應完整搬移、無孤兒、無重複，
    且每次變更都留下可反查的稽核紀錄（CatalogNumberChange）。"""

    @classmethod
    def setUpTestData(cls):
        cls.su = User.objects.create_superuser("root", "root@example.com", "x")
        cls.species = Species.objects.create(
            common_name="大冠鷲", scientific_name="Spilornis cheela",
            taxon_group=Species.TaxonGroup.BIRD,
            conservation_status=Species.ConservationStatus.GENERAL,
        )

    def _admin(self):
        return SpecimenAdmin(Specimen, site)

    def _su_request(self):
        """帶 messages 儲存的 superuser 請求（save_model 會呼叫 message_user）。"""
        req = RequestFactory().post("/admin/collection/specimen/")
        req.user = User.objects.get(pk=self.su.pk)
        req.session = {}
        setattr(req, "_messages", FallbackStorage(req))
        return req

    def _make_specimen(self, catalog_number):
        sp = Specimen.objects.create(
            catalog_number=catalog_number,
            taxon_group=Specimen.TaxonGroup.BIRD,
            specimen_type=Specimen.SpecimenType.TAXIDERMY,
            accession_year=2026,
        )
        Movement.objects.create(
            specimen=sp,
            movement_type=Movement.MovementType.STORE_IN,
            movement_date=datetime.date(2026, 1, 1),
        )
        Identification.objects.create(
            specimen=sp, identified_as=self.species,
            identified_date=datetime.date(2026, 1, 2),
        )
        SpecimenImage.objects.create(
            specimen=sp, image_type=SpecimenImage.ImageType.BODY,
            image=SimpleUploadedFile("body.jpg", b"not-really-an-image"),
        )
        return sp

    def _rename(self, new_number, original_number):
        """透過 admin.save_model 走完整改主鍵流程（含 superuser／pk 變更判斷）。"""
        admin = self._admin()
        obj = Specimen.objects.get(pk=original_number)  # 既載入實例
        obj.catalog_number = new_number
        form = _FakeSpecimenForm(original_number)
        admin.save_model(self._su_request(), obj, form, change=True)

    # ── 4. 改編號 → 關聯搬移成功 + 寫入異動紀錄 ──────────────────────────
    def test_rename_relocates_children_and_writes_audit(self):
        sp = self._make_specimen("LYM-AV-2026-0001")
        original_uuid = sp.occurrence_uuid
        self._rename("LYM-AV-2026-9001", "LYM-AV-2026-0001")

        # 舊列消失、新列存在
        self.assertFalse(Specimen.objects.filter(pk="LYM-AV-2026-0001").exists())
        self.assertTrue(Specimen.objects.filter(pk="LYM-AV-2026-9001").exists())
        # 全球唯一識別碼隨標本延續、不變（新列還原為原始 uuid）
        moved = Specimen.objects.get(pk="LYM-AV-2026-9001")
        self.assertEqual(moved.occurrence_uuid, original_uuid)
        # 三張子表都指向新編號
        self.assertEqual(
            Movement.objects.filter(specimen_id="LYM-AV-2026-9001").count(), 1
        )
        self.assertEqual(
            Identification.objects.filter(
                specimen_id="LYM-AV-2026-9001"
            ).count(), 1
        )
        self.assertEqual(
            SpecimenImage.objects.filter(
                specimen_id="LYM-AV-2026-9001"
            ).count(), 1
        )
        # 寫入一筆稽核紀錄，指向新編號、記錄舊→新與修改者
        change = CatalogNumberChange.objects.get()
        self.assertEqual(change.old_catalog_number, "LYM-AV-2026-0001")
        self.assertEqual(change.new_catalog_number, "LYM-AV-2026-9001")
        self.assertEqual(change.specimen_id, "LYM-AV-2026-9001")
        self.assertEqual(change.changed_by_id, self.su.pk)

    # ── 5. 重複編號被擋下（繁中錯誤）─────────────────────────────────
    def test_duplicate_catalog_number_rejected(self):
        self._make_specimen("LYM-AV-2026-0001")
        other = self._make_specimen("LYM-AV-2026-0002")

        # 把 other 改成已存在的 0001 → 表單驗證應擋下
        form = SpecimenAdminForm(
            data={
                "catalog_number": "LYM-AV-2026-0001",
                "accession_year": other.accession_year,
                "taxon_group": Specimen.TaxonGroup.BIRD,
                "identification_status": (
                    Specimen.IdentificationStatus.UNIDENTIFIED
                ),
                "specimen_type": Specimen.SpecimenType.TAXIDERMY,
                "individual_count": 1,
                "publication_status": PublicationStatus.DRAFT,
                "status": Specimen.Status.IN_STORAGE,
            },
            instance=Specimen.objects.get(pk="LYM-AV-2026-0002"),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("catalog_number", form.errors)
        self.assertIn("已被其他標本使用", str(form.errors["catalog_number"]))
        # 兩件標本都還在、未被搬動
        self.assertTrue(Specimen.objects.filter(pk="LYM-AV-2026-0001").exists())
        self.assertTrue(Specimen.objects.filter(pk="LYM-AV-2026-0002").exists())

    # ── 6. 改編號後：每張子表筆數不變、無孤兒、無重複 ───────────────────
    def test_no_orphans_no_duplicates_after_rename(self):
        self._make_specimen("LYM-AV-2026-0001")
        self._rename("LYM-AV-2026-9001", "LYM-AV-2026-0001")

        for model in (Movement, Identification, SpecimenImage):
            # 總筆數不變（仍為 1）
            self.assertEqual(model.objects.count(), 1, model.__name__)
            # 無指向已不存在標本的孤兒
            orphans = model.objects.exclude(
                specimen_id__in=Specimen.objects.values("pk")
            )
            self.assertEqual(orphans.count(), 0, f"{model.__name__} 出現孤兒")
            # 無殘留指向舊編號者
            self.assertEqual(
                model.objects.filter(
                    specimen_id="LYM-AV-2026-0001"
                ).count(), 0, f"{model.__name__} 殘留舊編號",
            )
        # Specimen 本身無重複（新編號僅一列）
        self.assertEqual(Specimen.objects.count(), 1)

    # ── 7. 關鍵案例：連續改兩次，早期稽核列不得斷鏈成 NULL ──────────────
    def test_double_rename_keeps_earlier_audit_linked(self):
        self._make_specimen("LYM-AV-2026-0001")
        self._rename("LYM-AV-2026-9001", "LYM-AV-2026-0001")  # 第一次
        self._rename("LYM-AV-2026-9002", "LYM-AV-2026-9001")  # 第二次

        # 標本最終編號
        self.assertTrue(Specimen.objects.filter(pk="LYM-AV-2026-9002").exists())
        self.assertEqual(Specimen.objects.count(), 1)

        # 共兩筆稽核紀錄，皆非孤兒、皆指向最新編號
        changes = CatalogNumberChange.objects.all()
        self.assertEqual(changes.count(), 2)
        for c in changes:
            self.assertIsNotNone(
                c.specimen_id, "早期稽核紀錄的 specimen 被靜默設為 NULL"
            )
            self.assertEqual(c.specimen_id, "LYM-AV-2026-9002")

        # 第一次那筆（0001→9001）仍在、specimen 未斷鏈
        first = CatalogNumberChange.objects.get(
            old_catalog_number="LYM-AV-2026-0001"
        )
        self.assertEqual(first.new_catalog_number, "LYM-AV-2026-9001")
        self.assertEqual(first.specimen_id, "LYM-AV-2026-9002")

        # 兩筆都能從標本頁（reverse 關聯）反查到
        specimen = Specimen.objects.get(pk="LYM-AV-2026-9002")
        self.assertEqual(specimen.catalog_number_changes.count(), 2)

    # ── 搬移中途失敗 → 整批 rollback，標本不得停留在暫時 uuid 狀態 ──────
    def test_relocation_rolls_back_on_error(self):
        from unittest import mock

        sp = self._make_specimen("LYM-AV-2026-0001")
        original_uuid = sp.occurrence_uuid

        admin = self._admin()
        obj = Specimen.objects.get(pk="LYM-AV-2026-0001")  # 既載入實例
        obj.catalog_number = "LYM-AV-2026-9001"
        form = _FakeSpecimenForm("LYM-AV-2026-0001")

        # 在最後一步（寫稽核紀錄）注入例外：此時暫時 uuid 已寫入、舊列已刪，
        # 若非同一交易，標本會卡在暫時 uuid；atomic 應把整批回滾。
        with mock.patch.object(
            CatalogNumberChange.objects, "create",
            side_effect=RuntimeError("模擬搬移中途失敗"),
        ):
            with self.assertRaises(RuntimeError):
                admin.save_model(self._su_request(), obj, form, change=True)

        # 整批 rollback：舊編號那列還在、新編號不存在
        self.assertTrue(Specimen.objects.filter(pk="LYM-AV-2026-0001").exists())
        self.assertFalse(
            Specimen.objects.filter(pk="LYM-AV-2026-9001").exists()
        )
        # 關鍵：occurrence_uuid 與 catalog_number 都維持原值（未停在暫時 uuid）
        restored = Specimen.objects.get(pk="LYM-AV-2026-0001")
        self.assertEqual(restored.occurrence_uuid, original_uuid)
        self.assertEqual(restored.catalog_number, "LYM-AV-2026-0001")
        # 子表仍指向原編號、無孤兒；稽核表無殘留
        for model in (Movement, Identification, SpecimenImage):
            self.assertEqual(
                model.objects.filter(
                    specimen_id="LYM-AV-2026-0001"
                ).count(), 1, model.__name__,
            )
        self.assertEqual(CatalogNumberChange.objects.count(), 0)


class EcologicalGroupTests(TestCase):
    """查詢頁「簡易版」生態分群推導與篩選。"""

    @classmethod
    def setUpTestData(cls):
        from .ecological_groups import EcoGroup
        cls.EcoGroup = EcoGroup

        def make(common, sci, order="", **extra):
            return Species.objects.create(
                common_name=common, scientific_name=sci,
                taxon_group=Species.TaxonGroup.BIRD, order=order,
                conservation_status=Species.ConservationStatus.GENERAL,
                publication_status=PublicationStatus.PUBLISHED, **extra,
            )

        # 屬名可直接命中對照表（即使目空白）
        cls.raptor = make("黑鳶", "Milvus migrans")
        cls.waterbird = make("小白鷺", "Egretta garzetta")
        cls.songbird = make("麻雀", "Passer montanus")
        cls.landfowl = make("珠頸斑鳩", "Spilopelia chinensis")
        # 燕科：分類屬雀形目（連目都填了），但策展上仍歸「其他（特殊生態）」，
        # 用以驗證屬名對照表優先於目級判斷。
        cls.swallow = make("家燕", "Hirundo rustica", order="雀形目")
        # 屬名未收錄、但有填「目」→ 由目後援歸類
        cls.by_order = make("某鳴禽", "Zzzunknownus testus", order="雀形目")
        # 屬名未收錄且目空白 → 落「其他」
        cls.unmapped = make("未知鳥", "Xxxunknownus ignotus")

    def test_eco_group_of_by_genus(self):
        from .ecological_groups import eco_group_of
        self.assertEqual(eco_group_of(self.raptor), self.EcoGroup.RAPTOR)
        self.assertEqual(eco_group_of(self.waterbird), self.EcoGroup.WATERBIRD)
        self.assertEqual(eco_group_of(self.songbird), self.EcoGroup.SONGBIRD)
        self.assertEqual(eco_group_of(self.landfowl), self.EcoGroup.LANDFOWL)

    def test_special_family_overrides_order(self):
        """燕科屬名優先於目級判斷，維持「其他」。"""
        from .ecological_groups import eco_group_of
        self.assertEqual(eco_group_of(self.swallow), self.EcoGroup.OTHER)

    def test_eco_group_falls_back_to_order(self):
        from .ecological_groups import eco_group_of
        self.assertEqual(eco_group_of(self.by_order), self.EcoGroup.SONGBIRD)

    def test_unmapped_falls_to_other(self):
        from .ecological_groups import eco_group_of, is_unmapped
        self.assertEqual(eco_group_of(self.unmapped), self.EcoGroup.OTHER)
        self.assertTrue(is_unmapped(self.unmapped))
        # 燕科雖歸「其他」，但屬名已收錄，不算未對應
        self.assertFalse(is_unmapped(self.swallow))

    def test_list_filters_by_eco_group(self):
        """簡易版帶 ?eco_group= 只回該生態分群的物種。"""
        resp = self.client.get(
            reverse("public_species_list"), {"eco_group": "raptor"}
        )
        self.assertContains(resp, "Milvus migrans")
        self.assertNotContains(resp, "Egretta garzetta")
        self.assertNotContains(resp, "Passer montanus")

    def test_research_view_filters_by_order(self):
        """研究檢視帶 ?order= 精確比對「目」欄位。"""
        resp = self.client.get(
            reverse("public_species_list"),
            {"view": "research", "order": "雀形目"},
        )
        self.assertContains(resp, "Zzzunknownus testus")
        self.assertNotContains(resp, "Milvus migrans")

