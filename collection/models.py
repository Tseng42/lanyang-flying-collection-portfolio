"""蘭陽博物館 飛行動物典藏與紀錄系統 — 資料模型

三張表以「物種表(Species)」為中心：
    Species  1 ── * Specimen     (實體標本)
    Species  1 ── * Observation  (觀察紀錄)
"""

import re
import uuid

from django.db import models
from django.utils import timezone

# 典藏編號格式：LYM-[2碼類群]-[4碼年份]-[4碼流水號]，例：LYM-AV-2026-0001
CATALOG_NUMBER_RE = re.compile(r"^LYM-[A-Z]{2}-\d{4}-\d{4}$")


class Species(models.Model):
    """物種表 — 系統的分類骨幹，可對接臺灣物種名錄 TaiCOL。

    主鍵為自動流水號（surrogate key），學名另設 unique 以保證唯一。
    如此日後訂正學名時不需更動主鍵，Specimen／Observation 的關聯不受影響。
    """

    class TaxonGroup(models.TextChoices):
        BIRD = "bird", "鳥"
        INSECT = "insect", "昆蟲"
        BAT = "bat", "蝙蝠"
        FLYING_SQUIRREL = "flying_squirrel", "飛鼠"
        # 與 Specimen.TaxonGroup 對齊，供標本類群為「其他」時自動建立物種可對應
        OTHER = "other", "其他"

    class ConservationStatus(models.TextChoices):
        # 依《野生動物保育法》公告之法定等級（由低到高）
        GENERAL = "general", "一般類"
        OTHER = "other", "其他應予保育"
        RARE = "rare", "珍貴稀有"
        ENDANGERED = "endangered", "瀕臨絕種"
        # 尚未查證：提供「不確定」時的合法填法，避免被迫亂選；
        # 因非「一般類」，公開頁一律採保護性處理（fail-safe）。
        UNVERIFIED = "unverified", "待查證"

    class IucnStatus(models.TextChoices):
        # IUCN 紅皮書受威脅等級
        LC = "LC", "LC 無危"
        NT = "NT", "NT 接近受脅"
        VU = "VU", "VU 易危"
        EN = "EN", "EN 瀕危"
        CR = "CR", "CR 極危"
        DD = "DD", "DD 資料不足"
        NE = "NE", "未評估"

    common_name = models.CharField(
        "中文名", max_length=200, blank=True,
    )
    scientific_name = models.CharField(
        "學名／拉丁名", max_length=200, unique=True,
    )
    taicol_taxon_id = models.CharField(
        "TaiCOL 物種編號", max_length=50, blank=True, default="",
    )
    inaturalist_taxon_id = models.CharField(
        "iNaturalist Taxon ID", max_length=20, blank=True, default="",
        help_text="填入後將直接連往該物種頁面；留白則以學名查詢台灣觀察紀錄。",
    )
    taxon_group = models.CharField(
        "分類群", max_length=20, choices=TaxonGroup.choices,
    )

    # 分類階層（可留空，之後由 TaiCOL 補齊）
    order = models.CharField("目 (Order)", max_length=100, blank=True, default="")
    family = models.CharField("科 (Family)", max_length=100, blank=True, default="")
    genus = models.CharField("屬 (Genus)", max_length=100, blank=True, default="")

    conservation_status = models.CharField(
        "保育等級（台灣）", max_length=20,
        choices=ConservationStatus.choices,
        # 移除預設值 → 建檔者必須主動選擇，避免忘改「一般類」造成誤判。
        help_text=(
            "必選。不確定時請選「待查證」。凡非「一般類」（含待查證）者，"
            "公開頁一律採保護性處理：其觀察影像不對外顯示、標本地點僅到縣市層級。"
        ),
    )
    iucn_status = models.CharField(
        "保育等級（IUCN）", max_length=2,
        choices=IucnStatus.choices,
        default=IucnStatus.NE,
    )
    introduction = models.TextField(
        "物種介紹", blank=True, default="",
        help_text="公開頁面顯示的物種簡介。",
    )
    public_description = models.TextField(
        "公開簡介", blank=True, default="",
        help_text="顯示於公開檢索頁簡易版的物種介紹，建議 80 到 120 字，語氣平易近人。",
    )
    # 最後更新時間：存檔時自動更新，供公開頁顯示「資料最後更新」與學術引用
    updated_at = models.DateTimeField("最後更新", auto_now=True)
    is_auto_created = models.BooleanField(
        "由標本建檔自動產生", default=False,
        help_text="此筆物種資料由標本建檔時自動產生，請盡快補齊保育等級與分類資訊。",
    )

    class Meta:
        verbose_name = "物種"
        verbose_name_plural = "物種"
        ordering = ["taxon_group", "scientific_name"]
        indexes = [
            # 支援清單預設排序與「分類群」篩選
            models.Index(fields=["taxon_group", "scientific_name"]),
            # 支援「保育等級」篩選
            models.Index(fields=["conservation_status"]),
        ]

    def __str__(self):
        if self.common_name:
            return f"{self.common_name}（{self.scientific_name}）"
        return self.scientific_name


