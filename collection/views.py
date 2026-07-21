"""公開前台（免登入、唯讀）。

隱私保護原則（重要）：
- 不顯示採集者／來源等個人姓名。
- 保育類物種（非「一般類」）不顯示精確經緯度，地點僅公開至縣市層級，
  以防盜獵。
"""

import csv
import io
import os
import re
import tempfile
from datetime import datetime

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required, permission_required
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .external import search_external_links, species_external_links
from .models import ObservationImage, Species, SpeciesImage, Specimen
from .stats import public_stats, staff_extra_stats


def _is_research(request):
    """公開頁的檢視模式：帶 ?view=research 為研究檢視（顯示全部）；否則為簡易版。

    僅用於「決定資料集過濾與提示文字」，不涉及權限；敏感欄位一律另依權限判斷。
    """
    return request.GET.get("view") == "research"


# Cloudinary 即時轉換參數：只載入縮圖／限制尺寸，不抓原始大圖。
# 本機開發用 FileSystemStorage 時（URL 為 /media/…）會自動略過轉換、原樣回傳。
THUMB_CARD = "w_300,h_300,c_fill,g_auto,q_auto,f_auto"   # 檢索卡片方形縮圖
THUMB_GRID = "w_600,h_600,c_fill,g_auto,q_auto,f_auto"   # 詳細頁格狀方形縮圖
FULL_VIEW = "w_1400,c_limit,q_auto,f_auto"               # 點擊放大檢視


def _cloudinary_url(fieldfile, transform):
    """回傳套用 Cloudinary 即時轉換後的影像 URL。

    僅當 URL 為 Cloudinary 的 /upload/ 路徑時插入轉換參數；本機開發用
    FileSystemStorage 時（URL 為 /media/…）原樣回傳，優雅降級。
    """
    url = fieldfile.url
    marker = "/upload/"
    if marker in url:
        head, tail = url.split(marker, 1)
        return f"{head}{marker}{transform}/{tail}"
    return url


def _gallery_item(img, species):
    """把一筆影像整理成模板要用的欄位（含縮圖／放大圖 URL 與授權資訊）。"""
    return {
        "thumb": _cloudinary_url(img.image, THUMB_GRID),
        "full": _cloudinary_url(img.image, FULL_VIEW),
        "caption": img.caption,
        "photographer": img.photographer,
        "license": img.get_license_display(),
        # 僅「其他授權」時才顯示授權備註
        "license_note": img.license_note if img.license == "other" else "",
        "alt": img.caption or f"{species.common_name} {species.scientific_name}".strip(),
    }


def public_stats_view(request):
    """大眾統計頁（免登入、唯讀）：僅公開數據（採簡易版過濾後的數量）。"""
    return render(request, "collection/stats_public.html", public_stats(simple=True))


@login_required(login_url="/admin/login/")
def staff_stats_view(request):
    """館方統計頁（需登入、唯讀）：公開數據 + 內部管理數據。

    關鍵數字附上「篩選後 Admin 清單」的連結，方便一鍵跳到對應資料。
    """
    from urllib.parse import urlencode

    from django.urls import reverse

    context = public_stats()
    context.update(staff_extra_stats())

    specimen_cl = reverse("admin:collection_specimen_changelist")
    species_cl = reverse("admin:collection_species_changelist")

    def sp(**params):
        return f"{specimen_cl}?{urlencode(params)}"

    def spc(**params):
        return f"{species_cl}?{urlencode(params)}"

    # 待辦類數字 → 篩選後標本清單
    context["hazard_link"] = sp(hazard="any")
    context["incomplete_link"] = sp(completeness="incomplete")
    context["unidentified_link"] = sp(identification_status="unidentified")
    for s in context["statuses"]:
        s["link"] = sp(status=s["value"])
    # 各分類群 → 物種／標本清單（標本改用自身 taxon_group 篩選）
    for g in context["groups"]:
        g["species_link"] = spc(taxon_group=g["value"])
        g["specimen_link"] = sp(taxon_group=g["value"])

    return render(request, "collection/stats_staff.html", context)


def go_home(request):
    """回首頁導覽：未登入直接回首頁；已登入先問要不要順便登出。"""
    if not request.user.is_authenticated:
        return redirect("home")

    # 「取消」要回到的原頁；驗證為本站網址，避免開放重新導向
    from_url = request.GET.get("from", "")
    if not url_has_allowed_host_and_scheme(
        from_url, allowed_hosts={request.get_host()}
    ):
        from_url = ""
    return render(request, "collection/go_home.html", {
        "from_url": from_url or "/",
    })


@require_POST
def go_home_logout(request):
    """登出並回首頁（以 POST 觸發，受 CSRF 保護）。"""
    logout(request)
    return redirect("home")


def _county_of(text: str) -> str:
    """從自由文字地點抽出到「縣／市」為止的縣市層級字串。"""
    if not text:
        return ""
    match = re.search(r"^.*?[縣市]", text)
    return match.group(0) if match else ""


