"""Darwin Core 匯出的資料組裝（供後台批次動作與側邊欄「Darwin Core 匯出」共用）。

把標本 queryset 轉成 Darwin Core 欄位的 CSV，打包成 ZIP（附授權與來源說明），回傳 bytes。
與「全欄位匯出」互相獨立、並存：
- 本匯出為對外標準化子集，欄位對應 Darwin Core（可供日後發布至 GBIF／TBIA）。
- 僅在 CSV 匯出這一層把中文選項轉成 DwC 控制詞彙；資料庫、Admin 介面、公開頁面
  一律維持 choices 的中文標籤不變。
"""

import csv
import io
import json
import re
import zipfile

from django.urls import reverse
from django.utils import timezone

from .models import Specimen, Species

# ── Darwin Core 匯出專用的「值」對照 ──────────────────────────────
# 鍵為 Specimen 的 choices 值（中文標籤所對應的內部值），未列入者（含「不明」）
# 一律由 .get(..., "") 輸出空字串（符合 DwC 慣例）。
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


def _county_of(text):
    """從自由文字地點擷取到「縣／市」為止的縣市層級字串（供 DwC stateProvince）。"""
    if not text:
        return ""
    m = re.search(r"^.*?[縣市]", text)
    return m.group(0) if m else ""


def dwc_export_readme(request):
    """Darwin Core 匯出附帶的授權與來源說明（獨立說明檔內容）。

    採「獨立說明檔」而非 CSV 檔首的井字號註解列，理由：
    (1) 註解列會破壞嚴格 CSV／Darwin Core Archive 解析器對標題列的判讀；
    (2) 授權尚待館方裁示，不宜在每列 license 欄位寫死未定案的授權值。
    """
    try:
        license_url = request.build_absolute_uri(reverse("license"))
    except Exception:  # noqa: BLE001 — 反查失敗仍要能匯出，退回相對路徑
        license_url = "/license/"
    now = timezone.localtime().strftime("%Y-%m-%d %H:%M")
    return (
        "蘭陽博物館 飛行生物典藏系統\n"
        "Darwin Core 匯出資料 — 授權與來源說明\n"
        f"匯出時間：{now}\n"
        "\n"
        "【狀態】\n"
        "本資料為試營運階段之匯出，內容與授權尚未經館方正式審核。\n"
        "\n"
        "【授權】\n"
        "本資料之授權條款尚待館方裁示，正式公告前請勿逕行再利用。\n"
        f"授權建議選項與最新狀態請見「資料授權聲明」頁：\n{license_url}\n"
        "\n"
        "【來源】\n"
        "資料來源：蘭陽博物館（Darwin Core institutionCode：LYM）。\n"
        "個別標本之來源單位與主管機關許可文號（若有）記於 CSV 的\n"
        "dynamicProperties 欄位（sourceInstitution／permitNumber）。\n"
        "\n"
        "【保育類資料限制】\n"
        "保育類物種之精確座標一律模糊化，CSV 以 informationWithheld 欄位標示；\n"
        "不得試圖還原或推導精確棲地位置。\n"
        "\n"
        "【引用】\n"
        "資料庫層級與單筆標本引用格式見上述授權聲明頁第三節；\n"
        "單筆引用請包含 catalogNumber 與 occurrenceID（urn:uuid）。\n"
    )


def build_darwin_core_zip(request, queryset):
    """把標本 queryset 組成 Darwin Core 欄位的 CSV，打包成 ZIP（附授權說明），回傳 bytes。"""
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

    # 先把 CSV 寫進字串緩衝區（檔首單一 BOM 供 Excel 正確判讀 UTF-8 中文）。
    csv_buffer = io.StringIO()
    csv_buffer.write("﻿")
    writer = csv.writer(csv_buffer)
    writer.writerow([name for name, _ in columns])
    for specimen in queryset:
        writer.writerow([getter(specimen) for _, getter in columns])

    # 授權與來源資訊改以「獨立說明檔」隨附，與 CSV 一起打包成 ZIP：
    # 不在 CSV 內加井字號註解列，避免破壞嚴格 CSV／DwC-Archive 解析器。
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "specimens_darwincore.csv",
            csv_buffer.getvalue().encode("utf-8"),
        )
        zf.writestr(
            "授權與來源說明.txt",
            dwc_export_readme(request).encode("utf-8"),
        )
    return zip_buffer.getvalue()
