"""查詢頁「簡易版」用的棲域分群推導（水域／陸域／其他）。

前身是「生態分群」（猛禽／水鳥／鳴禽／陸禽／其他）。該分法在同一個下拉選單裡
混用了三種不同準則——猛禽是「食性／生態角色」、水鳥是「棲息環境」、
鳴禽是「分類階元」（就是雀形目）、陸禽是傳統六大類的殘留——彼此不對等且會重疊
（燕科分類上屬雀形目卻歸「其他」、黑鳶在水面覓食卻歸「猛禽」）。
改為單一準則的「棲息環境」二分，再把不適用此準則的非鳥類獨立成一格：

    水域／陸域／其他（非鳥類）

「其他」在此不是收尾雜項，而是明確界線：棲域分類只適用鳥類，
館藏中的蛇類與哺乳類一律歸此格，不參與水陸判定。

實作限制與策略：
- 不新增／修改任何模型欄位，分群一律於查詢時「即時推導」，不落地儲存
  （資料庫沒有任何物種層級的棲地欄位可用；`CollectionEvent.habitat` 是
  「採集事件」的棲地環境、且目前全部空白，無法拿來推導物種的棲息環境）。
- 「目」欄位仍有空白，故推導以「一定有填」的學名（屬名）為主錨點，
  已填的「目」為後援，皆無命中則歸「陸域」。
- **只需維護「水域」一份對照表**：凡非水域的鳥類一律是陸域，不必再為
  猛禽／鳴禽／陸禽各維護一份屬名表（舊的四份表見 git 歷史）。

判定順序（前者優先）：
    1. 非鳥類（taxon_group ≠ bird）  → 其他
    2. 屬名在「陸域例外表」           → 陸域
    3. 屬名在「水域屬名表」           → 水域
    4. 已填的「目」在「水域目表」     → 水域
    5. 以上皆無命中                   → 陸域（預設）

> 預設落陸域是刻意的：陸域鳥種類遠多於水域，逐一列舉不切實際，
> 且漏列的代價（水鳥被誤歸陸域）可用 `list_unverified_habitat_species`
> 管理指令清查——它只挑出「屬名未收錄且目也空白」這種真正無從判斷的個案。
"""

from django.db import models

# 對應 `Species.TaxonGroup.BIRD` 的值。此處刻意用字串常數而非匯入 Species，
# 避免 models 與本模組互相匯入；`Specimen.GROUP_CODE` 亦以相同方式用字串值為鍵。
BIRD = "bird"


class HabitatGroup(models.TextChoices):
    """棲域分群（查詢頁簡易版篩選用）。value 僅供網址參數與內部比對。"""

    AQUATIC = "aquatic", "水域"
    TERRESTRIAL = "terrestrial", "陸域"
    OTHER = "other", "其他（非鳥類）"


# 供模板下拉選單使用（與 TextChoices.choices 同形：List[(value, label)]）
HABITAT_GROUP_CHOICES = HabitatGroup.choices


def _lower_set(names):
    """把屬名／目名清單正規化成小寫集合（去頭尾空白）。"""
    return {n.strip().lower() for n in names}


# ── 水域屬名表 ────────────────────────────────────────────────────
# 鍵一律小寫。學名第一個字即屬名，故此表可在「目」空白時仍正確分群。
AQUATIC_GENERA = _lower_set([
    # 雁形目 Anseriformes（雁鴨）
    "Anas", "Anser", "Aix", "Aythya", "Mareca", "Spatula", "Sibirionetta",
    "Tadorna", "Cygnus", "Bucephala", "Mergus", "Mergellus", "Nettapus",
    "Dendrocygna", "Branta", "Clangula", "Netta", "Histrionicus",
    # 鵜形目 Pelecaniformes（鷺科、䴉科、鵜鶘科）
    # 註：同屬鷺科的 Gorsachius（麻鷺屬）是林下型，已列入下方陸域例外表。
    "Ardea", "Egretta", "Bubulcus", "Ardeola", "Nycticorax", "Butorides",
    "Ixobrychus", "Botaurus", "Dupetor", "Platalea",
    "Threskiornis", "Plegadis", "Pelecanus", "Mesophoyx",
    # 鴴形目 Charadriiformes（鴴、鷸、鷗、燕鷗、燕鴴、彩鷸、水雉）
    "Charadrius", "Pluvialis", "Vanellus", "Tringa", "Calidris", "Actitis",
    "Numenius", "Limosa", "Gallinago", "Scolopax", "Arenaria", "Himantopus",
    "Recurvirostra", "Haematopus", "Larus", "Chroicocephalus", "Sterna",
    "Sternula", "Chlidonias", "Thalasseus", "Hydrophasianus", "Rostratula",
    "Glareola", "Phalaropus", "Xenus", "Limnodromus", "Lymnocryptes",
    "Gelochelidon", "Hydroprogne", "Onychoprion", "Anous",
    "Ibidorhyncha", "Esacus", "Burhinus", "Pluvianus", "Stercorarius",
    "Rissa", "Ichthyaetus",
    # 鸊鷉目 Podicipediformes
    "Tachybaptus", "Podiceps",
    # 鰹鳥目 Suliformes（鸕鷀、鰹鳥、軍艦鳥）
    "Phalacrocorax", "Microcarbo", "Sula", "Fregata",
    # 鸛形目 Ciconiiformes
    "Ciconia", "Mycteria", "Leptoptilos",
    # 鶴形目 Gruiformes（秧雞、鶴）
    "Rallus", "Gallinula", "Fulica", "Porzana", "Amaurornis", "Zapornia",
    "Gallicrex", "Grus", "Antigone", "Rallina", "Hypotaenidia", "Porphyrio",
    "Crex", "Lewinia",
    # 潛鳥目 Gaviiformes
    "Gavia",
    # 翠鳥科 Alcedinidae（分類上屬佛法僧目，該目整體並非水域，故只能在此以屬名指定）
    "Alcedo", "Halcyon", "Todiramphus", "Ceryle", "Megaceryle",
    "Pelargopsis", "Ceyx",
])


