"""蘭陽博物館 飛行動物典藏與紀錄系統 — Admin 設定

三個模型皆可於 Admin 新增／編輯／查詢／篩選；
在 Species 頁面以 inline 直接檢視其底下的標本與觀察紀錄。
"""

import csv
import difflib
import json
import re

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.urls import path
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.forms import (
    AdminPasswordChangeForm,
    UserChangeForm,
    UserCreationForm,
)
# unfold 的輸入框樣式類別；套到宣告式表單欄位（非 model 欄位不會被 unfold 自動套用）
from unfold.widgets import INPUT_CLASSES

from .models import (
    CATALOG_NUMBER_RE, CollectionEvent, Identification, Movement, Observation,
    ObservationImage, Species, SpeciesImage, Specimen, SpecimenImage,
)


# ── Darwin Core 匯出專用的「值」對照 ──────────────────────────────
# 僅在 CSV 匯出這一層把中文選項轉成 DwC 控制詞彙；資料庫、Admin 介面、
# 公開頁面一律維持 choices 的中文標籤不變。鍵為 Specimen 的 choices 值
# （即中文標籤所對應的內部值），未列入者（含「不明」）一律輸出空字串。
DWC_SEX = {
    Specimen.Sex.MALE: "male",      # 雄
    Specimen.Sex.FEMALE: "female",  # 雌
    # 不明／未鑑別、未填 → 由 .get(..., "") 產生空字串
}
DWC_LIFE_STAGE = {
    Specimen.LifeStage.EGG: "egg",            # 卵
    Specimen.LifeStage.LARVA: "larva",        # 幼蟲
    Specimen.LifeStage.NYMPH: "nymph",        # 若蟲
    Specimen.LifeStage.PUPA: "pupa",          # 蛹
    Specimen.LifeStage.JUVENILE: "juvenile",  # 幼體
    Specimen.LifeStage.SUBADULT: "subadult",  # 亞成體
    Specimen.LifeStage.ADULT: "adult",        # 成體
    # 不明、未填 → 由 .get(..., "") 產生空字串
}


# 登入頁改用「繼承自 unfold 登入頁」的模板（僅在 login_after 附加密碼顯示切換，
# 不整份複製，避免日後 unfold 更新失效）。
admin.site.login_template = "admin/login_toggle.html"


def _county_of(text):
    """從自由文字地點擷取到「縣／市」為止的縣市層級字串（供 DwC stateProvince）。"""
    if not text:
        return ""
    m = re.search(r"^.*?[縣市]", text)
    return m.group(0) if m else ""


class AccessionYearFilter(admin.SimpleListFilter):
    """依入藏年份篩選標本。"""

    title = "入藏年份"
    parameter_name = "accession_year"

    def lookups(self, request, model_admin):
        years = (
            Specimen.objects
            .exclude(accession_date__isnull=True)
            .dates("accession_date", "year", order="DESC")
        )
        return [(d.year, f"{d.year} 年") for d in years]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(accession_date__year=self.value())
        return queryset


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
        # 留空 → 存檔時自動產生；有填才驗證格式
        if value and not CATALOG_NUMBER_RE.match(value):
            raise forms.ValidationError(
                "典藏編號格式不符。正確格式為 LYM-類群代碼-年份-4位流水號，"
                "例如：LYM-AV-2026-0001（類群代碼：鳥=AV、昆蟲=IN、蝙蝠飛鼠=MA）。"
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
        }


