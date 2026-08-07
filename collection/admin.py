"""蘭陽博物館 飛行動物典藏與紀錄系統 — Admin 設定

三個模型皆可於 Admin 新增／編輯／查詢／篩選；
在 Species 頁面以 inline 直接檢視其底下的標本與觀察紀錄。
"""

import csv
import difflib
import io
import re
import uuid
from urllib.parse import quote

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from import_export.admin import ImportMixin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.import_export.forms import ImportForm
from unfold.decorators import display
from unfold.forms import (
    AdminPasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)
# unfold 的輸入框樣式類別；套到宣告式表單欄位（非 model 欄位不會被 unfold 自動套用）
from unfold.widgets import INPUT_CLASSES

from .models import (
    CATALOG_NUMBER_RE, CatalogNumberChange, CollectionEvent, Identification,
    Movement, Observation, ObservationImage, PublicationStatus, Species,
    SpeciesImage, Specimen, SpecimenImage,
)
from .darwin_core import build_darwin_core_zip
from .resources import SpecimenImportResource
from .validators import INVALID_IMAGE_MESSAGE


class ImageErrorMessageAdminForm(forms.ModelForm):
    """影像 inline 共用表單：只覆寫 image 欄位的 invalid_image 錯誤訊息。

    django-unfold 的 widget／樣式由 ModelAdmin/Inline 的 formfield_for_dbfield
    （formfield_callback）套用；這裡「只在 __init__ 事後改 error_messages」，
    不重新宣告欄位、不動 widget，因此外觀與改動前完全一致，也不影響 model 定義
    （不會產生 migration）。三個影像 inline（標本／物種／觀察）共用同一份表單。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "image" in self.fields:
            self.fields["image"].error_messages["invalid_image"] = (
                INVALID_IMAGE_MESSAGE
            )


# ── 公開狀態徽章配色（Unfold @display(label=...)：值 → 語意色）─────────────
# 草稿=灰（預設）、待審=橙黃(warning)、公開=綠(success)。
PUBLICATION_LABELS = {
    PublicationStatus.DRAFT: "",             # 未匹配任何語意色 → 灰底
    PublicationStatus.REVIEW: "warning",     # 橙黃
    PublicationStatus.PUBLISHED: "success",  # 綠
}


class PublicationAdminMixin:
    """三個模型（物種／標本／觀察紀錄）共用的「公開狀態」後台行為。

    子類別須設定 publish_permission，例如 "collection.can_publish_specimen"。
    提供：狀態徽章、依權限限制可選狀態、已公開者對無權限者唯讀、批次動作
    （設為草稿／待審／公開，其中「設為公開」需公開權限）。
    """

    publish_permission = None

    @display(
        description="公開狀態",
        ordering="publication_status",
        label=PUBLICATION_LABELS,
    )
    def publication_badge(self, obj):
        # 回傳 (值, 顯示文字)：Unfold 依「值」查配色、以「顯示文字」渲染中文
        return obj.publication_status, obj.get_publication_status_display()

    def has_publish_permission(self, request):
        """目前使用者是否可將此模型的資料設為公開。"""
        return bool(self.publish_permission) and request.user.has_perm(
            self.publish_permission
        )

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        # 無公開權限者：公開狀態欄位僅顯示「草稿」「待審」，不出現「公開」選項
        if db_field.name == "publication_status" and not self.has_publish_permission(
            request
        ):
            kwargs["choices"] = [
                (value, label)
                for value, label in PublicationStatus.choices
                if value != PublicationStatus.PUBLISHED
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        # 該筆原本已是公開狀態，且目前使用者無公開權限 → 不得改動此欄位
        if (
            obj is not None
            and obj.publication_status == PublicationStatus.PUBLISHED
            and not self.has_publish_permission(request)
            and "publication_status" not in readonly
        ):
            readonly.append("publication_status")
        return readonly

    @admin.action(description="批次設為草稿")
    def make_draft(self, request, queryset):
        updated = queryset.update(publication_status=PublicationStatus.DRAFT)
        self.message_user(
            request, f"已將 {updated} 筆設為「草稿」。", level=messages.INFO,
        )

    @admin.action(description="批次設為待審")
    def make_review(self, request, queryset):
        updated = queryset.update(publication_status=PublicationStatus.REVIEW)
        self.message_user(
            request, f"已將 {updated} 筆設為「待審」。", level=messages.INFO,
        )

    # permissions=["publish"] → Django 會呼叫 has_publish_permission 決定是否
    # 於下拉選單顯示此動作；無權限者根本看不到「批次設為公開」。
    @admin.action(description="批次設為公開", permissions=["publish"])
    def make_published(self, request, queryset):
        # 後端二次驗證：即使前端動作被隱藏，仍嚴格把關（不可只靠 UI 隱藏）
        if not self.has_publish_permission(request):
            raise PermissionDenied
        updated = queryset.update(publication_status=PublicationStatus.PUBLISHED)
        self.message_user(
            request, f"已將 {updated} 筆設為「公開」。", level=messages.INFO,
        )


# 登入頁改用「繼承自 unfold 登入頁」的模板（僅在 login_after 附加密碼顯示切換，
# 不整份複製，避免日後 unfold 更新失效）。
admin.site.login_template = "admin/login_toggle.html"


class HazardFilter(admin.SimpleListFilter):
    """依危害標記篩選標本（SQLite 的 JSONField 不支援 contains，改以 Python 比對）。"""

    title = "危害標記"
    parameter_name = "hazard"

    def lookups(self, request, model_admin):
        return [
            ("any", "有危害標記"),
            ("as", "含砷 As"),
            ("hg", "含汞 Hg"),
            ("other", "含其他防蟲劑"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "any":
            return queryset.exclude(hazard_markers=[])
        if value in ("as", "hg", "other"):
            ids = [
                s.pk for s in queryset
                if value in (s.hazard_markers or [])
            ]
            return queryset.filter(pk__in=ids)
        return queryset


class CompletenessFilter(admin.SimpleListFilter):
    """待補完標本：缺鑑定者 或 缺採集日期。"""

    title = "建檔完整度"
    parameter_name = "completeness"

    def lookups(self, request, model_admin):
        return [("incomplete", "待補完（缺鑑定者或採集日期）")]

    def queryset(self, request, queryset):
        if self.value() == "incomplete":
            return queryset.filter(
                Q(identified_by="")
                | Q(identified_by__isnull=True)
                | Q(collection_date__isnull=True)
            )
        return queryset


class SpecimenAdminForm(forms.ModelForm):
    """標本表單：學名可直接打字（後端比對既有物種，找不到則自動建立）；
    同時保留 species 下拉選取與「＋新增物種」彈窗，供使用者依習慣選擇。"""

    # 學名純文字輸入（標準 forms.TextInput，無自訂 widget／template、無 JS）。
    # 因是宣告式表單欄位（非 model 欄位），unfold 不會自動套用輸入框樣式，
    # 故以標準 widget 的 attrs 帶入 unfold 的 INPUT_CLASSES，否則輸入框無框線近乎不可見。
    species_input = forms.CharField(
        required=False,
        label="學名",
        help_text=(
            "可直接輸入學名或中文俗名。若系統中已有相符的物種會自動連結，"
            "沒有則會自動建立一筆新物種（保育等級預設「待查證」）。尚未鑑定可留空。"
            "（若想改用下方選單挑選既有物種，請先清空此欄位。）"
        ),
        widget=forms.TextInput(attrs={"class": " ".join(INPUT_CLASSES)}),
    )
    confirm_duplicate = forms.BooleanField(
        required=False,
        label="確認仍要存入（可能重複）",
        help_text="偵測到疑似重複時，勾選此項可強制存入。",
    )
    hazard_markers = forms.MultipleChoiceField(
        choices=Specimen.HAZARD_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="危害標記",
        help_text="舊標本若含砷／汞防蟲劑請務必標記，保護操作人員。可複選；都不勾即代表「無」。",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 編輯既有標本時，以現有物種學名預填輸入框
        if self.instance and self.instance.pk and self.instance.species_id:
            self.fields["species_input"].initial = (
                self.instance.species.scientific_name
            )
        # 下拉選取路徑（Django admin 標準 select + 預設＋新增彈窗）；文字框優先
        if "species" in self.fields:
            self.fields["species"].required = False
            self.fields["species"].label = "（或）選取既有物種"
            self.fields["species"].help_text = (
                "可從下拉選取既有物種，或按右側＋開啟彈窗完整新增一筆。"
                "若上方「學名」已輸入，將以上方為準。"
            )

    @staticmethod
    def _find_similar_species(raw, cutoff=0.85, n=3):
        """用 difflib 找與輸入高度相似的既有物種（比對學名與中文名）。"""
        candidates = {}
        for s in Species.objects.all():
            candidates[s.scientific_name.lower()] = s
            if s.common_name:
                candidates.setdefault(s.common_name.lower(), s)
        close = difflib.get_close_matches(
            raw.lower(), list(candidates.keys()), n=n, cutoff=cutoff
        )
        seen, result = set(), []
        for key in close:
            s = candidates[key]
            if s.pk not in seen:
                seen.add(s.pk)
                result.append(s)
        return result

    class Meta:
        model = Specimen
        # 保留 species（下拉，與 species_input 並存）；採集欄位改由「採集事件」提供，
        # 故排除舊採集欄位，避免被空值覆寫（欄位仍保留於資料庫供歷史查考）。
        exclude = (
            "collector", "collection_date", "collection_location",
            "latitude", "longitude",
        )
        # 中文說明文字（設在表單層，不更動資料模型）
        help_texts = {
            "catalog_number": (
                "格式：LYM-類群代碼-年份-4位流水號（例：LYM-AV-2026-0001、LYM-IN-2026-0023）。"
                "類群代碼：鳥=AV、昆蟲=IN、蝙蝠飛鼠=MA。"
                "選好物種後系統會自動建議下一個可用編號，可自行修改；留空存檔亦會自動產生。"
            ),
            "specimen_type": "標本的製作方式（剝製／針插／浸液／骨骼）。",
            "collector": "採集者姓名。基於隱私，公開頁不會顯示。",
            "collection_date": "標本的採集日期。",
            "collection_location": "採集地點。公開頁對保育類物種僅顯示到縣市層級。",
            "latitude": "十進位緯度。保育類物種座標不會顯示在公開頁。",
            "longitude": "十進位經度。保育類物種座標不會顯示在公開頁。",
            "accession_date": "標本入藏本館的日期（必填，預設今天）。",
            "source": "標本的取得方式（捐贈／救傷／採集／移交）。",
            "status": "標本目前狀態，預設「在庫」。",
            "preservation_status": "標本保存狀況描述，例如：完整、部分破損。",
            "storeroom": "存放的庫房名稱或編號。",
            "cabinet": "存放的櫃號。",
            "drawer": "存放的抽屜號。",
            "identified_by": "鑑定者姓名。",
            "identified_date": "本次鑑定的日期。",
            "remarks": "其他補充說明，可留空。",
        }

    def clean_catalog_number(self):
        value = (self.cleaned_data.get("catalog_number") or "").strip()
        # 編輯既有標本時（superuser 才可改此欄）不可清空：既有標本主鍵留空會觸發
        # 自動重編，反而製造重複列與孤兒關聯。新增時留空才允許自動產生。
        if not self.instance._state.adding and not value:
            raise forms.ValidationError(
                "既有標本的典藏編號不可清空。如需變更，請直接填入新的編號。"
            )
        # 留空 → 存檔時自動產生；有填才驗證格式
        if value and not CATALOG_NUMBER_RE.match(value):
            raise forms.ValidationError(
                "典藏編號格式不符。正確格式為 LYM-類群代碼-年份-4位流水號，"
                "例如：LYM-AV-2026-0001（類群代碼：鳥=AV、昆蟲=IN、蝙蝠飛鼠=MA）。"
            )
        # 唯一性驗證：典藏編號為主鍵，不得與其他標本重複。
        # superuser 修改既有標本編號、或新增時手填編號，都在此擋下重複值。
        # clean_<field> 階段 self.instance 仍保有載入時的原始主鍵，故可據以排除自己。
        if value:
            original_pk = (
                None if self.instance._state.adding else self.instance.pk
            )
            duplicates = Specimen.objects.filter(pk=value)
            if original_pk:
                duplicates = duplicates.exclude(pk=original_pk)
            if duplicates.exists():
                raise forms.ValidationError(
                    "此典藏編號已被其他標本使用（典藏編號為主鍵，必須唯一），"
                    "請改用不重複的編號。"
                )
        return value

    def clean(self):
        cleaned = super().clean()
        location = cleaned.get("collection_location")
        date = cleaned.get("collection_date")
        confirmed = cleaned.get("confirm_duplicate")

        # ── 學名解析：文字框優先；文字框留空才採用下拉選取 ──
        # 相似度不阻擋儲存，改於 save_model 以警告訊息事後提示。
        raw = (cleaned.get("species_input") or "").strip()
        self.resolved_species = None    # 比對到／下拉選取的既有物種
        self.new_species_name = None    # 需自動建立的學名（文字框無比對時）
        if raw:
            match = Species.objects.filter(
                Q(scientific_name__iexact=raw) | Q(common_name__iexact=raw)
            ).first()
            if match:
                self.resolved_species = match
            else:
                self.new_species_name = raw
        else:
            # 文字框留空 → 直接採用下拉選取的物種（可能為 None＝未鑑定）
            self.resolved_species = cleaned.get("species")

        # 供重複偵測使用（僅在比對到既有物種時才有意義；新建物種不可能重複）
        species = self.resolved_species

        # ── 日期邏輯驗證（欄位皆選填，僅在相關兩者都有值時檢查）──
        collection_date = cleaned.get("collection_date")
        acquisition_date = cleaned.get("acquisition_date")
        preparation_date = cleaned.get("preparation_date")
        identified_date = cleaned.get("identified_date")
        if preparation_date and acquisition_date and preparation_date < acquisition_date:
            self.add_error(
                "preparation_date", "製作完成日期不得早於取得日期。"
            )
        if preparation_date and collection_date and preparation_date < collection_date:
            self.add_error(
                "preparation_date", "製作完成日期不得早於採集日期。"
            )
        if identified_date and collection_date and identified_date < collection_date:
            self.add_error(
                "identified_date", "鑑定日期不得早於採集日期。"
            )

        # 三者齊全才有辨識重複的意義；已勾選確認則放行
        if species and location and date and not confirmed:
            dup = Specimen.objects.filter(
                species=species,
                collection_location=location,
                collection_date=date,
            )
            if self.instance.pk:
                dup = dup.exclude(pk=self.instance.pk)
            if dup.exists():
                raise forms.ValidationError(
                    "偵測到可能重複的標本（同物種、同採集地、同採集日期）。"
                    "若確定仍要存入，請勾選下方「確認仍要存入（可能重複）」後再送出一次。"
                )
        return cleaned


class SpecimenInline(TabularInline):
    """在物種頁面內嵌顯示其標本。"""

    model = Specimen
    fk_name = "species"
    extra = 0
    # 採集日期已移至採集事件，故此處不再顯示（避免顯示已停用的舊欄位）
    fields = ("catalog_number", "specimen_type")
    show_change_link = True


class ObservationInline(TabularInline):
    """在物種頁面內嵌顯示其觀察紀錄。"""

    model = Observation
    extra = 0
    fields = ("record_number", "data_source", "observation_date", "count")
    show_change_link = True


class SpeciesImageInline(TabularInline):
    """在物種頁面內嵌顯示其代表照片（比照標本影像 inline，含預覽縮圖）。"""

    model = SpeciesImage
    form = ImageErrorMessageAdminForm
    extra = 0
    fields = (
        "image", "thumbnail", "image_type", "is_primary", "is_public",
        "photographer", "license", "license_note", "caption",
    )
    readonly_fields = ("thumbnail",)

    @admin.display(description="預覽")
    def thumbnail(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height:80px;" />', obj.image.url,
            )
        return "—"


class ObservationImageInline(TabularInline):
    """在觀察紀錄頁面內嵌顯示其現場照片（比照標本影像 inline，含預覽縮圖）。"""

    model = ObservationImage
    form = ImageErrorMessageAdminForm
    extra = 0
    fields = (
        "image", "thumbnail", "image_type", "is_primary", "is_public",
        "photographer", "license", "license_note", "caption",
    )
    readonly_fields = ("thumbnail",)

    @admin.display(description="預覽")
    def thumbnail(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height:80px;" />', obj.image.url,
            )
        return "—"


class SpeciesAdminForm(forms.ModelForm):
    """物種表單：補上中文說明（設在表單層，不更動資料模型）。"""

    class Meta:
        model = Species
        fields = "__all__"
        help_texts = {
            "common_name": "物種的中文俗名，例如：小白鷺。",
            "scientific_name": "拉丁學名，例如 Egretta garzetta。此為物種唯一識別。",
            "taicol_taxon_id": "臺灣物種名錄 TaiCOL 的物種編號，用於連結外部資料；可留空。",
            "taxon_group": "物種所屬的飛行生物分類群（鳥／昆蟲／蝙蝠／飛鼠）。",
            "order": "分類階層：目。可留空，之後由 TaiCOL 補齊。",
            "family": "分類階層：科。可留空，之後由 TaiCOL 補齊。",
            "genus": "分類階層：屬。可留空，之後由 TaiCOL 補齊。",
            "conservation_status": (
                "依《野生動物保育法》的保育等級，必選。不確定時請選「待查證」。"
                "凡非「一般類」（含待查證）者，公開頁一律保護性處理："
                "觀察影像不對外顯示、標本地點僅到縣市層級。"
            ),
            "iucn_status": "IUCN 紅皮書受威脅等級。",
            "introduction": "公開頁顯示的物種簡介，請以淺白文字介紹。",
            "is_auto_created": "系統自動建立的物種會勾選此項，代表資料尚未補齊、不會顯示於公開簡易版。補齊分類階層與保育等級後，請「取消勾選」並儲存，該物種才會出現在公開簡易版。",
        }
        labels = {
            "is_auto_created": "系統自動建立（待查證）",
        }


@admin.register(Species)
class SpeciesAdmin(PublicationAdminMixin, ModelAdmin):
    form = SpeciesAdminForm
    publish_permission = "collection.can_publish_species"
    list_display = (
        "common_name", "scientific_name_italic", "taxon_group",
        "conservation_status", "publication_badge", "is_auto_created",
    )
    list_filter = (
        "publication_status", "taxon_group", "conservation_status",
        "is_auto_created",
    )
    actions = ["make_published", "make_review", "make_draft", "make_verified"]
    search_fields = ("common_name", "scientific_name")
    inlines = (SpeciesImageInline, SpecimenInline, ObservationInline)

    class Media:
        # 選到「非一般類」保育等級時，於表單顯示醒目提示（純前端提示，不影響儲存）
        js = ("collection/js/conservation_hint.js",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # 保育等級為「待查證」時提醒盡快查證（仍允許儲存）
        if obj.conservation_status in ("", Species.ConservationStatus.UNVERIFIED):
            self.message_user(
                request,
                f"「{obj}」的保育等級為「待查證」，請盡快查證確認；"
                "在此之前，公開頁會以保護性方式處理其觀察影像與地點。",
                level=messages.WARNING,
            )

    @admin.action(description="標記為已查證（清除自動建立標示，將顯示於公開簡易版）")
    def make_verified(self, request, queryset):
        updated = queryset.update(is_auto_created=False)
        self.message_user(
            request,
            f"已將 {updated} 筆物種標記為已查證，這些物種將顯示於公開簡易版。",
            level=messages.SUCCESS,
        )

    @admin.display(description="學名", ordering="scientific_name")
    def scientific_name_italic(self, obj):
        # 列表頁學名：襯線字體 + 斜體（拉丁學名慣例，僅套用於顯示）
        return format_html(
            '<span style="font-family:Georgia,\'Times New Roman\',serif;'
            'font-style:italic;">{}</span>',
            obj.scientific_name,
        )

    fieldsets = (
        ("公開設定", {
            "fields": ("publication_status", "is_auto_created"),
            "description": (
                "僅「公開」狀態的物種會出現在對外檢索、物種頁與公開統計；"
                "草稿與待審僅供館內作業。設為公開需具備公開權限。"
            ),
        }),
        ("基本分類", {
            "fields": (
                "common_name", "other_common_names",
                "scientific_name", "scientific_name_authorship",
                "taicol_taxon_id", "taxon_group", "order", "family", "genus",
            ),
            "description": "必填欄位：<b>學名</b>、<b>分類群</b>（表單中以粗體標示）。",
        }),
        ("保育等級", {
            "fields": ("conservation_status", "iucn_status"),
        }),
        ("物種介紹", {
            "classes": ["collapse"],
            "fields": ("public_description", "introduction"),
        }),
    )


class MovementInlineFormSet(forms.BaseInlineFormSet):
    """驗證借出／歸還日期邏輯。

    Movement 為「單一事件單一日期」的紀錄（借出、歸還各為一列），
    故以跨列規則檢查：任何「歸還」的日期不得早於最早的「借出」日期。
    """

    def clean(self):
        super().clean()
        loan_dates, return_dates = [], []
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            cd = form.cleaned_data
            if not cd or cd.get("DELETE"):
                continue
            mtype, mdate = cd.get("movement_type"), cd.get("movement_date")
            if not mtype or not mdate:
                continue
            if mtype == Movement.MovementType.LOAN_OUT:
                loan_dates.append(mdate)
            elif mtype == Movement.MovementType.RETURN:
                return_dates.append(mdate)
        if loan_dates and return_dates and min(return_dates) < min(loan_dates):
            raise forms.ValidationError(
                "「歸還」的日期不得早於「借出」的日期，請檢查異動紀錄的日期。"
            )


class MovementInline(TabularInline):
    """在標本頁面內嵌顯示異動紀錄（最新在上）。"""

    model = Movement
    formset = MovementInlineFormSet
    extra = 0
    fields = (
        "movement_type", "movement_date", "counterparty",
        "handler", "remarks",
    )


class SpecimenImageInline(TabularInline):
    """在標本頁面內嵌顯示標本照片。"""

    model = SpecimenImage
    form = ImageErrorMessageAdminForm
    extra = 0
    fields = (
        "image", "thumbnail", "image_type", "is_primary", "is_public",
        "photographer", "license", "license_note", "caption",
    )
    readonly_fields = ("thumbnail",)

    @admin.display(description="預覽")
    def thumbnail(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height:80px;" />', obj.image.url,
            )
        return "—"


class IdentificationInline(TabularInline):
    """在標本頁面內嵌顯示鑑定歷程（最新在上）。"""

    model = Identification
    extra = 0
    fields = (
        "identified_as", "identified_by", "identified_date",
        "is_current", "basis",
    )
    autocomplete_fields = ("identified_as",)


@admin.register(Specimen)
class SpecimenAdmin(PublicationAdminMixin, ImportMixin, ModelAdmin):
    form = SpecimenAdminForm
    publish_permission = "collection.can_publish_specimen"
    inlines = (IdentificationInline, MovementInline, SpecimenImageInline)
    # ── 標本批次匯入（django-import-export ＋ unfold 樣式）─────────────────
    # 「匯入」按鈕出現在標本清單頁；上傳範本後先 dry-run 預覽，確認才寫入。
    resource_classes = [SpecimenImportResource]
    import_form_class = ImportForm
    # 自訂模板：在預覽頁最上方顯示「將新增 X 件標本、Y 筆新物種、Z 筆採集事件」
    import_template_name = "admin/import_export/specimen_import.html"

    def has_import_permission(self, request):
        """可匯入者需同時具備新增標本與新增物種權限（匯入會一併建立物種）。
        對應群組：登錄員／典藏主管／管理員可匯入；唯讀研究員不可。"""
        return request.user.has_perm(
            "collection.add_specimen"
        ) and request.user.has_perm("collection.add_species")

    def get_import_data_kwargs(self, **kwargs):
        """確保「確認匯入」階段也在驗證錯誤時整批 rollback。

        import-export 於 dry-run 已會攔下驗證錯誤；此處額外要求正式匯入時
        （萬一預覽後資料狀態改變而出現驗證錯誤）同樣整批回滾，符合
        「任一列出錯就整批 rollback」的要求。"""
        import_kwargs = super().get_import_data_kwargs(**kwargs)
        import_kwargs["rollback_on_validation_errors"] = True
        return import_kwargs

    def add_success_message(self, result, request):
        """實際匯入完成後的訊息，改用本系統的彙總數字（含新物種／採集事件）。"""
        self.message_user(
            request,
            "批次匯入完成："
            f"新增 {getattr(result, 'lanyang_new_specimens', 0)} 件標本、"
            f"{getattr(result, 'lanyang_new_species', 0)} 筆新物種、"
            f"{getattr(result, 'lanyang_new_events', 0)} 筆採集事件。",
            level=messages.SUCCESS,
        )
    actions = [
        "make_published", "make_review", "make_draft",
        "export_darwin_core_csv", "export_darwin_core_csv_with_unpublished",
        "export_label_csv_utf8", "export_label_csv_big5",
    ]
    # 清單頁一次帶出物種，避免逐列查詢（N+1）
    list_select_related = ("species",)
    # 「以此為範本另存」：同物種/同地點的標本可沿用上一筆，主鍵留空會自動產生新編號
    save_as = True

    class Media:
        # 選好類群後自動建議典藏編號（學名改為純文字輸入，無 JS）
        js = ("collection/js/catalog_suggest.js",)

    def get_urls(self):
        custom = [
            path(
                "suggest-catalog/",
                self.admin_site.admin_view(self.suggest_catalog_view),
                name="collection_specimen_suggest_catalog",
            ),
        ]
        return custom + super().get_urls()

    def suggest_catalog_view(self, request):
        """回傳指定類群的下一個可用典藏編號（供表單自動建議）。

        優先用 taxon_group 參數；為相容舊呼叫，僅帶 species 時沿用其類群。
        """
        taxon_group = request.GET.get("taxon_group") or ""
        if not taxon_group:
            try:
                species = Species.objects.get(pk=request.GET.get("species"))
                taxon_group = species.taxon_group
            except (Species.DoesNotExist, ValueError, TypeError):
                return JsonResponse({"catalog_number": ""})
        if taxon_group not in Specimen.GROUP_CODE:
            return JsonResponse({"catalog_number": ""})
        # 預覽用年份：優先取表單傳入的入藏年份，否則以今年估算。此處僅為即時建議，
        # 真正的建號一律於存檔時以標本的入藏年份 accession_year 為準。
        try:
            year = int(request.GET.get("year") or "")
        except (TypeError, ValueError):
            year = timezone.localdate().year
        return JsonResponse({
            "catalog_number": Specimen.next_catalog_number(taxon_group, year),
        })
    list_display = (
        "catalog_number", "species_or_group", "specimen_type", "status",
        "identification_status", "publication_badge", "hazard_flag",
    )
    # 「入藏年份」filter 已移除：accession_date 已停用（預設為建檔當天），
    # 篩選無意義；acquisition_date 填寫率過低（實測 0%），亦不適合作為篩選依據。
    list_filter = (
        "publication_status",
        "taxon_group", "identification_status", "status",
        "preparation_status", "cause_of_death",
        HazardFilter, CompletenessFilter,
    )

    @admin.display(description="物種／類群", ordering="species__scientific_name")
    def species_or_group(self, obj):
        # 尚未鑑定（無 species）時顯示類群，不顯示空白或 None
        if obj.species_id:
            return str(obj.species)
        return f"（未鑑定・{obj.get_taxon_group_display()}）"
    search_fields = (
        "catalog_number", "species__scientific_name",
        "species__common_name",
        # 採集者／地點改由採集事件搜尋（舊欄位已撤出表單）
        "collection_event__collector", "collection_event__collection_location",
    )
    # date_hierarchy 已移除：原本用 accession_date（已停用、等同建檔日期），無篩選意義。
    # 採集事件下拉搜尋 + 右側「＋新增採集事件」彈窗
    autocomplete_fields = ("collection_event",)

    fieldsets = (
        ("基本資料", {
            "fields": (
                "catalog_number", "accession_year", "occurrence_uuid",
                "taxon_group", "identification_status",
                "species_input", "species", "specimen_type",
                "sex", "life_stage", "individual_count", "basis_of_record",
            ),
            "description": (
                "必填：<b>類群</b>、<b>標本類型</b>。<b>學名</b>可直接於上方輸入框打字："
                "比對到既有物種即沿用，找不到會自動建立（保育等級「待查證」）；"
                "也可清空輸入框、改用下方「選取既有物種」下拉／＋新增彈窗。"
                "尚未鑑定可留空。典藏編號留空會自動產生。"
            ),
        }),
        ("公開設定", {
            "fields": ("publication_status",),
            "description": (
                "僅「公開」狀態的標本會出現在對外頁面與公開統計；"
                "草稿與待審僅供館內作業。設為公開需具備公開權限。"
            ),
        }),
        ("標本製作與來源", {
            "fields": (
                "preparation_status", "preservation_method",
                "cause_of_death", "cause_of_death_note",
                "acquisition_type", "acquisition_date",
                "source_institution", "permit_number",
                "preparer", "preparation_date", "storage_location",
            ),
            "description": (
                "支援冰箱中尚未製作的冷凍標本先行建檔；"
                "冷凍待處理者請於「製作狀態」標示。"
            ),
        }),
        ("採集事件", {
            "fields": ("collection_event",),
            "description": (
                "採集時間／地點／座標／採集者等改由「採集事件」提供，多件同一次採集"
                "的標本可共用一筆，避免重複輸入。可從下拉選取，或按右側＋新增一筆。"
                "來源不明者可留空。"
            ),
        }),
        # 「入藏資訊」fieldset 已移除：accession_date 與 source 停用、撤下表單
        # （欄位仍保留於資料庫）。取得方式／取得日期改於「標本製作與來源」填寫。
        ("狀態與危害", {
            "fields": ("status", "hazard_markers"),
        }),
        ("典藏位置", {
            "fields": ("storeroom", "cabinet", "drawer"),
        }),
        # ── 以下為次要區塊，預設折疊 ──
        ("鑑定與備註", {
            "classes": ["collapse"],
            "fields": ("identified_by", "identified_date", "remarks"),
        }),
        # 「影像」fieldset 已移除：Specimen.image 停用、撤下表單（欄位仍保留於
        # 資料庫）。標本照片改由下方「標本影像」inline（SpecimenImage，可多張）管理。
        ("防呆確認", {
            "classes": ["collapse"],
            "fields": ("confirm_duplicate",),
            "description": "偵測到疑似重複標本時，才需要勾選此項再送出。",
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        # 先取 mixin 的判斷（含「已公開且無權限 → publication_status 唯讀」）
        readonly = list(super().get_readonly_fields(request, obj))
        # occurrence_uuid 一律唯讀（自動產生、不得變更或清空）
        for field in ("basis_of_record", "occurrence_uuid"):
            if field not in readonly:
                readonly.append(field)
        # 典藏編號（主鍵）預設鎖定，避免改動造成關聯錯亂；
        # 唯有「編輯既有標本」且操作者為 superuser 時才開放編輯，
        # 變更時於 save_model 內以交易搬移子表關聯（見 _relocate_catalog_number）。
        # 新增標本頁（obj is None）維持原本可留空自動產生的行為，不列入唯讀。
        if (
            obj is not None
            and not request.user.is_superuser
            and "catalog_number" not in readonly
        ):
            readonly.append("catalog_number")
        return readonly

    # 類群 → 可靠對應的「目」（僅蝙蝠/飛鼠可靠；其餘留空）
    _ORDER_BY_GROUP = {"bat": "Chiroptera", "flying_squirrel": "Rodentia"}

    def _auto_create_species(self, scientific_name, taxon_group):
        """自動建立一筆物種：保育等級一律「待查證」、標記為自動產生。"""
        valid_groups = {v for v, _ in Species.TaxonGroup.choices}
        kwargs = {
            "scientific_name": scientific_name,
            "conservation_status": Species.ConservationStatus.UNVERIFIED,
            "is_auto_created": True,
            "taxon_group": (
                taxon_group if taxon_group in valid_groups
                else Species.TaxonGroup.OTHER
            ),
        }
        order = self._ORDER_BY_GROUP.get(taxon_group)
        if order:
            kwargs["order"] = order
        return Species.objects.create(**kwargs)

    def save_model(self, request, obj, form, change):
        # 解析學名：無比對 → 自動建立；比對到/下拉選取 → 沿用；留空 → 未鑑定
        new_name = getattr(form, "new_species_name", None)
        if new_name:
            # 先算相似物種（於建立前，避免把剛建的算進去），事後以警告提示、不阻擋
            similar = SpecimenAdminForm._find_similar_species(new_name)
            sp = self._auto_create_species(new_name, obj.taxon_group)
            obj.species = sp
            self.message_user(
                request,
                f"已自動建立新物種：{sp.scientific_name}，"
                "請盡快補齊保育等級與分類資訊。",
                level=messages.WARNING,
            )
            if similar:
                listed = "、".join(
                    f"「{s.scientific_name}（{s.common_name or '無中文名'}）」"
                    for s in similar
                )
                self.message_user(
                    request,
                    f"注意：新建的「{new_name}」與既有物種相似：{listed}。"
                    "若其實是同一物種，請改連結該既有物種以免重複。",
                    level=messages.WARNING,
                )
        else:
            obj.species = getattr(form, "resolved_species", None)
            if obj.species is not None:
                self.message_user(
                    request,
                    f"已連結至既有物種：{obj.species.scientific_name}。",
                    level=messages.INFO,
                )

        # 判斷 superuser 是否變更了典藏編號（主鍵）。
        # clean 階段起 form.initial 保有載入時的原始主鍵，obj.catalog_number 已是新值。
        original_pk = form.initial.get("catalog_number") if change else None
        new_pk = (obj.catalog_number or "").strip()
        pk_changed = bool(
            change and original_pk and new_pk and original_pk != new_pk
        )

        auto = not obj.catalog_number
        if pk_changed:
            # get_readonly_fields 已保證僅 superuser 能送出新編號；此處僅在後端再次確認。
            if not request.user.is_superuser:
                raise PermissionDenied("僅具備管理員權限者可變更典藏編號。")
            self._relocate_catalog_number(request, obj, original_pk, new_pk)
        else:
            super().save_model(request, obj, form, change)
            if auto:
                self.message_user(
                    request,
                    f"已自動產生典藏編號：{obj.catalog_number}",
                    level=messages.INFO,
                )

        # 警示 A：取得日期年份與入藏年份不一致（不阻擋存檔，僅提醒）
        if (
            obj.acquisition_date
            and obj.acquisition_date.year != obj.accession_year
        ):
            self.message_user(
                request,
                "取得日期年份與入藏年份不一致，請確認是否正確",
                level=messages.WARNING,
            )

        # 警示 B：典藏編號中的年份與入藏年份不符（編號不會自動更新）
        catalog_match = re.match(
            r"^LYM-[A-Z]{2}-(\d{4})-\d{4}$", obj.catalog_number or "",
        )
        if catalog_match and int(catalog_match.group(1)) != obj.accession_year:
            self.message_user(
                request,
                "入藏年份已與典藏編號不符，編號不會自動更新，"
                "如需更正請聯繫系統管理者",
                level=messages.WARNING,
            )

    @transaction.atomic
    def _relocate_catalog_number(self, request, obj, old_pk, new_pk):
        """superuser 變更典藏編號（主鍵）時，於單一交易內安全地搬移整筆資料。

        典藏編號是 Specimen 的主鍵，且 Movement／Identification／SpecimenImage
        以 CASCADE、CatalogNumberChange 以 SET_NULL 外鍵指向它。若直接 obj.save()，
        Django 會以新主鍵找不到既有列而改為 INSERT，留下重複列與孤兒關聯。故改以
        下列步驟在交易內搬移：
          1. 以新編號 INSERT 一列（force_insert，因 obj 為既載入之實例）。
          2. 將所有子表外鍵由舊編號改指向新編號；含既有的 CatalogNumberChange
             稽核列——它是 SET_NULL，若不先搬走，下方刪舊列時會被靜默設為 NULL，
             造成「同一標本改第二次以上」時早期稽核紀錄的 specimen 斷鏈成孤兒。
          3. 子關聯皆已搬離，安全刪除舊編號那一列（此時已無任何 FK 指向它）。
          4. 還原原始 occurrence_uuid（見下）。
          5. 於 CatalogNumberChange 留下「舊編號 → 新編號、修改者、時間」稽核紀錄
             （specimen 指向新列 new_pk）。

        occurrence_uuid 是 unique 且「產生後不得變更」的全球識別碼，須隨標本延續。
        但新舊兩列在刪除舊列前會短暫並存，同一 uuid 會觸發 unique 衝突；故新列先以
        暫時 uuid 插入，待舊列刪除後再還原原始 uuid，識別碼實質不變。

        注意：本功能「不」更動 Cloudinary 上的影像檔名。標本影像雖以典藏編號命名，
        但改名不屬於這支功能的職責；此處僅搬移資料庫關聯，影像檔沿用原檔名不動。
        """
        original_uuid = obj.occurrence_uuid
        obj.catalog_number = new_pk
        # 暫時 uuid：避開與舊列 occurrence_uuid 的 unique 衝突（稍後還原）
        obj.occurrence_uuid = uuid.uuid4()
        # obj 為既載入實例（_state.adding=False），需強制 INSERT 才會以新主鍵建列
        obj.save(force_insert=True)

        # 搬移子表關聯：先改指向新列，舊列才可安全刪除
        Movement.objects.filter(specimen_id=old_pk).update(specimen_id=new_pk)
        Identification.objects.filter(
            specimen_id=old_pk
        ).update(specimen_id=new_pk)
        SpecimenImage.objects.filter(
            specimen_id=old_pk
        ).update(specimen_id=new_pk)
        # CatalogNumberChange 為 SET_NULL：必須在刪舊列前一併搬走，否則早期稽核列
        # 的 specimen 會被刪除連帶靜默設為 NULL。
        CatalogNumberChange.objects.filter(
            specimen_id=old_pk
        ).update(specimen_id=new_pk)

        # 子關聯皆已搬離，刪除舊列不會 CASCADE／SET_NULL 波及任何子資料
        Specimen.objects.filter(pk=old_pk).delete()

        # 舊列已刪，unique 衝突解除 → 還原原始全球唯一識別碼
        Specimen.objects.filter(pk=new_pk).update(occurrence_uuid=original_uuid)
        obj.occurrence_uuid = original_uuid  # 同步記憶體實例，供後續程式使用

        CatalogNumberChange.objects.create(
            specimen=obj,
            old_catalog_number=old_pk,
            new_catalog_number=new_pk,
            changed_by=request.user,
        )
        self.message_user(
            request,
            f"典藏編號已由 {old_pk} 變更為 {new_pk}，"
            "關聯的異動／鑑定／影像資料已一併搬移，並記錄於「典藏編號異動」。",
            level=messages.SUCCESS,
        )

    @admin.action(description="匯出為 Darwin Core CSV（僅公開資料）")
    def export_darwin_core_csv(self, request, queryset):
        """匯出勾選標本中「公開」者為 Darwin Core CSV（預設不含未公開資料）。"""
        return self._darwin_core_response(
            request,
            queryset.filter(publication_status=PublicationStatus.PUBLISHED),
        )

    # 「包含未公開資料」的匯出：以 permissions=["publish"] 控管——無 can_publish_specimen
    # 權限者，此動作根本不出現在下拉選單（等同「勾選框僅對有權限者顯示」）。
    @admin.action(
        description="匯出為 Darwin Core CSV（含未公開資料）",
        permissions=["publish"],
    )
    def export_darwin_core_csv_with_unpublished(self, request, queryset):
        """匯出勾選的全部標本（含草稿／待審）。需具備 can_publish_specimen 權限。"""
        # 後端二次驗證，不可只靠前端隱藏
        if not self.has_publish_permission(request):
            raise PermissionDenied
        return self._darwin_core_response(request, queryset)

    # ── 標籤列印用 CSV（供 GoDEX EZ-6300 Plus ＋ GoLabel 使用）───────────────
    # GoLabel 負責版面與 QR 圖形，這裡只輸出 CSV。不受 publication_status 影響：
    # 貼標籤是整理實體標本的作業，草稿／待審／公開一律可匯出。
    LABEL_CSV_HEADER = [
        "典藏編號", "中文名", "學名",
        "採集日期", "典藏日期", "採集地點", "採集者", "QR內容",
    ]

    @staticmethod
    def _label_clean(value):
        """轉字串並移除換行（CSV／QR 欄位內含換行會讓 GoLabel 讀取出錯）。"""
        if value is None:
            return ""
        return re.sub(r"[\r\n]+", " ", str(value)).strip()

    @staticmethod
    def _label_iso(value):
        """日期→YYYY-MM-DD；空值→空字串。"""
        return value.isoformat() if value else ""

    def _label_row(self, specimen):
        """組出單一標本的標籤欄位（七欄文字＋QR內容）。

        採集資訊優先讀採集事件（collection_event），退回標本上保留的舊採集欄位；
        典藏日期優先取得日期（acquisition_date），退回入藏日期（accession_date）。
        """
        event = specimen.collection_event
        collection_date = (event.collection_date if event else None) or specimen.collection_date
        location = (event.collection_location if event else "") or specimen.collection_location
        collector = (event.collector if event else "") or specimen.collector
        accession_date = specimen.acquisition_date or specimen.accession_date

        catalog = self._label_clean(specimen.catalog_number)
        common = self._label_clean(specimen.species.common_name if specimen.species_id else "")
        scientific = self._label_clean(specimen.species.scientific_name if specimen.species_id else "")
        collection_iso = self._label_iso(collection_date)
        accession_iso = self._label_iso(accession_date)
        location = self._label_clean(location)
        collector = self._label_clean(collector)

        # QR內容：非空項目才串入，空值整項略過（不留空欄、不留連續分隔符）。
        # 兩個日期加「採集／典藏」短前綴以資區別（其餘欄位不加前綴，節省 QR 容量）。
        qr_parts = []
        if catalog:
            qr_parts.append(catalog)
        if common:
            qr_parts.append(common)
        if scientific:
            qr_parts.append(scientific)
        if collection_iso:
            qr_parts.append(f"採集 {collection_iso}")
        if accession_iso:
            qr_parts.append(f"典藏 {accession_iso}")
        if location:
            qr_parts.append(location)
        if collector:
            qr_parts.append(collector)
        qr_content = "｜".join(qr_parts)

        return [
            catalog, common, scientific,
            collection_iso, accession_iso, location, collector, qr_content,
        ]

    def _label_csv_response(self, request, queryset, *, big5):
        queryset = queryset.select_related("species", "collection_event")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(self.LABEL_CSV_HEADER)
        count = 0
        for specimen in queryset:
            writer.writerow(self._label_row(specimen))
            count += 1
        text = buffer.getvalue()

        if big5:
            # 罕用字無法以 Big5 編碼時以「?」取代，不拋例外中斷匯出
            content = text.encode("big5", errors="replace")
            content_type = "text/csv; charset=big5"
            label = "Big5"
        else:
            # UTF-8 with BOM（utf-8-sig 自動加 BOM，供 Excel／GoLabel 判讀）
            content = text.encode("utf-8-sig")
            content_type = "text/csv; charset=utf-8"
            label = "UTF-8 BOM"

        stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
        filename = f"標籤列印_{stamp}.csv"
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = (
            f"attachment; filename=labels_{stamp}.csv; "
            f"filename*=UTF-8''{quote(filename)}"
        )
        self.message_user(
            request,
            f"已匯出 {count} 筆標籤列印 CSV（{label}）。",
            level=messages.INFO,
        )
        return response

    @admin.action(description="匯出標籤列印用 CSV（UTF-8）", permissions=["change"])
    def export_label_csv_utf8(self, request, queryset):
        """標籤列印 CSV（UTF-8 with BOM）；不受公開狀態影響，草稿也可匯出。"""
        return self._label_csv_response(request, queryset, big5=False)

    @admin.action(description="匯出標籤列印用 CSV（Big5）", permissions=["change"])
    def export_label_csv_big5(self, request, queryset):
        """標籤列印 CSV（Big5，罕用字以 ? 取代）；不受公開狀態影響，草稿也可匯出。"""
        return self._label_csv_response(request, queryset, big5=True)

    def _darwin_core_response(self, request, queryset):
        """把 queryset 的標本組成 Darwin Core CSV（ZIP，附授權說明）並回傳下載回應。

        資料組裝抽到 collection.darwin_core.build_darwin_core_zip，與側邊欄的
        「Darwin Core 匯出」共用同一份欄位對應（單一事實來源）。
        """
        count = queryset.count()
        response = HttpResponse(
            build_darwin_core_zip(request, queryset),
            content_type="application/zip",
        )
        response["Content-Disposition"] = (
            'attachment; filename="specimens_darwincore.zip"'
        )
        self.message_user(
            request,
            f"已匯出 {count} 筆標本為 Darwin Core CSV"
            "（ZIP 內含授權與來源說明.txt）。",
            level=messages.INFO,
        )
        return response

    @admin.display(description="basisOfRecord")
    def basis_of_record(self, obj):
        return obj.BASIS_OF_RECORD

    @admin.display(description="危害標記")
    def hazard_flag(self, obj):
        labels = obj.hazard_labels()
        if labels:
            # 有危害：填色警示徽章，讓列表一眼可辨
            return format_html(
                '<span style="display:inline-block;background:#b3261e;color:#fff;'
                'font-weight:700;padding:2px 9px;border-radius:6px;white-space:nowrap;">'
                '⚠ {}</span>',
                "、".join(labels),
            )
        return format_html('<span style="color:#9a9a9a;">—</span>')

    @admin.display(description="影像預覽")
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-height:200px;" />', obj.image.url,
            )
        return "（無影像）"


@admin.register(Observation)
class ObservationAdmin(PublicationAdminMixin, ModelAdmin):
    publish_permission = "collection.can_publish_observation"
    list_display = (
        "record_number", "species", "data_source", "observer",
        "observation_date", "observation_location", "count",
        "publication_badge",
    )
    list_filter = ("publication_status", "data_source", "species__taxon_group")
    actions = ["make_published", "make_review", "make_draft"]
    search_fields = (
        "record_number", "species__scientific_name",
        "species__common_name", "observer", "observation_location",
        "source_reference",
    )
    date_hierarchy = "observation_date"
    autocomplete_fields = ("species",)
    readonly_fields = ("basis_of_record",)
    inlines = (ObservationImageInline,)
    # 清單頁一次帶出物種，避免逐列查詢（N+1）
    list_select_related = ("species",)

    fieldsets = (
        ("公開設定", {
            "fields": ("publication_status",),
            "description": (
                "僅「公開」狀態的觀察紀錄會出現在對外頁面與公開統計；"
                "設為公開需具備公開權限。"
            ),
        }),
        ("基本資訊", {
            "fields": (
                "record_number", "species", "data_source",
                "source_reference", "basis_of_record",
            ),
        }),
        ("觀察資訊", {
            "fields": (
                "observer", "observation_date", "observation_location",
                ("latitude", "longitude"), "count",
            ),
        }),
    )

    @admin.display(description="basisOfRecord")
    def basis_of_record(self, obj):
        return obj.BASIS_OF_RECORD


class SpecimenCollectionInline(TabularInline):
    """在採集事件頁面內嵌批次新增／檢視該次採集的標本。

    僅收標本專屬欄位（採集資訊由母層事件共用、不重複輸入）；學名採下拉
    autocomplete（此處無 SpecimenAdminForm 的自由輸入自動建物種），細部
    可再到標本頁編修。館藏編號留空會自動產生。
    """

    model = Specimen
    fk_name = "collection_event"
    extra = 1
    fields = (
        "catalog_number", "accession_year", "taxon_group", "species",
        "specimen_type", "identification_status",
    )
    autocomplete_fields = ("species",)
    show_change_link = True


@admin.register(CollectionEvent)
class CollectionEventAdmin(ModelAdmin):
    """採集事件：先建立一次採集事件，再以下方 inline 快速新增多件標本。"""

    list_display = (
        "__str__", "collection_date", "collection_location",
        "collector", "sampling_protocol", "specimen_count",
    )
    list_filter = ("sampling_protocol",)
    # 供 Specimen.collection_event 的 autocomplete 使用
    search_fields = ("collection_location", "collector", "habitat")
    readonly_fields = ("event_uuid",)
    inlines = (SpecimenCollectionInline,)

    fieldsets = (
        ("採集事件", {
            "fields": (
                "event_uuid",
                "collection_date", "collection_location",
                ("latitude", "longitude"),
                "collector", "habitat", "sampling_protocol",
            ),
            "description": (
                "精確經緯度僅供內部與 Darwin Core 匯出使用，公開頁一律只顯示到"
                "縣市層級、且保育類不輸出精確座標。"
            ),
        }),
    )

    @admin.display(description="標本數")
    def specimen_count(self, obj):
        return obj.specimens.count()


@admin.register(CatalogNumberChange)
class CatalogNumberChangeAdmin(ModelAdmin):
    """典藏編號異動：唯讀稽核紀錄，僅供查閱，一律由系統於變更編號時自動寫入。"""

    list_display = (
        "old_catalog_number", "new_catalog_number", "changed_by", "changed_at",
    )
    search_fields = ("old_catalog_number", "new_catalog_number")
    list_filter = ("changed_at",)
    readonly_fields = (
        "specimen", "old_catalog_number", "new_catalog_number",
        "changed_by", "changed_at",
    )

    def has_add_permission(self, request):
        # 僅能由系統自動寫入，禁止人工新增
        return False

    def has_change_permission(self, request, obj=None):
        # 稽核紀錄不可竄改
        return False


# ---------------------------------------------------------------------------
# 修正 django-unfold 與 Django 內建 User 表單的樣式衝突：
# 預設 UserAdmin 的密碼欄位在 unfold 下不會正確渲染，改用 unfold 提供的
# 相容表單重新註冊 User／Group，讓「新增／修改使用者」的密碼框正常顯示。
# ---------------------------------------------------------------------------
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass
