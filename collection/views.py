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
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .external import search_external_links, species_external_links
from .models import Species
from .stats import public_stats, staff_extra_stats


def public_stats_view(request):
    """大眾統計頁（免登入、唯讀）：僅公開數據。"""
    return render(request, "collection/stats_public.html", public_stats())


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
    for s in context["statuses"]:
        s["link"] = sp(status=s["value"])
    # 各分類群 → 物種／標本清單
    for g in context["groups"]:
        g["species_link"] = spc(taxon_group=g["value"])
        g["specimen_link"] = sp(**{"species__taxon_group": g["value"]})

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


def _filtered_species(request):
    """依查詢參數過濾物種，回傳 (queryset, q, group, status)。列表與匯出共用。"""
    q = request.GET.get("q", "").strip()
    group = request.GET.get("taxon_group", "")
    status = request.GET.get("conservation_status", "")

    species = Species.objects.all()
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

    has_results = species.exists()
    context = {
        "species_list": species,
        "q": q,
        "group": group,
        "status": status,
        "searched": bool(q or group or status),
        "taxon_groups": Species.TaxonGroup.choices,
        "conservation_choices": Species.ConservationStatus.choices,
        "total": species.count(),
        # 系統完全沒有任何物種資料（空系統）→ 顯示優雅空狀態
        "db_empty": not Species.objects.exists(),
        # 有查詢條件卻查無結果時，引導到外部權威資源查詢
        "no_result_links": search_external_links(q) if not has_results else None,
    }
    return render(request, "collection/species_list.html", context)


def public_species_export(request):
    """把目前的查詢結果匯出成 CSV（僅公開可見欄位；不含捐贈者姓名與精確座標）。"""
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
    species = get_object_or_404(
        Species.objects.prefetch_related(
            "specimens__identifications__identified_as"
        ),
        pk=pk,
    )
    protected = species.conservation_status != Species.ConservationStatus.GENERAL

    specimens = []
    for sp in species.specimens.all():
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
        })

    context = {
        "species": species,
        "protected": protected,
        "specimen_count": len(specimens),
        "specimens": specimens,
        "external_links": species_external_links(species),
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


# ---------------------------------------------------------------------------
# 暫時性 Cloudinary 診斷端點（除錯完畢後請連同 urls.py 的路由一起移除）
# ---------------------------------------------------------------------------

from django.contrib.admin.views.decorators import staff_member_required


@staff_member_required
def debug_cloudinary(request):
    """直接測試 Cloudinary 上傳，把設定與錯誤訊息以純文字回傳到網頁。

    Render 免費方案無 Shell 且 Logs 會截斷多行輸出，本端點讓完整錯誤訊息
    直接顯示在瀏覽器上。僅限已登入管理員存取。除錯完成後移除。
    """
    import cloudinary
    import cloudinary.uploader
    from django.core.files.storage import default_storage

    cfg = cloudinary.config()
    lines = [
        f"cloud_name: {cfg.cloud_name}",
        f"api_key 前4碼: {str(cfg.api_key)[:4]}****",
        f"default_storage 類別: {default_storage.__class__.__name__}",
        "-" * 40,
    ]

    # 用 Pillow 產生 10x10 純色 PNG，寫進記憶體（不落地磁碟）
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 128, 255)).save(buf, format="PNG")
    buf.seek(0)

    try:
        result = cloudinary.uploader.upload(
            buf, public_id="debug_cloudinary_test", overwrite=True
        )
        lines.append("上傳成功 ✅")
        lines.append(f"secure_url: {result.get('secure_url')}")
    except Exception as exc:  # noqa: BLE001 — 除錯用途，需完整顯示例外
        lines.append("上傳失敗 ❌")
        lines.append(f"repr: {exc!r}")
        lines.append(f"錯誤訊息: {exc}")

    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