def home(request):
    """網站首頁：三張大卡片入口，依任務分流。"""
    return render(request, "collection/home.html")


def privacy_policy(request):
    """隱私權政策頁（試營運草稿版）；內容為靜態轉檔，無需資料庫查詢。"""
    return render(request, "collection/privacy.html")


def _filtered_species(request):
    """依查詢參數過濾物種，回傳 (queryset, q, group, status)。列表與匯出共用。

    簡易版（無 ?view=research）額外排除自動建立（待查證）物種；研究檢視顯示全部。
    """
    q = request.GET.get("q", "").strip()
    group = request.GET.get("taxon_group", "")
    status = request.GET.get("conservation_status", "")

    species = Species.objects.all()
    # 簡易版排除自動建立（待查證）物種；研究檢視顯示全部
    if not _is_research(request):
        species = species.exclude(is_auto_created=True)
    if q:
        # icontains → 學名/中文名皆不分大小寫
        species = species.filter(
            Q(scientific_name__icontains=q) | Q(common_name__icontains=q)
        )
    if group:
        species = species.filter(taxon_group=group)
    if status:
        species = species.filter(conservation_status=status)
    return species, q, group, status


def public_species_list(request):
    """公開物種查詢頁：搜尋（學名／中文名）＋篩選（分類群／保育等級）。"""
    species, q, group, status = _filtered_species(request)

    # 只預取「對外公開」的物種影像（後端過濾，未公開影像不進入公開頁），
    # 依 SpeciesImage 的 Meta 排序：主要影像優先，否則最早上傳者在前。
    # 用 prefetch_related 一次撈完，避免逐筆查影像造成 N+1。
    species_list = list(species.prefetch_related(Prefetch(
        "images",
        queryset=SpeciesImage.objects.filter(is_public=True),
        to_attr="public_images",
    )))

    # 每筆附上主要縮圖（Cloudinary 即時轉為正方形小圖）；無公開影像則留空，
    # 由模板改顯示佔位圖。
    for s in species_list:
        primary = s.public_images[0] if s.public_images else None
        s.thumb_url = _cloudinary_url(primary.image, THUMB_CARD) if primary else ""

    total = len(species_list)
    context = {
        "species_list": species_list,
        "q": q,
        "group": group,
        "status": status,
        "searched": bool(q or group or status),
        "taxon_groups": Species.TaxonGroup.choices,
        "conservation_choices": Species.ConservationStatus.choices,
        "total": total,
        # 系統完全沒有任何物種資料（空系統）→ 顯示優雅空狀態
        "db_empty": not Species.objects.exists(),
        # 有查詢條件卻查無結果時，引導到外部權威資源查詢
        "no_result_links": search_external_links(q) if not total else None,
        # 檢視模式：研究檢視顯示全部並標示待查證；簡易版於頁尾顯示說明
        "is_research": _is_research(request),
    }
    return render(request, "collection/species_list.html", context)


def public_species_export(request):
    """把目前的查詢結果匯出成 CSV（僅公開可見欄位；不含捐贈者姓名與精確座標）。

    跟隨同一個 view 參數：簡易版排除自動建立（待查證）物種；研究檢視匯出全部。
    未鑑定標本一律計入館藏標本數。此匯出為物種層級、本就不含精確座標等敏感欄位。
    """
    species, _q, _group, _status = _filtered_species(request)
    species = species.annotate(n_specimens=Count("specimens"))

    columns = [
        ("中文名", lambda s: s.common_name),
        ("學名", lambda s: s.scientific_name),
        ("分類群", lambda s: s.get_taxon_group_display()),
        ("保育等級_台灣", lambda s: s.get_conservation_status_display()),
        ("保育等級_IUCN", lambda s: s.get_iucn_status_display()),
        ("目", lambda s: s.order),
        ("科", lambda s: s.family),
        ("屬", lambda s: s.genus),
        ("taxonID", lambda s: s.taicol_taxon_id),
        ("館藏標本數", lambda s: s.n_specimens),
    ]

    response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = (
        'attachment; filename="species_query.csv"'
    )
    # charset=utf-8-sig 已自動加 BOM，讓 Excel 正確判讀 UTF-8 中文
    writer = csv.writer(response)
    writer.writerow([name for name, _ in columns])
    for sp in species:
        writer.writerow([getter(sp) for _, getter in columns])
    return response


