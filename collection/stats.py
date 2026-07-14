"""統計頁的資料計算。

public_stats()：公開可見的統計（不含任何內部管理或敏感資訊）。
staff_extra_stats()：僅館方可見的內部管理統計。
"""

from django.db.models import Count, Q

from .models import Observation, Species, Specimen


def _count_map(manager, field):
    """回傳 {欄位值: 筆數} 的字典。用 pk 以相容自訂主鍵的模型。"""
    return {
        row[field]: row["c"]
        for row in manager.values(field).annotate(c=Count("pk"))
    }


def public_stats():
    """公開統計：總數、各分類群、保育等級分布。"""
    species_by_group = _count_map(Species.objects, "taxon_group")
    specimen_by_group = _count_map(Specimen.objects, "species__taxon_group")
    groups = [
        {
            "label": label,
            "value": value,
            "species": species_by_group.get(value, 0),
            "specimens": specimen_by_group.get(value, 0),
        }
        for value, label in Species.TaxonGroup.choices
    ]

    cons_by_status = _count_map(Species.objects, "conservation_status")
    conservation = [
        {"label": label, "value": value, "species": cons_by_status.get(value, 0)}
        for value, label in Species.ConservationStatus.choices
    ]

    return {
        "species_total": Species.objects.count(),
        "specimen_total": Specimen.objects.count(),
        "observation_total": Observation.objects.count(),
        "groups": groups,
        "conservation": conservation,
    }


def staff_extra_stats():
    """內部管理統計：危害、待補完、標本狀態、最近登錄。"""
    hazard_lists = list(
        Specimen.objects.exclude(hazard_markers=[])
        .values_list("hazard_markers", flat=True)
    )
    hazard_as = sum(1 for h in hazard_lists if "as" in (h or []))
    hazard_hg = sum(1 for h in hazard_lists if "hg" in (h or []))
    hazard_other = sum(1 for h in hazard_lists if "other" in (h or []))

    incomplete = Specimen.objects.filter(
        Q(identified_by="")
        | Q(identified_by__isnull=True)
        | Q(collection_date__isnull=True)
    ).count()

    status_map = _count_map(Specimen.objects, "status")
    statuses = [
        {"label": label, "value": value, "count": status_map.get(value, 0)}
        for value, label in Specimen.Status.choices
    ]

    recent = [
        {
            "catalog": s.catalog_number,
            "scientific": s.species.scientific_name,
            "common": s.species.common_name,
            "created": s.created_at,
        }
        for s in Specimen.objects.select_related("species").order_by("-created_at")[:5]
    ]

    return {
        "hazard_total": len(hazard_lists),
        "hazard_as": hazard_as,
        "hazard_hg": hazard_hg,
        "hazard_other": hazard_other,
        "incomplete": incomplete,
        "statuses": statuses,
        "recent": recent,
    }
