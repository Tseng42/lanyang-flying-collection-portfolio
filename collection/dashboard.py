"""Unfold 儀表板首頁的資料組裝。

設定於 UNFOLD["DASHBOARD_CALLBACK"]；回傳的 context 交給
templates/admin/index.html 渲染成卡片式入口。
所有數字都附上連向「篩選後列表頁」的連結，方便點擊。
"""

from urllib.parse import urlencode

from django.db.models import Q
from django.urls import reverse

from .models import Observation, Species, Specimen


def _changelist(model: str, **params) -> str:
    """組出某模型 admin 列表頁的網址，可帶篩選參數。"""
    url = reverse(f"admin:collection_{model}_changelist")
    if params:
        url += "?" + urlencode(params)
    return url


def dashboard_callback(request, context):
    # 1. 館藏統計
    context["kpi_cards"] = [
        {"label": "物種總數", "value": Species.objects.count(),
         "link": _changelist("species")},
        {"label": "標本總數", "value": Specimen.objects.count(),
         "link": _changelist("specimen")},
        {"label": "觀察紀錄總數", "value": Observation.objects.count(),
         "link": _changelist("observation")},
    ]

    # 依分類群列出物種數與標本數（用模型實際的四個分類群）
    group_rows = []
    for value, label in Species.TaxonGroup.choices:
        group_rows.append({
            "label": label,
            "species": Species.objects.filter(taxon_group=value).count(),
            "species_link": _changelist("species", taxon_group=value),
            "specimens": Specimen.objects.filter(
                species__taxon_group=value).count(),
            "specimen_link": _changelist(
                "specimen", **{"species__taxon_group": value}),
        })
    context["group_rows"] = group_rows

    # 2. 待辦提醒
    hazard_lists = list(
        Specimen.objects.exclude(hazard_markers=[])
        .values_list("hazard_markers", flat=True)
    )
    hazard_total = len(hazard_lists)
    as_count = sum(1 for h in hazard_lists if "as" in (h or []))
    hg_count = sum(1 for h in hazard_lists if "hg" in (h or []))
    other_count = sum(1 for h in hazard_lists if "other" in (h or []))

    incomplete = Specimen.objects.filter(
        Q(identified_by="")
        | Q(identified_by__isnull=True)
        | Q(collection_date__isnull=True)
    ).count()

    loaned = Specimen.objects.filter(
        status=Specimen.Status.LOANED).count()

    context["todo_cards"] = [
        {"label": "含危害標記標本", "value": hazard_total, "alert": True,
         "sub": f"砷 {as_count}／汞 {hg_count}／其他 {other_count}",
         "link": _changelist("specimen", hazard="any")},
        {"label": "待補完標本", "value": incomplete, "alert": False,
         "sub": "缺鑑定者或採集日期",
         "link": _changelist("specimen", completeness="incomplete")},
        {"label": "借出中標本", "value": loaned, "alert": False,
         "sub": "目前狀態為「借出」",
         "link": _changelist("specimen", status=Specimen.Status.LOANED)},
    ]

    # 3. 最近新增的 5 件標本
    recent = []
    for s in Specimen.objects.select_related("species").order_by("-created_at")[:5]:
        recent.append({
            "catalog": s.catalog_number,
            "scientific": s.species.scientific_name,
            "created": s.created_at,
            "link": reverse(
                "admin:collection_specimen_change", args=[s.pk]),
        })
    context["recent_specimens"] = recent

    return context