class CollectionEvent(models.Model):
    """採集事件 — 一次採集的時間／地點／座標／採集者等資訊。

    多件同一次採集的標本可共用一個採集事件，避免重複輸入相同資訊；
    對應 Darwin Core 的 Event（eventID 見 event_uuid）。
    """

    class SamplingProtocol(models.TextChoices):
        MIST_NET = "mist_net", "霧網"
        LIGHT_TRAP = "light_trap", "燈光誘集"
        SWEEP_NET = "sweep_net", "掃網"
        TRAP = "trap", "陷阱"
        FOUND = "found", "拾獲"
        ACOUSTIC = "acoustic", "聲波偵測"
        OTHER = "other", "其他"

    event_uuid = models.UUIDField(
        "採集事件識別碼", default=uuid.uuid4, editable=False,
        unique=True, db_index=True,
        help_text="系統自動產生，作為 Darwin Core eventID，產生後不得變更。",
    )
    collection_date = models.DateField("採集日期", null=True, blank=True)
    collection_location = models.CharField(
        "採集地點", max_length=300, blank=True,
        help_text=(
            "請填至縣市與鄉鎮市區層級即可（例：宜蘭縣蘇澳鎮）。"
            "為保護保育類棲地、防範盜獵，請勿填入巷弄、門牌、確切巢位或棲所座標；"
            "精確座標請填於下方「緯度／經度」欄位（不會出現在公開頁）。"
        ),
    )
    latitude = models.DecimalField(
        "緯度", max_digits=9, decimal_places=6, null=True, blank=True,
    )
    longitude = models.DecimalField(
        "經度", max_digits=9, decimal_places=6, null=True, blank=True,
    )
    collector = models.CharField(
        "採集者", max_length=200, blank=True,
        help_text="主動採集者或民眾拾獲者皆填於此。",
    )
    habitat = models.CharField(
        "棲地環境", max_length=300, blank=True,
        help_text="對應 Darwin Core habitat；選填。",
    )
    sampling_protocol = models.CharField(
        "採集方法", max_length=20,
        choices=SamplingProtocol.choices, blank=True,
        help_text="對應 Darwin Core samplingProtocol；選填。",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "採集事件"
        verbose_name_plural = "採集事件"
        ordering = ["-collection_date", "-created_at"]

    def __str__(self):
        loc = self.collection_location or "（地點未填）"
        date = self.collection_date.isoformat() if self.collection_date else "（日期未填）"
        label = f"{loc}｜{date}"
        return f"{label}｜{self.collector}" if self.collector else label


class Specimen(models.Model):
    """標本表 — 館藏實體標本，主鍵為典藏編號。"""

    # 依 Darwin Core，實體標本的 basisOfRecord 固定為此值
    BASIS_OF_RECORD = "PreservedSpecimen"

    # 典藏編號固定機構前綴
    CATALOG_PREFIX = "LYM"
    # 典藏編號中的類群代碼（依標本自身的類群欄位 taxon_group；蝙蝠與飛鼠同為 MA）
    # 以字串值為鍵，同時相容 Species.TaxonGroup 與 Specimen.TaxonGroup（值相同）
    GROUP_CODE = {
        "bird": "AV",
        "insect": "IN",
        "bat": "MA",
        "flying_squirrel": "MA",
        "other": "OT",
    }

    # 標本自身的類群（即使尚未鑑定到種，仍須指定；決定館藏編號的類群代碼）
    class TaxonGroup(models.TextChoices):
        BIRD = "bird", "鳥類"
        INSECT = "insect", "昆蟲"
        BAT = "bat", "蝙蝠"
        FLYING_SQUIRREL = "flying_squirrel", "飛鼠"
        OTHER = "other", "其他"

    # 鑑定狀態（表達 Identification 歷程無法表達的粒度：未鑑定／科／屬／無法鑑定等）
    class IdentificationStatus(models.TextChoices):
        UNIDENTIFIED = "unidentified", "未鑑定"
        IN_PROGRESS = "in_progress", "鑑定中"
        TO_FAMILY = "to_family", "已鑑定至科"
        TO_GENUS = "to_genus", "已鑑定至屬"
        TO_SPECIES = "to_species", "已鑑定至種"
        UNIDENTIFIABLE = "unidentifiable", "無法鑑定"

    class SpecimenType(models.TextChoices):
        TAXIDERMY = "taxidermy", "剝製"
        PINNED = "pinned", "針插"
        FLUID = "fluid", "浸液"
        SKELETON = "skeleton", "骨骼"

    class Source(models.TextChoices):
        DONATION = "donation", "捐贈"
        RESCUE = "rescue", "救傷"
        COLLECTION = "collection", "採集"
        TRANSFER = "transfer", "移交"

    class Status(models.TextChoices):
        IN_STORAGE = "in_storage", "在庫"
        LOANED = "loaned", "借出"
        ON_DISPLAY = "on_display", "展示中"
        UNDER_REPAIR = "under_repair", "待修復"
        LOST = "lost", "遺失"

    # 標本製作狀態（支援冰箱中尚未製作的冷凍標本先行建檔）
    class PreparationStatus(models.TextChoices):
        FROZEN_PENDING = "frozen_pending", "冷凍待處理"
        IN_PROGRESS = "in_progress", "製作中"
        DONE = "done", "已完成"
        NOT_REQUIRED = "not_required", "無需製作"
        OTHER = "other", "其他"

    class PreservationMethod(models.TextChoices):
        TAXIDERMY = "taxidermy", "剝製"
        STUDY_SKIN = "study_skin", "假剝製"
        FLUID = "fluid", "浸液"
        SKELETON = "skeleton", "骨骼"
        DRIED = "dried", "乾燥"
        PINNED = "pinned", "針插"
        FROZEN = "frozen", "冷凍"
        OTHER = "other", "其他"

    class CauseOfDeath(models.TextChoices):
        ROADKILL = "roadkill", "路殺"
        WINDOW_STRIKE = "window_strike", "窗殺"
        ELECTROCUTION = "electrocution", "電殺"
        CAT_DOG = "cat_dog", "貓犬咬傷"
        SUSPECTED_POISON = "suspected_poison", "疑似中毒"
        ENTANGLE_BYCATCH = "entangle_bycatch", "纏繞或誤捕"
        NATURAL = "natural", "自然死亡"
        CONFISCATED = "confiscated", "查獲沒入"
        UNKNOWN = "unknown", "不明"
        OTHER = "other", "其他"

    class AcquisitionType(models.TextChoices):
        PUBLIC_HANDIN = "public_handin", "民眾拾獲送交"
        STAFF_COLLECT = "staff_collect", "館員採集"
        AUTHORITY_TRANSFER = "authority_transfer", "主管機關移交"
        DONATION = "donation", "捐贈"
        PURCHASE = "purchase", "收購"
        EXCHANGE = "exchange", "與他館交換"
        OTHER = "other", "其他"

    # 危害標記可複選；都不勾即代表「無」
    HAZARD_CHOICES = [
        ("as", "砷 As"),
        ("hg", "汞 Hg"),
        ("other", "其他防蟲劑"),
    ]

    catalog_number = models.CharField(
        "典藏編號", max_length=50, primary_key=True,
        blank=True,
        help_text="留空即依「分類群代碼-年份-流水號」自動產生（例：AVE-2026-0001）；也可自行填寫。",
    )
    occurrence_uuid = models.UUIDField(
        "全球唯一識別碼", default=uuid.uuid4, editable=False,
        unique=True, db_index=True,
        help_text="系統自動產生，用於對外發布與引用，產生後不得變更。",
    )
    taxon_group = models.CharField(
        # max_length 用 20（非任務所述 10）：值「flying_squirrel」長 15，且刻意
        # 與 Species.taxon_group 的值一致，資料回填可直接複製、GROUP_CODE 共用。
        "類群", max_length=20, choices=TaxonGroup.choices,
        help_text="即使尚未鑑定到種，仍須指定類群；此欄位決定館藏編號的類群代碼。",
    )
    identification_status = models.CharField(
        "鑑定狀態", max_length=20,
        choices=IdentificationStatus.choices,
        default=IdentificationStatus.UNIDENTIFIED,
    )
    species = models.ForeignKey(
        Species, on_delete=models.PROTECT,
        related_name="specimens", verbose_name="學名",
        null=True, blank=True,
        help_text="尚未鑑定者可留白，待鑑定後再補填。",
    )
    specimen_type = models.CharField(
        "標本類型", max_length=20, choices=SpecimenType.choices,
    )

    # 採集事件（選填）：多件同一次採集的標本共用一筆，避免重複輸入。
    # 公開頁／CSV／DwC 匯出一律改讀此關聯的採集事件，不再讀下方保留的舊採集欄位。
    collection_event = models.ForeignKey(
        "CollectionEvent", on_delete=models.PROTECT,
        related_name="specimens", verbose_name="採集事件",
        null=True, blank=True,
        help_text="選填。來源不明的標本可留空。",
    )

    # 採集資訊（舊欄位：已改由「採集事件」提供，僅保留於資料庫供歷史查考，撤出表單）
    collector = models.CharField(
        "採集者", max_length=200, blank=True,
        help_text="主動採集者或民眾拾獲者皆填於此，來源性質請於『取得方式』欄位標示",
    )
    collection_date = models.DateField("採集日期", null=True, blank=True)
    collection_location = models.CharField(
        "採集地點", max_length=300, blank=True,
        help_text=(
            "請填至縣市與鄉鎮市區層級即可（例：宜蘭縣蘇澳鎮）。"
            "為保護保育類棲地、防範盜獵，請勿填入巷弄、門牌、確切巢位或棲所座標；"
            "精確座標請填於下方「緯度／經度」欄位（不會出現在公開頁）。"
        ),
    )
    latitude = models.DecimalField(
        "緯度", max_digits=9, decimal_places=6, null=True, blank=True,
    )
    longitude = models.DecimalField(
        "經度", max_digits=9, decimal_places=6, null=True, blank=True,
    )

    # 入藏資訊
    # 註：以下三個欄位已停用（自表單撤下），保留於資料庫供歷史資料查考，
    #     新建檔請改用「標本製作與來源」區塊的對應新欄位。
    accession_date = models.DateField(
        "入藏日期", default=timezone.localdate,
        help_text="已停用，保留供歷史資料查考，請改用『取得日期』(acquisition_date)。",
    )
    source = models.CharField(
        "來源", max_length=20, choices=Source.choices,
        null=True, blank=True,
        help_text="已停用，保留供歷史資料查考，請改用『取得方式』(acquisition_type)。",
    )
    preservation_status = models.CharField(
        "保存狀態", max_length=200, blank=True,
        help_text="已停用，保留供歷史資料查考，請改用『保存方式』(preservation_method)。",
    )

    # 庫房位置
    storeroom = models.CharField("庫房", max_length=100, blank=True)
    cabinet = models.CharField("櫃號", max_length=50, blank=True)
    drawer = models.CharField("抽屜號", max_length=50, blank=True)

    image = models.ImageField(
        "標本影像", upload_to="specimens/", null=True, blank=True,
        help_text="已停用，請改用『標本影像』（可上傳多張的標本影像表）。",
    )

    # 狀態與危害
    status = models.CharField(
        "標本狀態", max_length=20,
        choices=Status.choices, default=Status.IN_STORAGE,
    )
    hazard_markers = models.JSONField(
        "危害標記", default=list, blank=True,
        help_text="可複選；都不勾即代表「無」。曾以砷／汞／其他防蟲劑處理者請勾選。",
    )

    # 鑑定資訊
    identified_by = models.CharField(
        "鑑定者", max_length=200, blank=True, default="",
    )
    identified_date = models.DateField("鑑定日期", null=True, blank=True)

    remarks = models.TextField("備註", blank=True, default="")

    # 標本製作與來源（支援冷凍待製作標本先行建檔；皆可空白以免既有資料無法儲存）
    preparation_status = models.CharField(
        "製作狀態", max_length=20,
        choices=PreparationStatus.choices, default=PreparationStatus.DONE,
        help_text="冰箱中尚未處理者請選「冷凍待處理」",
    )
    preservation_method = models.CharField(
        "保存方式", max_length=20,
        choices=PreservationMethod.choices, blank=True,
    )
    cause_of_death = models.CharField(
        "死亡原因", max_length=20,
        choices=CauseOfDeath.choices, blank=True,
        help_text="與人為因素相關者，具展示詮釋價值，請盡量填寫",
    )
    cause_of_death_note = models.CharField(
        "死亡原因補述", max_length=200, blank=True,
    )
    acquisition_type = models.CharField(
        "取得方式", max_length=20,
        choices=AcquisitionType.choices, blank=True,
    )
    acquisition_date = models.DateField(
        "取得日期", null=True, blank=True,
        help_text="拾獲或入館日期，與採集日期不同時請分別填寫",
    )
    preparer = models.CharField("標本製作者", max_length=100, blank=True)
    preparation_date = models.DateField("製作完成日期", null=True, blank=True)
    storage_location = models.CharField(
        "存放位置", max_length=100, blank=True,
        help_text="例如 典藏庫冷凍櫃A-3、標本室第2櫃",
    )

    # 建檔時間（供儀表板「最近新增」使用；不顯示於表單）
    created_at = models.DateTimeField(
        "建立時間", default=timezone.now, editable=False,
    )

    class Meta:
        verbose_name = "標本"
        verbose_name_plural = "標本"
        ordering = ["catalog_number"]
        indexes = [
            # list_filter 常用欄位（species FK 已自動索引）
            models.Index(fields=["status"]),
            models.Index(fields=["preparation_status"]),
            models.Index(fields=["cause_of_death"]),
        ]

    def hazard_labels(self):
        """把已勾選的危害代碼轉成人類可讀的標籤清單。"""
        mapping = dict(self.HAZARD_CHOICES)
        return [mapping.get(code, code) for code in (self.hazard_markers or [])]

    @property
    def basis_of_record(self):
        """Darwin Core basisOfRecord — 標本固定為 PreservedSpecimen。"""
        return self.BASIS_OF_RECORD

    @classmethod
    def next_catalog_number(cls, taxon_group, year=None):
        """依「LYM-類群代碼-年份-4位流水號」算出該類群當年度的下一個編號。

        流水號以「同類群代碼＋同年度」為範圍，從 0001 起遞增，
        例如 LYM-AV-2026-0001、LYM-IN-2026-0023。
        """
        code = cls.GROUP_CODE[taxon_group]
        year = year or timezone.localdate().year
        prefix = f"{cls.CATALOG_PREFIX}-{code}-{year}-"
        last = (
            cls.objects
            .filter(catalog_number__startswith=prefix)
            .order_by("-catalog_number")
            .first()
        )
        last_serial = int(last.catalog_number.rsplit("-", 1)[-1]) if last else 0
        return f"{prefix}{last_serial + 1:04d}"

    def generate_catalog_number(self):
        """為本標本（依其類群 taxon_group、當年度）產生下一個典藏編號。"""
        return self.next_catalog_number(self.taxon_group)

    def save(self, *args, **kwargs):
        # 典藏編號留空時自動產生；已手填則尊重原值
        if not self.catalog_number:
            self.catalog_number = self.generate_catalog_number()
        super().save(*args, **kwargs)

    def __str__(self):
        # 尚未鑑定（無 species）時，以類群名稱代替，避免顯示 None
        label = self.species if self.species_id else f"（未鑑定・{self.get_taxon_group_display()}）"
        return f"{self.catalog_number}｜{label}"


class Observation(models.Model):
    """觀察紀錄表 — 由網路資料源匯入的目擊/觀察，主鍵為紀錄編號。"""

    # 依 Darwin Core，人為觀察的 basisOfRecord 固定為此值
    BASIS_OF_RECORD = "HumanObservation"

    class DataSource(models.TextChoices):
        EBIRD = "ebird", "eBird"
        YILAN_BIRD = "yilan_bird", "宜蘭野鳥學會"
        BAT_SOCIETY = "bat_society", "蝙蝠學會"
        OTHER = "other", "其他"

    record_number = models.CharField(
        "紀錄編號", max_length=50, primary_key=True,
    )
    species = models.ForeignKey(
        Species, on_delete=models.PROTECT,
        related_name="observations", verbose_name="學名",
    )
    observer = models.CharField("觀察者", max_length=200, blank=True)
    observation_date = models.DateField("觀察日期", null=True, blank=True)
    observation_location = models.CharField(
        "觀察地點", max_length=300, blank=True,
    )
    latitude = models.DecimalField(
        "緯度", max_digits=9, decimal_places=6, null=True, blank=True,
    )
    longitude = models.DecimalField(
        "經度", max_digits=9, decimal_places=6, null=True, blank=True,
    )
    count = models.PositiveIntegerField("數量", null=True, blank=True)
    data_source = models.CharField(
        "資料來源", max_length=20, choices=DataSource.choices,
    )
    source_reference = models.CharField(
        "來源網址或原始ID", max_length=500, blank=True,
    )

    class Meta:
        verbose_name = "觀察紀錄"
        verbose_name_plural = "觀察紀錄"
        ordering = ["-observation_date", "record_number"]
        indexes = [
            # 支援「資料來源」篩選與依觀察日期排序（species FK 已自動索引）
            models.Index(fields=["data_source"]),
            models.Index(fields=["observation_date"]),
        ]

    @property
    def basis_of_record(self):
        """Darwin Core basisOfRecord — 觀察固定為 HumanObservation。"""
        return self.BASIS_OF_RECORD

    def __str__(self):
        return f"{self.record_number}｜{self.species}"


class Movement(models.Model):
    """異動紀錄表 — 一件標本的借出／歸還／送修等異動歷程。"""

    class MovementType(models.TextChoices):
        LOAN_OUT = "loan_out", "借出"
        RETURN = "return", "歸還"
        REPAIR = "repair", "送修"
        DISPLAY = "display", "展示"
        STORE_IN = "store_in", "入庫"
        OTHER = "other", "其他"

    specimen = models.ForeignKey(
        Specimen, on_delete=models.CASCADE,
        related_name="movements", verbose_name="標本",
    )
    movement_type = models.CharField(
        "異動類型", max_length=20, choices=MovementType.choices,
    )
    movement_date = models.DateField("異動日期")
    counterparty = models.CharField(
        "對象單位或個人", max_length=300, blank=True,
        help_text="例：借給某大學、送某修復師。",
    )
    handler = models.CharField("經手人", max_length=200, blank=True)
    remarks = models.TextField("備註", blank=True)

    class Meta:
        verbose_name = "異動紀錄"
        verbose_name_plural = "異動紀錄"
        # 最新的異動排在最上面
        ordering = ["-movement_date", "-id"]

    def __str__(self):
        return f"{self.specimen_id}｜{self.get_movement_type_display()}｜{self.movement_date}"


class SpecimenImage(models.Model):
    """標本影像表 — 一件標本可有多張照片。"""

    # 標本專用影像類型（保留原有語意，不與 BaseMediaImage 的生態照/棲地混用）
    class ImageType(models.TextChoices):
        BODY = "body", "標本本體"
        LABEL = "label", "原始標籤"
        DETAIL = "detail", "細節"
        OTHER = "other", "其他"

    # 授權方式：與 SpeciesImage（BaseMediaImage.License）完全一致
    class License(models.TextChoices):
        MUSEUM = "museum", "館方自攝"
        CC0 = "cc0", "CC0"
        CC_BY = "cc_by", "CC BY"
        CC_BY_NC = "cc_by_nc", "CC BY-NC"
        CC_BY_SA = "cc_by_sa", "CC BY-SA"
        OTHER = "other", "其他授權"
        UNVERIFIED = "unverified", "未確認"

    specimen = models.ForeignKey(
        Specimen, on_delete=models.CASCADE,
        related_name="images", verbose_name="標本",
    )
    image = models.ImageField("影像檔", upload_to="specimen_images/")
    image_type = models.CharField(
        "影像類型", max_length=20, choices=ImageType.choices,
    )
    caption = models.CharField("說明", max_length=300, blank=True)
    # 以下欄位比照 SpeciesImage/ObservationImage 補齊（公開展示與授權控管用）
    is_public = models.BooleanField(
        "對外公開", default=True,
        help_text="取消勾選則僅限館內人員檢視。",
    )
    is_primary = models.BooleanField(
        "主要影像", default=False,
        help_text="勾選後會顯示於檢索結果與詳細頁。",
    )
    photographer = models.CharField("攝影者", max_length=100, blank=True)
    license = models.CharField(
        "授權方式", max_length=20,
        choices=License.choices, default=License.UNVERIFIED, blank=True,
    )
    license_note = models.CharField(
        "授權備註", max_length=200, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "標本影像"
        verbose_name_plural = "標本影像"
        ordering = ["-is_primary", "created_at"]

    def save(self, *args, **kwargs):
        """確保同一標本最多一張主要影像：本筆勾選 is_primary 時，取消其餘。"""
        super().save(*args, **kwargs)
        if self.is_primary:
            SpecimenImage.objects.filter(
                specimen_id=self.specimen_id
            ).exclude(pk=self.pk).update(is_primary=False)

    def __str__(self):
        return f"{self.specimen_id}｜{self.get_image_type_display()}"


class Identification(models.Model):
    """鑑定歷程表 — 一件標本歷次的物種鑑定紀錄。"""

    specimen = models.ForeignKey(
        Specimen, on_delete=models.CASCADE,
        related_name="identifications", verbose_name="標本",
    )
    identified_as = models.ForeignKey(
        Species, on_delete=models.PROTECT,
        related_name="identifications", verbose_name="鑑定為物種",
    )
    identified_by = models.CharField("鑑定者", max_length=200, blank=True)
    identified_date = models.DateField("鑑定日期")
    basis = models.TextField(
        "鑑定依據／備註", blank=True,
        help_text="例：依羽色、依標本標籤、參考文獻。",
    )
    is_current = models.BooleanField("是否為現行鑑定", default=False)

    class Meta:
        verbose_name = "鑑定歷程"
        verbose_name_plural = "鑑定歷程"
        # 最新的鑑定排在最上面
        ordering = ["-identified_date", "-id"]

    def __str__(self):
        return f"{self.specimen_id}｜{self.identified_as}｜{self.identified_date}"


class BaseMediaImage(models.Model):
    """物種／觀察紀錄影像的共用欄位與行為（抽象基底）。

    SpeciesImage 與 ObservationImage 結構一致、僅外鍵不同，故共用此基底；
    既有的 SpecimenImage 不繼承此類，結構完全不受影響。
    影像檔沿用專案設定的 default storage（線上為 Cloudinary）。
    """

    # 活體／野外情境的影像類型（與標本用的 SpecimenImage.ImageType 不同）
    class ImageType(models.TextChoices):
        LIVE = "live", "生態照"
        DETAIL = "detail", "細節特徵"
        HABITAT = "habitat", "棲地環境"
        OTHER = "other", "其他"

    class License(models.TextChoices):
        MUSEUM = "museum", "館方自攝"
        CC0 = "cc0", "CC0"
        CC_BY = "cc_by", "CC BY"
        CC_BY_NC = "cc_by_nc", "CC BY-NC"
        CC_BY_SA = "cc_by_sa", "CC BY-SA"
        OTHER = "other", "其他授權"
        UNVERIFIED = "unverified", "未確認"

    # 子類別指定「判定同一主體」所用的外鍵欄位名（save() 據此確保唯一主要影像）
    parent_field = None

    image_type = models.CharField(
        "影像類型", max_length=20, choices=ImageType.choices,
    )
    is_primary = models.BooleanField(
        "主要影像", default=False,
        help_text="勾選後會顯示於檢索結果與詳細頁。",
    )
    is_public = models.BooleanField(
        "對外公開", default=True,
        help_text="取消勾選則僅限館內人員檢視。",
    )
    photographer = models.CharField("攝影者", max_length=100, blank=True)
    license = models.CharField(
        "授權方式", max_length=20,
        choices=License.choices, default=License.UNVERIFIED,
    )
    license_note = models.CharField(
        "授權備註", max_length=200, blank=True,
        help_text="選擇「其他授權」時請填寫授權來源與範圍。",
    )
    caption = models.CharField("說明", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["-is_primary", "created_at"]

    def save(self, *args, **kwargs):
        """確保同一主體最多一張主要影像：本筆勾選 is_primary 時，取消其餘。"""
        super().save(*args, **kwargs)
        if self.is_primary:
            parent_id_field = f"{self.parent_field}_id"
            type(self).objects.filter(
                **{parent_id_field: getattr(self, parent_id_field)}
            ).exclude(pk=self.pk).update(is_primary=False)


class SpeciesImage(BaseMediaImage):
    """物種影像表 — 一個物種可有多張代表照片。"""

    parent_field = "species"

    species = models.ForeignKey(
        Species, on_delete=models.CASCADE,
        related_name="images", verbose_name="物種",
    )
    image = models.ImageField("影像檔", upload_to="species_images/")

    class Meta(BaseMediaImage.Meta):
        verbose_name = "物種影像"
        verbose_name_plural = "物種影像"

    def __str__(self):
        return f"{self.species}｜{self.get_image_type_display()}"


class ObservationImage(BaseMediaImage):
    """觀察紀錄影像表 — 一筆觀察紀錄可有多張現場照片。"""

    parent_field = "observation"

    observation = models.ForeignKey(
        Observation, on_delete=models.CASCADE,
        related_name="images", verbose_name="觀察紀錄",
    )
    image = models.ImageField("影像檔", upload_to="observation_images/")

    class Meta(BaseMediaImage.Meta):
        verbose_name = "觀察紀錄影像"
        verbose_name_plural = "觀察紀錄影像"

    def __str__(self):
        return f"{self.observation_id}｜{self.get_image_type_display()}"
