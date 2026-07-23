"""後台「全欄位匯出」：把全部典藏資料匯成單一多工作表 .xlsx（內部完整備份）。

與 Darwin Core 匯出（給 GBIF／TBIA 的標準化子集）互相獨立、並存：
- 本匯出輸出「全部欄位、全部資料」，不依 publication_status 過濾（草稿／待審／公開皆含）。
- 標題列使用欄位的 verbose_name（中文）；外鍵拆成「ID」與「可讀識別」兩欄，方便老師
  以典藏編號／學名在不同工作表之間對照。
- 使用 openpyxl 的 write_only 模式串流寫入，避免大量資料一次載入記憶體。
"""

import json
from io import BytesIO
from zoneinfo import ZoneInfo

from django.apps import apps
from django.db import models
from django.utils import timezone

from openpyxl import Workbook

TAIPEI = ZoneInfo("Asia/Taipei")

# (工作表名稱, 模型類別名)。依序輸出；不存在的模型自動略過（例如子表尚未建立完成）。
SHEET_MODELS = [
    ("物種", "Species"),
    ("標本", "Specimen"),
    ("觀察紀錄", "Observation"),
    ("標本照片", "SpecimenImage"),
    ("物種照片", "SpeciesImage"),
    ("觀察照片", "ObservationImage"),
    ("鑑定歷程", "Identification"),
    ("異動紀錄", "Movement"),
]


def _resolve_models():
    """解析出實際存在的 (工作表名稱, 模型)；模型不存在則略過、不報錯。"""
    resolved = []
    for title, model_name in SHEET_MODELS:
        try:
            model = apps.get_model("collection", model_name)
        except LookupError:
            continue
        resolved.append((title, model))
    return resolved


def _fk_readable(related):
    """外鍵的可讀識別：優先學名／典藏編號／紀錄編號，便於跨工作表對照；否則用 str()。"""
    if related is None:
        return ""
    for attr in ("scientific_name", "catalog_number", "record_number"):
        value = getattr(related, attr, None)
        if value:
            return value
    return str(related)


def _cell_value(obj, field):
    """把單一（非外鍵）欄位值轉為適合寫入儲存格的形式。

    - 空值 → 空字串（不輸出 None/null）
    - 有選項的欄位 → 中文顯示值（例：公開狀態→草稿）
    - 日期時間 → 轉 Asia/Taipei，格式 YYYY-MM-DD HH:MM
    - 日期 → YYYY-MM-DD
    - list／dict（如危害標記 JSON）→ 以 JSON 字串輸出（中文不轉義）
    """
    value = field.value_from_object(obj)
    if value is None:
        return ""
    if getattr(field, "choices", None):
        return getattr(obj, f"get_{field.name}_display")() or ""
    if isinstance(field, models.DateTimeField):
        if timezone.is_aware(value):
            value = value.astimezone(TAIPEI)
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(field, models.DateField):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False) if value else ""
    if isinstance(value, (str, int, float, bool)):
        return value
    # UUID、Decimal 等非原生型別 → 轉字串，確保 openpyxl write_only 可寫入
    return str(value)


def _columns(model):
    """回傳 (headers, getters)。外鍵拆成「<名稱> ID」與「<名稱>」兩欄。"""
    headers, getters = [], []
    for field in model._meta.fields:
        if field.is_relation:
            name = str(field.verbose_name)
            # ID 欄：外鍵的主鍵值（attname，如 species_id）
            headers.append(f"{name} ID")
            getters.append(
                lambda o, f=field: (
                    getattr(o, f.attname)
                    if getattr(o, f.attname) is not None else ""
                )
            )
            # 可讀識別欄
            headers.append(name)
            getters.append(lambda o, f=field: _fk_readable(getattr(o, f.name)))
        else:
            headers.append(str(field.verbose_name))
            getters.append(lambda o, f=field: _cell_value(o, f))
    return headers, getters


def _queryset(model):
    """全部資料（不依 publication_status 過濾）；select_related 外鍵避免 N+1。"""
    fk_names = [f.name for f in model._meta.fields if f.is_relation]
    qs = model._default_manager.all()
    if fk_names:
        qs = qs.select_related(*fk_names)
    return qs


def build_full_export(counts=None):
    """建立整份 .xlsx 並回傳 bytes。

    counts：若給定 dict，會回填 {工作表名稱: 筆數}，供呼叫端顯示或記錄。
    """
    workbook = Workbook(write_only=True)
    for title, model in _resolve_models():
        worksheet = workbook.create_sheet(title=title)
        worksheet.freeze_panes = "A2"  # 凍結標題列
        headers, getters = _columns(model)
        worksheet.append(headers)
        row_count = 0
        for obj in _queryset(model).iterator():
            worksheet.append([getter(obj) for getter in getters])
            row_count += 1
        if counts is not None:
            counts[title] = row_count

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