@admin.register(Species)
class SpeciesAdmin(ModelAdmin):
    form = SpeciesAdminForm
    list_display = (
        "common_name", "scientific_name_italic", "taxon_group",
        "conservation_status",
    )
    list_filter = ("taxon_group", "conservation_status", "is_auto_created")
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

    @admin.display(description="學名", ordering="scientific_name")
    def scientific_name_italic(self, obj):
        # 列表頁學名：襯線字體 + 斜體（拉丁學名慣例，僅套用於顯示）
        return format_html(
            '<span style="font-family:Georgia,\'Times New Roman\',serif;'
            'font-style:italic;">{}</span>',
            obj.scientific_name,
        )

    fieldsets = (
        ("基本分類", {
            "fields": (
                "common_name", "scientific_name", "taicol_taxon_id",
                "taxon_group", "order", "family", "genus",
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
class SpecimenAdmin(ModelAdmin):
    form = SpecimenAdminForm
    inlines = (IdentificationInline, MovementInline, SpecimenImageInline)
    actions = ["export_darwin_core_csv"]
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
        return JsonResponse({
            "catalog_number": Specimen.next_catalog_number(taxon_group),
        })
    list_display = (
        "catalog_number", "species_or_group", "specimen_type", "status",
        "identification_status", "hazard_flag",
    )
    list_filter = (
        "taxon_group", "identification_status", "status",
        "preparation_status", "cause_of_death",
        AccessionYearFilter, HazardFilter, CompletenessFilter,
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
    date_hierarchy = "accession_date"
    # 採集事件下拉搜尋 + 右側「＋新增採集事件」彈窗
    autocomplete_fields = ("collection_event",)

    fieldsets = (
        ("基本資料", {
            "fields": (
                "catalog_number", "occurrence_uuid",
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
        # occurrence_uuid 一律唯讀（自動產生、不得變更或清空）
        readonly = ["basis_of_record", "occurrence_uuid"]
        if obj is not None:
            # 編輯既有標本時鎖定典藏編號（主鍵），避免改動造成關聯錯亂
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

        auto = not obj.catalog_number
        super().save_model(request, obj, form, change)
        if auto:
            self.message_user(
                request,
                f"已自動產生典藏編號：{obj.catalog_number}",
                level=messages.INFO,
            )

    @admin.action(description="匯出為 Darwin Core CSV")
    def export_darwin_core_csv(self, request, queryset):
        """把勾選的標本匯出成 Darwin Core 欄位的 CSV。"""
        queryset = queryset.select_related("species", "collection_event")

        def dwc_date(value):
            # Darwin Core eventDate 用 ISO 8601（YYYY-MM-DD）
            return value.isoformat() if value else ""

        # 類群 → (class, order)：供 species 為空（未鑑定）時仍輸出較高分類階層
        DWC_HIGHER = {
            "bird": ("Aves", ""),
            "insect": ("Insecta", ""),
            "bat": ("Mammalia", "Chiroptera"),
            "flying_squirrel": ("Mammalia", "Rodentia"),
            "other": ("", ""),
        }
        # 鑑定狀態 → DwC taxonRank（僅可對應者）
        RANK = {"to_family": "family", "to_genus": "genus", "to_species": "species"}

        def sp_attr(s, attr):
            # 未鑑定（無 species）時回傳空字串，不存取 None
            return getattr(s.species, attr) if s.species_id else ""

        def dwc_order(s):
            # 有物種且已填「目」→ 用之；否則由類群推導（蝙蝠/飛鼠）
            if s.species_id and s.species.order:
                return s.species.order
            return DWC_HIGHER.get(s.taxon_group, ("", ""))[1]

        WITHHELD_NOTE = "保育類物種位置資訊，依館方保育政策保留"

        def is_withheld(s):
            # 安全連鎖：species 未鑑定（None），或保育等級非「一般類」
            # （含待查證／空白）→ 一律保守，精確經緯度留空。
            if not s.species_id:
                return True
            return s.species.conservation_status != Species.ConservationStatus.GENERAL

        def ev(s, attr):
            # 採集資訊一律讀採集事件；無事件或值為 None 則回空字串。
            # 不讀 Specimen 上保留的舊採集欄位，避免兩份資料不一致。
            ce = s.collection_event
            if ce is None:
                return ""
            val = getattr(ce, attr)
            return "" if val is None else val

        def ev_coord(s, attr):
            # 精確經緯度：保育類/待查證/未鑑定一律留空；其餘讀採集事件。
            if is_withheld(s):
                return ""
            ce = s.collection_event
            if ce is None or getattr(ce, attr) is None:
                return ""
            return getattr(ce, attr)

        def dwc_dynamic_properties(s):
            # 無對應 DwC 標準欄位的來源單位／許可文號，改放 dynamicProperties
            # （JSON 字串，中文不轉義）。兩者皆未填時輸出空字串，不輸出空的 {}。
            props = {}
            if s.source_institution:
                props["sourceInstitution"] = s.source_institution
            if s.permit_number:
                props["permitNumber"] = s.permit_number
            return json.dumps(props, ensure_ascii=False) if props else ""

        # (DwC 欄名, 取值函式)
        columns = [
            # occurrenceID 改用全球唯一 UUID（urn:uuid: 形式）；典藏編號改放 catalogNumber
            ("occurrenceID", lambda s: f"urn:uuid:{s.occurrence_uuid}"),
            ("catalogNumber", lambda s: s.catalog_number),
            ("basisOfRecord", lambda s: s.BASIS_OF_RECORD),
            # eventID：比照 occurrenceID 以 urn:uuid: 輸出採集事件 UUID（無事件則空）
            ("eventID", lambda s: f"urn:uuid:{s.collection_event.event_uuid}" if s.collection_event_id else ""),
            # DwC preparations：標本的製作／保存方式（對應 preservation_method）
            ("preparations", lambda s: s.get_preservation_method_display() if s.preservation_method else ""),
            # 較高分類：未鑑定時仍可由類群提供 kingdom/class（符合 DwC，可於 GBIF 高階匹配）
            ("kingdom", lambda s: "Animalia"),
            ("class", lambda s: DWC_HIGHER.get(s.taxon_group, ("", ""))[0]),
            ("order", dwc_order),
            ("family", lambda s: sp_attr(s, "family")),
            ("scientificName", lambda s: sp_attr(s, "scientific_name")),
            # 學名命名者（Species.scientific_name_authorship，可為 None → 空字串）
            ("scientificNameAuthorship", lambda s: sp_attr(s, "scientific_name_authorship") or ""),
            ("vernacularName", lambda s: sp_attr(s, "common_name")),
            ("taxonID", lambda s: sp_attr(s, "taicol_taxon_id")),
            ("taxonRank", lambda s: RANK.get(s.identification_status, "")),
            ("identificationRemarks", lambda s: s.get_identification_status_display()),
            # 個體層級（Darwin Core Occurrence）：性別／年齡階段／個體數。
            # sex／lifeStage 經模組層級對照字典轉為 DwC 控制詞彙；
            # 「不明」與未填因不在字典中，.get 預設回空字串（符合 DwC 慣例）。
            ("sex", lambda s: DWC_SEX.get(s.sex, "")),
            ("lifeStage", lambda s: DWC_LIFE_STAGE.get(s.life_stage, "")),
            ("individualCount", lambda s: s.individual_count if s.individual_count is not None else ""),
            # 以下採集欄位一律讀採集事件（collection_event），不讀 Specimen 舊欄位
            ("recordedBy", lambda s: ev(s, "collector")),
            ("eventDate", lambda s: dwc_date(ev(s, "collection_date") or None)),
            ("samplingProtocol", lambda s: s.collection_event.get_sampling_protocol_display() if (s.collection_event_id and s.collection_event.sampling_protocol) else ""),
            ("habitat", lambda s: ev(s, "habitat")),
            # 保育類／待查證／未鑑定：精確經緯度留空，並以 informationWithheld 說明原因；
            # 仍輸出行政區層級地點（stateProvince 縣市、locality 縣市＋鄉鎮），保留地理價值。
            ("decimalLatitude", lambda s: ev_coord(s, "latitude")),
            ("decimalLongitude", lambda s: ev_coord(s, "longitude")),
            ("informationWithheld", lambda s: WITHHELD_NOTE if is_withheld(s) else ""),
            ("stateProvince", lambda s: _county_of(ev(s, "collection_location"))),
            ("locality", lambda s: ev(s, "collection_location")),
            ("identifiedBy", lambda s: s.identified_by),
            ("dateIdentified", lambda s: dwc_date(s.identified_date)),
            ("institutionCode", lambda s: "LYM"),
            # 來源單位／許可文號無對應 DwC 標準欄位，統一放 dynamicProperties
            ("dynamicProperties", dwc_dynamic_properties),
        ]

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            'attachment; filename="specimens_darwincore.csv"'
        )
        # 於檔首寫入單一 BOM 供 Excel 正確判讀 UTF-8 中文。
        # 不可用 charset=utf-8-sig：那會使每次 writerow 各自加一個 BOM，
        # 污染每列第一欄（例如 occurrenceID 會變成 BOM+urn:uuid:…）。
        response.write("﻿")
        writer = csv.writer(response)
        writer.writerow([name for name, _ in columns])
        for specimen in queryset:
            writer.writerow([getter(specimen) for _, getter in columns])

        self.message_user(
            request,
            f"已匯出 {queryset.count()} 筆標本為 Darwin Core CSV。",
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
class ObservationAdmin(ModelAdmin):
    list_display = (
        "record_number", "species", "data_source", "observer",
        "observation_date", "observation_location", "count",
    )
    list_filter = ("data_source", "species__taxon_group")
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
        "catalog_number", "taxon_group", "species",
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