def public_species_detail(request, pk):
    """公開物種詳情：兩層檢視（簡易版／研究檢視）。

    隱私在後端就過濾：不輸出採集者／來源等姓名，地點一律只到縣市層級，
    精確經緯度完全不輸出（前端無從取得）。
    """
    species = get_object_or_404(Species, pk=pk)
    is_research = _is_research(request)

    # 簡易版直接開啟被排除（自動建立）的物種 → 302 導向研究檢視，並提示已切換。
    # 不回 404。
    if not is_research and species.is_auto_created:
        return redirect(f"{request.path}?view=research&switched=1")

    protected = species.conservation_status != Species.ConservationStatus.GENERAL

    # 標本：未鑑定標本不再排除，簡易版與研究檢視都顯示（僅以「鑑定中」標示）。
    specimen_qs = species.specimens.prefetch_related(
        "identifications__identified_as"
    )

    # 種名為空時的最低分類階層 fallback（種→屬→科→目→類群），避免留下空白
    def _taxon_label(sp):
        for rank in (species.scientific_name, species.genus,
                     species.family, species.order):
            if rank:
                return rank
        return sp.get_taxon_group_display()

    specimens = []
    for sp in specimen_qs:
        locality = _county_of(sp.collection_location) or "（僅限館內）"

        history = []
        for idn in sp.identifications.all():  # 已依 -identified_date 排序
            history.append({
                "date": idn.identified_date,
                "species": str(idn.identified_as),
                "identified_by": idn.identified_by,   # 鑑定者：研究檢視允許顯示
                "is_current": idn.is_current,
                "basis": idn.basis,
            })

        specimens.append({
            "catalog": sp.catalog_number,
            "type": sp.get_specimen_type_display(),
            "date": sp.collection_date,
            "locality": locality,              # 僅縣市層級
            "identified_by": sp.identified_by,  # 鑑定者（非採集者/來源）
            "history": history,
            # 未鑑定 → 標示「鑑定中」；分類資訊以最低已知階層呈現，不留白
            "is_pending": sp.identification_status == Specimen.IdentificationStatus.UNIDENTIFIED,
            "taxon_label": _taxon_label(sp),
            # 全球唯一識別碼（研究檢視顯示、可複製）
            "occurrence_urn": f"urn:uuid:{sp.occurrence_uuid}",
        })

    # ---- 影像藝廊（隱私與權限一律在後端 queryset 過濾）----
    # 研究人員（已登入且為館方人員）才可另外看到未公開影像；未登入者一律看不到。
    can_view_private = request.user.is_authenticated and request.user.is_staff

    # 公開影像（所有人可見）：物種影像 is_public=True。
    public_gallery = [
        _gallery_item(img, species)
        for img in species.images.filter(is_public=True)
    ]
    # 觀察影像：保育類物種一律不顯示（防盜獵），非保育類才納入公開影像。
    if not protected:
        public_gallery += [
            _gallery_item(img, species)
            for img in ObservationImage.objects.filter(
                observation__species=species, is_public=True,
            )
        ]

    # 未公開影像（僅研究人員可見，置於研究檢視區塊）。未登入者此清單永遠為空，
    # 相關 URL 不會出現在頁面上。
    private_gallery = []
    if can_view_private:
        private_gallery = [
            _gallery_item(img, species)
            for img in species.images.filter(is_public=False)
        ]
        if not protected:
            private_gallery += [
                _gallery_item(img, species)
                for img in ObservationImage.objects.filter(
                    observation__species=species, is_public=False,
                )
            ]

    context = {
        "species": species,
        "protected": protected,
        "specimen_count": len(specimens),
        "specimens": specimens,
        "external_links": species_external_links(species),
        "images": public_gallery,
        "research_images": private_gallery,
        # 檢視模式（決定是否渲染研究區塊、標示待查證、頁面提示）
        "is_research": is_research,
        "switched": request.GET.get("switched") == "1",
    }
    return render(request, "collection/species_detail.html", context)


# ---------------------------------------------------------------------------
# 資料備份／還原（後端強制權限檢查，非僅隱藏按鈕）
#
# 權限判定（Django 的 superuser 自動擁有所有權限）：
#   - 備份：superuser 或具 collection.can_backup_database（→ 典藏主管、管理員）
#   - 還原：superuser 或具 collection.can_restore_database（目前僅 superuser）
# 未登入者：先被 login_required 導向登入頁；已登入但無權限者：raise 403。
# ---------------------------------------------------------------------------

@login_required(login_url="/admin/login/")
@permission_required("collection.can_backup_database", raise_exception=True)
def backup_database(request):
    """匯出 collection 全部典藏資料為 JSON 檔下載（不含使用者帳密）。"""
    buffer = io.StringIO()
    call_command("dumpdata", "collection", indent=2, stdout=buffer)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    response = HttpResponse(buffer.getvalue(), content_type="application/json")
    response["Content-Disposition"] = (
        f'attachment; filename="lymuseum-backup-{stamp}.json"'
    )
    return response


@login_required(login_url="/admin/login/")
@permission_required("collection.can_restore_database", raise_exception=True)
def restore_database(request):
    """從上傳的 JSON 備份還原（loaddata）。目前僅 superuser 有此權限。"""
    result = None
    if request.method == "POST" and request.FILES.get("backup_file"):
        upload = request.FILES["backup_file"]
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="wb"
        ) as tmp:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
        try:
            with transaction.atomic():
                call_command("loaddata", tmp_path)
            result = ("ok", f"已成功還原：{upload.name}")
        except Exception as exc:  # noqa: BLE001 — 還原失敗要回報給使用者
            result = ("error", f"還原失敗：{exc}")
        finally:
            os.unlink(tmp_path)
    return render(request, "collection/restore.html", {"result": result})
