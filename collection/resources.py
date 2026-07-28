"""標本批次匯入 — django-import-export 的 Resource 與轉換邏輯。

一列範本 = 一件標本，一次最多建立三張表：
    Species（以「中文名」去重的 get-or-create）
    CollectionEvent（每列新建一筆；三項採集資訊皆空則不建）
    Specimen（每列新建一筆；典藏編號留空由 save() 自動產生）

設計重點：
- 全程包在交易內（Meta.use_transactions=True）。dry-run 會建立資料再整批 rollback，
  因此預覽階段就能算出正確的「將新增 X 件標本、Y 筆新物種、Z 筆採集事件」。
  正式匯入時任一列出錯 → result.has_errors() → import-export 整批 rollback。
- 逐列 instance.save()（use_bulk=False），讓 Specimen.save() 逐筆產生流水號，
  避免 bulk_create 造成典藏編號衝突。
- 同一次匯入中，多列相同中文名只建一筆物種（self._species_cache 快取）。
- 欄位級錯誤以 ValidationError({欄位: 訊息}) 拋出，import-export 會在預覽頁
  標明「第幾列、哪個欄位、什麼錯」，且不會寫入。
"""

from datetime import date, datetime

from django.core.exceptions import ValidationError
from import_export import fields, resources

from .models import (
    CollectionEvent, PublicationStatus, Species, Specimen, current_year,
)

# 學名未填時的佔位前綴（配合 Species.scientific_name 唯一性）
PLACEHOLDER_PREFIX = "[待查證] "

# 範本「保育等級」中文標籤 → Species.ConservationStatus 值（僅在新建物種時使用）
CONSERVATION_MAP = {
    "一般類": Species.ConservationStatus.GENERAL,
    "其他應予保育": Species.ConservationStatus.OTHER,
    "珍貴稀有": Species.ConservationStatus.RARE,
    "瀕臨絕種": Species.ConservationStatus.ENDANGERED,
    "待查證": Species.ConservationStatus.UNVERIFIED,
}

# 範本「生命階段」中文標籤 → Specimen.LifeStage 值（含口語別名；空白→不填 None）
LIFE_STAGE_MAP = {
    "成體": Specimen.LifeStage.ADULT,
    "成鳥": Specimen.LifeStage.ADULT,
    "亞成體": Specimen.LifeStage.SUBADULT,
    "亞成": Specimen.LifeStage.SUBADULT,
    "幼體": Specimen.LifeStage.JUVENILE,
    "幼鳥": Specimen.LifeStage.JUVENILE,
    "卵": Specimen.LifeStage.EGG,
    "幼蟲": Specimen.LifeStage.LARVA,
    "若蟲": Specimen.LifeStage.NYMPH,
    "蛹": Specimen.LifeStage.PUPA,
}

# 範本欄位標題（Sheet1「標本資料」標題列）
COL_COMMON = "中文名"
COL_SCIENTIFIC = "學名"
COL_LIFE_STAGE = "生命階段"
COL_CONSERVATION = "保育等級"
COL_DATE = "採集日期"
COL_LOCATION = "採集地點"
COL_COLLECTOR = "採集者"
COL_REMARKS = "備註"
COL_PHOTO = "照片檔名"
COL_CONDITION = "標本狀況"  # 選填欄；有填則彙整進備註

TEMPLATE_HEADERS = [
    COL_COMMON, COL_SCIENTIFIC, COL_LIFE_STAGE, COL_CONSERVATION,
    COL_DATE, COL_LOCATION, COL_COLLECTOR, COL_REMARKS, COL_PHOTO,
]


def _text(row, key):
    """讀取儲存格並轉為去頭尾空白的字串；空值回空字串。"""
    value = row.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


