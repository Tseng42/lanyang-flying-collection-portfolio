"""公開狀態控制（publication_status）相關測試。

涵蓋：對外頁面只撈公開資料、未公開物種 404、後台依權限限制可選狀態與批次動作、
以及 Darwin Core 匯出「含未公開資料」的權限控管。
"""

import datetime

from django.contrib.admin.sites import site
from django.contrib.auth.models import Group, Permission, User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .admin import SpecimenAdmin
from .models import (
    CollectionEvent, Observation, PublicationStatus, Species, Specimen,
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
        print("\n[QR內容] " + row[7])   # 供人工確認格式
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