# ── 陸域例外屬名表 ────────────────────────────────────────────────
# 分類上隸屬水域類群、實際卻是陸域生活的屬；優先於水域屬名表與目級判斷。
TERRESTRIAL_GENERA = _lower_set([
    # 麻鷺屬（黑冠麻鷺）：科屬雖為鷺科／鵜形目，實際在林下與草地上覓食蚯蚓、
    # 於樹林繁殖，不依賴水域，故歸陸域。
    "Gorsachius",
])


# ── 水域目表（後援；「目」有填才用得上）───────────────────────────
# 中文與拉丁兩種寫法都收，鍵一律小寫。
# 註：佛法僧目（翠鳥所屬）刻意不列入——該目的蜂虎、佛法僧等皆為陸域，
#     翠鳥科只能靠上方屬名表指定。
AQUATIC_ORDERS = _lower_set([
    "Anseriformes", "雁形目",
    "Pelecaniformes", "鵜形目",
    "Charadriiformes", "鴴形目",
    "Podicipediformes", "鸊鷉目",
    "Suliformes", "鰹鳥目",
    "Ciconiiformes", "鸛形目",
    "Gruiformes", "鶴形目",
    "Gaviiformes", "潛鳥目",
    "Phoenicopteriformes", "紅鶴目",
])


def _genus_of(scientific_name):
    """取學名的屬名（第一個字），小寫化；空白學名回空字串。"""
    if not scientific_name:
        return ""
    return scientific_name.strip().split(" ", 1)[0].lower()


def habitat_group_of(species):
    """推導單一物種的棲域分群（回傳 HabitatGroup 的 value 字串）。

    判斷優先序見模組說明。刻意落入「陸域」預設的已知案例：
    - 黑鳶（Milvus）等在水面／河口覓食的猛禽——築巢棲息於樹上，且「猛禽一律陸域」
      規則單純、不必逐種判斷水域關聯的強弱。
    - 家燕（Hirundo）等燕科——常在水面上空捕食，但築巢於建物、屬空中覓食者。
    兩者若日後要改判水域，把屬名加進 AQUATIC_GENERA 即可。
    """
    # 非鳥類不適用水陸棲域分類（館藏中的蛇類、哺乳類）
    if species.taxon_group != BIRD:
        return HabitatGroup.OTHER.value

    genus = _genus_of(species.scientific_name)
    if genus in TERRESTRIAL_GENERA:
        return HabitatGroup.TERRESTRIAL.value
    if genus in AQUATIC_GENERA:
        return HabitatGroup.AQUATIC.value

    order = (species.order or "").strip().lower()
    if order in AQUATIC_ORDERS:
        return HabitatGroup.AQUATIC.value

    return HabitatGroup.TERRESTRIAL.value


def is_unverified(species):
    """是否「無從判斷、只能靠預設歸陸域」（供人工複核清查）。

    僅在三者皆成立時為真：是鳥類、屬名未收錄於任一對照表、且「目」欄位空白。
    若「目」已填但非水域目（例如雀形目），代表可據以確定為陸域，不算未判定。
    非鳥類有明確歸屬（其他），亦不算。
    """
    if species.taxon_group != BIRD:
        return False
    genus = _genus_of(species.scientific_name)
    if genus in TERRESTRIAL_GENERA or genus in AQUATIC_GENERA:
        return False
    return not (species.order or "").strip()