class SpecimenImportResource(resources.ModelResource):
    """標本批次匯入資源。實際的欄位轉換全部在 import_instance() 內完成；
    宣告的欄位皆為 readonly，僅供 dry-run 預覽表格顯示（不參與匯入賦值）。"""

    col_catalog = fields.Field(
        column_name="典藏編號", attribute="catalog_number", readonly=True,
    )
    col_species = fields.Field(
        column_name="物種", readonly=True, dehydrate_method="dehy_species",
    )
    col_taxon = fields.Field(
        column_name="類群", readonly=True, dehydrate_method="dehy_taxon",
    )
    col_ident = fields.Field(
        column_name="鑑定狀態", readonly=True, dehydrate_method="dehy_ident",
    )
    col_life = fields.Field(
        column_name="生命階段", readonly=True, dehydrate_method="dehy_life",
    )
    col_event = fields.Field(
        column_name="採集事件", readonly=True, dehydrate_method="dehy_event",
    )
    col_remarks = fields.Field(
        column_name="備註", attribute="remarks", readonly=True,
    )

    class Meta:
        model = Specimen
        # 每列一律視為新增，不做任何既有資料的比對查詢
        force_init_instance = True
        import_id_fields = ()
        # 只保留上方宣告的預覽欄位；不自動帶入 model 全部欄位
        fields = (
            "col_catalog", "col_species", "col_taxon", "col_ident",
            "col_life", "col_event", "col_remarks",
        )
        # 逐列 save()（見類別說明），且全程交易化以支援 dry-run rollback
        use_bulk = False
        use_transactions = True
        # 由本模組自行驗證欄位；不呼叫 full_clean，避免 specimen_type="" 等被擋
        clean_model_instances = False
        skip_unchanged = False

    # ── 預覽欄位的顯示值（dehydrate 於 dry-run／實際匯入皆會被呼叫）─────────
    def dehy_species(self, obj):
        if not obj.species_id:
            return "—"
        tag = "（＋新物種）" if getattr(obj, "_import_species_created", False) else ""
        return f"{obj.species.common_name or obj.species.scientific_name}{tag}"

    def dehy_taxon(self, obj):
        return obj.get_taxon_group_display()

    def dehy_ident(self, obj):
        return obj.get_identification_status_display()

    def dehy_life(self, obj):
        return obj.get_life_stage_display() if obj.life_stage else "—"

    def dehy_event(self, obj):
        return str(obj.collection_event) if obj.collection_event_id else "—"

    # ── 匯入生命週期 ─────────────────────────────────────────────────────
    def before_import(self, dataset, **kwargs):
        super().before_import(dataset, **kwargs)
        # 每次匯入（每個 request）重置：同名物種去重快取與計數器
        self._species_cache = {}
        self.new_species_count = 0
        self.new_events_count = 0
        self.new_specimen_count = 0

    def after_import_row(self, row, row_result, **kwargs):
        super().after_import_row(row, row_result, **kwargs)
        if row_result.import_type == row_result.IMPORT_TYPE_NEW:
            self.new_specimen_count += 1

    def after_import(self, dataset, result, **kwargs):
        super().after_import(dataset, result, **kwargs)
        # 把彙總數字掛到 result 上，供預覽模板與成功訊息讀取
        result.lanyang_new_specimens = self.new_specimen_count
        result.lanyang_new_species = self.new_species_count
        result.lanyang_new_events = self.new_events_count

    def import_instance(self, instance, row, **kwargs):
        """把一列範本轉成一件 Specimen（並視需要建立物種／採集事件）。

        不呼叫 super()：本資源不做以欄位為基礎的自動賦值，全部由此處掌控。
        任何欄位級錯誤在建立關聯資料「之前」先以 ValidationError 拋出。
        """
        # 1) 先驗證欄位（在建立任何關聯資料之前）
        common_name = _text(row, COL_COMMON)
        if not common_name:
            raise ValidationError(
                {COL_COMMON: "中文名必填，請填寫物種的中文名。"}
            )
        collection_date = self._parse_date(row.get(COL_DATE))
        life_stage = self._map_life_stage(_text(row, COL_LIFE_STAGE))

        # 2) 物種（get-or-create，以中文名去重）；可能因保育等級標籤無效而拋錯
        species, to_species, created = self._resolve_species(
            common_name,
            _text(row, COL_SCIENTIFIC),
            _text(row, COL_CONSERVATION),
        )

        # 3) 採集事件（三項採集資訊皆空則不建）
        event = self._build_event(row, collection_date)

        # 4) 組出標本（典藏編號留空 → save() 自動產生）
        instance.taxon_group = Specimen.TaxonGroup.BIRD
        instance.accession_year = current_year()  # 入藏年份＝當年（目前為 2026）
        instance.species = species
        if to_species:
            instance.identification_status = (
                Specimen.IdentificationStatus.TO_SPECIES
            )
        # 佔位／新建物種：維持預設「未鑑定」
        instance.collection_event = event
        if life_stage:
            instance.life_stage = life_stage
        instance.preparation_status = Specimen.PreparationStatus.FROZEN_PENDING
        instance.preservation_method = Specimen.PreservationMethod.FROZEN
        instance.specimen_type = ""  # 匯入不經表單驗證，日後編輯再補選
        instance.remarks = self._build_remarks(row)
        instance.publication_status = PublicationStatus.DRAFT
        # 供預覽欄位標示此列是否順帶建立了新物種
        instance._import_species_created = created

    # ── 內部工具 ─────────────────────────────────────────────────────────
    def _resolve_species(self, common_name, scientific_raw, conservation_raw):
        """回傳 (species, to_species, created)。

        - to_species：對到「既有且有真實學名」的物種 → True（標本鑑定狀態設已鑑定至種）。
        - created：本列是否新建了一筆物種（用於計數與預覽標示）。
        同一次匯入中，多列相同中文名只建一筆（透過 self._species_cache）。
        既有物種一律不覆寫其學名與保育等級。
        """
        if common_name in self._species_cache:
            species, to_species = self._species_cache[common_name]
            return species, to_species, False

        existing = (
            Species.objects.filter(common_name=common_name)
            .order_by("pk")
            .first()
        )
        if existing:
            to_species = self._has_real_name(existing)
            self._species_cache[common_name] = (existing, to_species)
            return existing, to_species, False

        # 找不到 → 新建。學名有填就用，沒填則產生唯一佔位。
        scientific_name = scientific_raw or f"{PLACEHOLDER_PREFIX}{common_name}"
        conservation = self._map_conservation(conservation_raw)
        species, created = Species.objects.get_or_create(
            scientific_name=scientific_name,
            defaults={
                "common_name": common_name,
                "taxon_group": Species.TaxonGroup.BIRD,
                "conservation_status": conservation,
                "is_auto_created": True,
                "publication_status": PublicationStatus.DRAFT,
            },
        )
        if created:
            self.new_species_count += 1
            to_species = False  # 新建物種一律維持未鑑定
        else:
            # 佔位學名已存在 → 沿用該筆（既有），依其學名是否真實決定鑑定狀態
            to_species = self._has_real_name(species)
        self._species_cache[common_name] = (species, to_species)
        return species, to_species, created

    @staticmethod
    def _has_real_name(species):
        """物種是否具備「真實學名」（非佔位、非空）。"""
        return bool(species.scientific_name) and not (
            species.scientific_name.startswith(PLACEHOLDER_PREFIX)
        )

    def _build_event(self, row, collection_date):
        """建立採集事件；三項採集資訊（地點／日期／採集者）皆空則回 None。"""
        location = _text(row, COL_LOCATION)
        collector = _text(row, COL_COLLECTOR)
        if not (location or collector or collection_date):
            return None
        event = CollectionEvent.objects.create(
            collection_location=location,
            collection_date=collection_date,
            collector=collector,
        )
        self.new_events_count += 1
        return event

    @staticmethod
    def _build_remarks(row):
        """把沒有專屬欄位的資訊（備註／照片檔名／標本狀況）彙整成多行文字。"""
        parts = []
        notes = _text(row, COL_REMARKS)
        photo = _text(row, COL_PHOTO)
        condition = _text(row, COL_CONDITION)
        if notes:
            parts.append(notes)
        if photo:
            parts.append(f"照片檔名：{photo}")
        if condition:
            parts.append(f"標本狀況：{condition}")
        return "\n".join(parts)

    @staticmethod
    def _map_conservation(label):
        """保育等級標籤 → enum 值。空白→待查證；無法辨識→拋欄位錯誤。"""
        if not label:
            return Species.ConservationStatus.UNVERIFIED
        try:
            return CONSERVATION_MAP[label]
        except KeyError:
            raise ValidationError({
                COL_CONSERVATION: (
                    f"無法辨識的保育等級「{label}」。可填：一般類／其他應予保育／"
                    "珍貴稀有／瀕臨絕種／待查證（或留空＝待查證）。"
                )
            })

    @staticmethod
    def _map_life_stage(label):
        """生命階段標籤 → enum 值。空白→None；無法辨識→拋欄位錯誤。"""
        if not label:
            return None
        try:
            return LIFE_STAGE_MAP[label]
        except KeyError:
            raise ValidationError({
                COL_LIFE_STAGE: (
                    f"無法辨識的生命階段「{label}」。可填：成體（成鳥）／亞成體（亞成）／"
                    "幼體（幼鳥）／卵／幼蟲／若蟲／蛹（或留空＝不填）。"
                )
            })

    @staticmethod
    def _parse_date(value):
        """解析採集日期。空白→None；YYYY-MM-DD（或 YYYY/MM/DD）→date；無法解析→拋欄位錯誤。"""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValidationError({
            COL_DATE: f"採集日期「{text}」格式錯誤，請用 YYYY-MM-DD（例：2026-05-01），或留空。"
        })
