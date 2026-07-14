"""外部權威資源連結組裝。

已查證的網址格式：
- TaiCOL 物種頁：https://taicol.tw/zh-hant/taxon/{taxonID}
- GBIF 物種搜尋：https://www.gbif.org/species/search?q={name}
- TBIA 出現紀錄查詢：https://tbiadata.tw/zh-hant/search/occurrence
eBird 無免登入的學名深連結，故鳥類改用同屬 Cornell 生態系、可用學名公開
查詢的 Macaulay Library。
"""

from urllib.parse import quote

from .models import Species


def _q(text: str) -> str:
    return quote(text or "")


def species_external_links(species):
    """物種詳細頁「延伸查詢」的外部連結清單。"""
    links = []
    if species.taicol_taxon_id:
        links.append({
            "label": "TaiCOL 臺灣物種名錄",
            "url": f"https://taicol.tw/zh-hant/taxon/{species.taicol_taxon_id}",
            "note": "依 taxonID 直連物種頁",
        })
    links.append({
        "label": "GBIF 全球生物多樣性資訊機構",
        "url": f"https://www.gbif.org/species/search?q={_q(species.scientific_name)}",
        "note": "以學名搜尋",
    })
    if species.taxon_group == Species.TaxonGroup.BIRD:
        links.append({
            "label": "eBird · Macaulay Library",
            "url": f"https://search.macaulaylibrary.org/catalog?q={_q(species.scientific_name)}",
            "note": "鳥音與影像（以學名查詢）",
        })
        links.append({
            "label": "TBIA 生物多樣性資料共通查詢",
            "url": "https://tbiadata.tw/zh-hant/search/occurrence",
            "note": "跨機構出現紀錄查詢",
        })
    return links


def search_external_links(query: str):
    """查無結果時，引導使用者到外部查詢的連結清單。"""
    label_q = f"查詢「{query}」" if query else "物種查詢"
    return [
        {
            "label": "GBIF 全球生物多樣性資訊機構",
            "url": f"https://www.gbif.org/species/search?q={_q(query)}",
            "note": label_q,
        },
        {
            # TaiCOL 結果由 JS 載入、無可靠的關鍵字網址參數，故連到查詢頁供自行輸入
            "label": "TaiCOL 臺灣物種名錄",
            "url": "https://taicol.tw/zh-hant/catalogue?filter=2",
            "note": "到 TaiCOL 學名／中文名查詢頁",
        },
    ]
