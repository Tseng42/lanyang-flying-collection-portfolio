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


def public_stats(simple=False):
    """公開統計：總數、各分類群、保育等級分布。

    simple=True（公開/簡易版）：僅排除自動建立（待查證）物種；未鑑定標本一律計入。
    simple=False（內部/館方版）：顯示全部。觀察紀錄數不受此過濾影響。
    """
    species_qs = Species.objects.all()
    specimen_qs = Specimen.objects.all()
    if simple:
        species_qs = species_qs.exclude(is_auto_created=True)

    species_by_group = _count_map(species_qs, "taxon_group")
    # 標本改用自身的 taxon_group 統計，使「未鑑定標本」也計入所屬類群（不再漏算）
    specimen_by_group = _count_map(specimen_qs, "taxon_group")
    groups = [
        {
            "label": label,
            "value": value,
            "species": species_by_group.get(value, 0),
            "specimens": specimen_by_group.get(value, 0),
        }
        for value, label in Species.TaxonGroup.choices
    ]

    cons_by_status = _count_map(species_qs, "conservation_status")
    conservation = [
        {"label": label, "value": value, "species": cons_by_status.get(value, 0)}
        for value, label in Species.ConservationStatus.choices
    ]

    return {
        "species_total": species_qs.count(),
        "specimen_total": specimen_qs.count(),
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

    # 尚未鑑定到種（無關聯物種）的標本數
    unidentified = Specimen.objects.filter(species__isnull=True).count()

    status_map = _count_map(Specimen.objects, "status")
    statuses = [
        {"label": label, "value": value, "count": status_map.get(value, 0)}
        for value, label in Specimen.Status.choices
    ]

    recent = []
    for s in Specimen.objects.select_related("species").order_by("-created_at")[:5]:
        if s.species_id:
            scientific, common = s.species.scientific_name, s.species.common_name
        else:
            scientific, common = f"（未鑑定・{s.get_taxon_group_display()}）", ""
        recent.append({
            "catalog": s.catalog_number,
            "scientific": scientific,
            "common": common,
            "created": s.created_at,
        })

    return {
        "hazard_total": len(hazard_lists),
        "hazard_as": hazard_as,
        "hazard_hg": hazard_hg,
        "hazard_other": hazard_other,
        "incomplete": incomplete,
        "unidentified": unidentified,
        "statuses": statuses,
        "recent": recent,
    }
