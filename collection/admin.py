"""蘭陽博物館 飛行動物典藏與紀錄系統 — Admin 設定

三個模型皆可於 Admin 新增／編輯／查詢／篩選；
在 Species 頁面以 inline 直接檢視其底下的標本與觀察紀錄。
"""

import csv

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

from .models import (
    CATALOG_NUMBER_RE, Identification, Movement, Observation,
    ObservationImage, Species, SpeciesImage, Specimen, SpecimenImage,
)


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
    """標本表單：加入『可能重複』的二次確認防呆。"""

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

    class Meta:
        model = Specimen
        fields = "__all__"
        # 中文說明文字（設在表單層，不更動資料模型）
        help_texts = {
            "catalog_number": (
                "格式：LYM-類群代碼-年份-4位流水號（例：LYM-AV-2026-0001、LYM-IN-2026-0023）。"
                "類群代碼：鳥=AV、昆蟲=IN、蝙蝠飛鼠=MA。"
                "選好物種後系統會自動建議下一個可用編號，可自行修改；留空存檔亦會自動產生。"
            ),
            "species": "此標本所屬物種（學名）。可輸入關鍵字搜尋選取。",
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
        species = cleaned.get("species")
        location = cleaned.get("collection_location")
        date = cleaned.get("collection_date")
        confirmed = cleaned.get("confirm_duplicate")

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
    extra = 0
    fields = ("catalog_number", "specimen_type", "collection_date")
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
    list_filter = ("taxon_group", "conservation_status")
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
            "fields": ("introduction",),
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
        # 選好物種後自動建議典藏編號
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
        """回傳指定物種的下一個可用典藏編號（供表單自動建議）。"""
        try:
            species = Species.objects.get(pk=request.GET.get("species"))
        except (Species.DoesNotExist, ValueError, TypeError):
            return JsonResponse({"catalog_number": ""})
        return JsonResponse({
            "catalog_number": Specimen.next_catalog_number(species.taxon_group),
        })
    list_display = (
        "catalog_number", "species", "specimen_type", "status",
        "hazard_flag",
    )
    list_filter = (
        "species__taxon_group", "status", "preparation_status", "cause_of_death",
        AccessionYearFilter, HazardFilter, CompletenessFilter,
    )
    search_fields = (
        "catalog_number", "species__scientific_name",
        "species__common_name", "collector", "collection_location",
    )
    date_hierarchy = "accession_date"
    autocomplete_fields = ("species",)

    fieldsets = (
        ("基本資料", {
            "fields": (
                "catalog_number", "species", "specimen_type",
                "basis_of_record",
            ),
            "description": (
                "標示為必填（<b>物種</b>、<b>標本類型</b>）的欄位不可空白，"
                "表單中以粗體標示。典藏編號留空會自動產生。"
            ),
        }),
        ("標本製作與來源", {
            "fields": (
                "preparation_status", "preservation_method",
                "cause_of_death", "cause_of_death_note",
                "acquisition_type", "acquisition_date",
                "preparer", "preparation_date", "storage_location",
            ),
            "description": (
                "支援冰箱中尚未製作的冷凍標本先行建檔；"
                "冷凍待處理者請於「製作狀態」標示。"
            ),
        }),
        ("採集資訊", {
            "fields": (
                "collector", "collection_date", "collection_location",
                ("latitude", "longitude"),
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
        readonly = ["basis_of_record"]
        if obj is not None:
            # 編輯既有標本時鎖定典藏編號（主鍵），避免改動造成關聯錯亂
            readonly.append("catalog_number")
        return readonly

    def save_model(self, request, obj, form, change):
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
        queryset = queryset.select_related("species")

        def dwc_date(value):
            # Darwin Core eventDate 用 ISO 8601（YYYY-MM-DD）
            return value.isoformat() if value else ""

        # (DwC 欄名, 取值函式)
        columns = [
            ("occurrenceID", lambda s: s.catalog_number),
            ("basisOfRecord", lambda s: s.BASIS_OF_RECORD),
            # DwC preparations：標本的製作／保存方式（對應 preservation_method）
            ("preparations", lambda s: s.get_preservation_method_display() if s.preservation_method else ""),
            ("scientificName", lambda s: s.species.scientific_name),
            ("vernacularName", lambda s: s.species.common_name),
            ("order", lambda s: s.species.order),
            ("family", lambda s: s.species.family),
            ("taxonID", lambda s: s.species.taicol_taxon_id),
            ("recordedBy", lambda s: s.collector),
            ("eventDate", lambda s: dwc_date(s.collection_date)),
            ("decimalLatitude", lambda s: s.latitude if s.latitude is not None else ""),
            ("decimalLongitude", lambda s: s.longitude if s.longitude is not None else ""),
            ("locality", lambda s: s.collection_location),
            ("identifiedBy", lambda s: s.identified_by),
            ("dateIdentified", lambda s: dwc_date(s.identified_date)),
            ("institutionCode", lambda s: "LYM"),
        ]

        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = (
            'attachment; filename="specimens_darwincore.csv"'
        )
        # charset=utf-8-sig 已自動加 BOM，讓 Excel 正確判讀 UTF-8 中文
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
