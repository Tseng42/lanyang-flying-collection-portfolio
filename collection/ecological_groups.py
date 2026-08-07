"""查詢頁「簡易版」用的生態分群推導。

特展改為「飛羽生物」（僅展鳥類）後，原本的「分類群」（鳥／昆蟲／蝙蝠／飛鼠）
對所有紀錄都會是「鳥」，失去篩選作用。改以觀眾易懂的「生態分群」篩選：
猛禽／水鳥／鳴禽／陸禽／其他（特殊生態）。

實作限制與策略：
- 不新增／修改任何模型欄位，分群一律於查詢時「即時推導」，不落地儲存。
- 正式站的「目」欄位大多空白，故推導以「一定有填」的學名（屬名）為主錨點
  （`GENUS_TO_ECO`），已填的「目」為後援（`ORDER_TO_ECO`），皆無命中則歸「其他」。
- 生態分群面向觀眾、非嚴謹分類：燕科／翠鳥科／夜鷹科等雖分類上各有所屬，
  仍依策展意圖歸「其他（特殊生態）」，且因由屬名表指定，優先於目級判斷。

> 屬名表以館藏（鳥類、物種數有限）為範圍逐步補齊；未收錄且「目」亦空白者
> 會落入「其他」，可用 `list_unmapped_eco_species` 管理指令清查後補表或補「目」。
"""

from django.db import models


class EcoGroup(models.TextChoices):
    """生態分群（查詢頁簡易版篩選用）。value 僅供網址參數與內部比對。"""

    RAPTOR = "raptor", "猛禽類"
    WATERBIRD = "waterbird", "水鳥類"
    SONGBIRD = "songbird", "鳴禽類"
    LANDFOWL = "landfowl", "陸禽類"
    OTHER = "other", "其他／特殊生態"


# 供模板下拉選單使用（與 TextChoices.choices 同形：List[(value, label)]）
ECO_GROUP_CHOICES = EcoGroup.choices


# ── 屬名 → 生態分群 ────────────────────────────────────────────────
# 鍵一律小寫。學名第一個字即屬名，故此表可在「目」空白時仍正確分群。
# 「其他」段落包含策展上要獨立出來的特殊生態科（燕科／翠鳥科／夜鷹科…），
# 因其屬名在此指定，優先於下方 ORDER_TO_ECO 的目級判斷。
GENUS_TO_ECO = {}


def _register(group, genera):
    """把一串屬名登記到指定生態分群（小寫化，重複以後者為準）。"""
    for g in genera:
        GENUS_TO_ECO[g.strip().lower()] = group


# 猛禽類：鷹形目、鴞形目、隼形目
_register(EcoGroup.RAPTOR, [
    # 鷹形目 Accipitriformes
    "Milvus", "Accipiter", "Tachyspiza", "Butastur", "Buteo", "Circus",
    "Pernis", "Elanus", "Aquila", "Clanga", "Hieraaetus", "Nisaetus",
    "Spilornis", "Pandion", "Haliaeetus", "Ictinaetus", "Aviceda", "Gyps",
    "Aegypius", "Haliastur", "Circaetus",
    # 隼形目 Falconiformes
    "Falco", "Microhierax",
    # 鴞形目 Strigiformes
    "Otus", "Bubo", "Ketupa", "Strix", "Glaucidium", "Athene", "Asio",
    "Ninox", "Tyto", "Phodilus", "Surnia", "Aegolius",
])

# 水鳥類：雁形目、鵜形目（含鷺科）、鴴形目、鸊鷉目、鰹鳥目、鸛形目、鶴形目、潛鳥目
_register(EcoGroup.WATERBIRD, [
    # 雁形目 Anseriformes
    "Anas", "Anser", "Aix", "Aythya", "Mareca", "Spatula", "Sibirionetta",
    "Tadorna", "Cygnus", "Bucephala", "Mergus", "Mergellus", "Nettapus",
    "Dendrocygna", "Branta", "Clangula", "Netta", "Histrionicus",
    # 鵜形目 Pelecaniformes（含鷺科 Ardeidae、䴉科、鵜鶘科）
    "Ardea", "Egretta", "Bubulcus", "Ardeola", "Nycticorax", "Butorides",
    "Ixobrychus", "Gorsachius", "Botaurus", "Dupetor", "Platalea",
    "Threskiornis", "Plegadis", "Pelecanus", "Mesophoyx",
    # 鴴形目 Charadriiformes（鴴、鷸、鷗、燕鴴、彩鷸、水雉…）
    "Charadrius", "Pluvialis", "Vanellus", "Tringa", "Calidris", "Actitis",
    "Numenius", "Limosa", "Gallinago", "Scolopax", "Arenaria", "Himantopus",
    "Recurvirostra", "Haematopus", "Larus", "Chroicocephalus", "Sterna",
    "Sternula", "Chlidonias", "Thalasseus", "Hydrophasianus", "Rostratula",
    "Glareola", "Phalaropus", "Xenus", "Limnodromus", "Lymnocryptes",
    "Pluvialis", "Gelochelidon", "Hydroprogne", "Onychoprion", "Anous",
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
    "Crex", "Lewinia", "Porzana",
    # 潛鳥目 Gaviiformes
    "Gavia",
])

# 陸禽類：雞形目、鴿形目（鳩鴿）
_register(EcoGroup.LANDFOWL, [
    # 雞形目 Galliformes
    "Gallus", "Bambusicola", "Lophura", "Syrmaticus", "Arborophila",
    "Coturnix", "Phasianus", "Pavo", "Francolinus",
    # 鴿形目 Columbiformes
    "Columba", "Streptopelia", "Spilopelia", "Treron", "Ptilinopus",
    "Chalcophaps", "Geopelia", "Macropygia", "Phapitreron",
])

# 其他／特殊生態：策展上獨立出來的特殊生態科（不論分類歸屬）
# 燕科（分類屬雀形目）、翠鳥科（佛法僧目）、夜鷹科（夜鷹目）等。
_register(EcoGroup.OTHER, [
    # 燕科 Hirundinidae
    "Hirundo", "Delichon", "Cecropis", "Riparia", "Ptyonoprogne",
    "Petrochelidon",
    # 翠鳥科 Alcedinidae
    "Alcedo", "Halcyon", "Todiramphus", "Ceryle", "Megaceryle",
    "Pelargopsis", "Ceyx",
    # 夜鷹科 Caprimulgidae／蟆口鴟
    "Caprimulgus", "Lyncornis", "Batrachostomus",
])

# 鳴禽類：雀形目 Passeriformes。「目」空白時靠此表命中；未列入者靠 ORDER 後援。
# 註：燕科屬雀形目但已於上方「其他」登記（後登記者為準，故燕科維持「其他」）。
_register(EcoGroup.SONGBIRD, [
    "Passer", "Motacilla", "Anthus", "Dendronanthus", "Lanius", "Pica",
    "Corvus", "Garrulus", "Urocissa", "Dendrocitta", "Nucifraga",
    "Pycnonotus", "Hypsipetes", "Spizixos", "Ixos",
    "Cettia", "Horornis", "Phylloscopus", "Acrocephalus", "Locustella",
    "Cisticola", "Prinia", "Helopsaltes", "Urosphena", "Abroscopus",
    "Zosterops", "Yuhina", "Erpornis",
    "Sturnus", "Acridotheres", "Spodiopsar", "Gracupica", "Sturnia",
    "Agropsar", "Pastor",
    "Turdus", "Zoothera", "Geokichla",
    "Myophonus", "Monticola", "Luscinia", "Larvivora", "Tarsiger",
    "Phoenicurus", "Saxicola", "Muscicapa", "Ficedula", "Cyanoptila",
    "Niltava", "Copsychus", "Enicurus", "Calliope",
    "Emberiza", "Fringilla", "Carpodacus", "Chloris", "Spinus", "Eophona",
    "Pyrrhula", "Coccothraustes", "Haematospiza",
    "Parus", "Machlolophus", "Sittiparus", "Periparus", "Pardaliparus",
    "Aegithalos", "Sitta", "Certhia", "Troglodytes", "Cinclus", "Regulus",
    "Pnoepyga", "Alcippe", "Sinosuthora", "Suthora", "Fulvetta",
    "Liocichla", "Trochalopteron", "Garrulax", "Pterorhinus", "Ianthocincla",
    "Heterophasia", "Pomatorhinus", "Schoeniparus", "Timalia", "Actinodura",
    "Dicaeum", "Prunella", "Rhipidura", "Terpsiphone", "Hypothymis",
    "Culicicapa", "Dicrurus", "Pericrocotus", "Coracina", "Lalage",
    "Oriolus", "Artamus", "Aethopyga", "Cinnyris", "Chalcoparia",
    "Estrilda", "Lonchura", "Amandava", "Euodice",
    "Bombycilla", "Alauda", "Mirafra", "Eremophila", "Melanocorypha",
    "Chelidorhynx", "Cyornis", "Eumyias", "Chloropsis", "Aegithina",
    "Passerella",
])


# ── 目 → 生態分群（後援；「目」有填才用得上）─────────────────────
# 中文與拉丁兩種寫法都收，鍵一律小寫。
ORDER_TO_ECO = {
    # 猛禽類
    "accipitriformes": EcoGroup.RAPTOR, "鷹形目": EcoGroup.RAPTOR,
    "strigiformes": EcoGroup.RAPTOR, "鴞形目": EcoGroup.RAPTOR,
    "falconiformes": EcoGroup.RAPTOR, "隼形目": EcoGroup.RAPTOR,
    # 水鳥類
    "anseriformes": EcoGroup.WATERBIRD, "雁形目": EcoGroup.WATERBIRD,
    "pelecaniformes": EcoGroup.WATERBIRD, "鵜形目": EcoGroup.WATERBIRD,
    "charadriiformes": EcoGroup.WATERBIRD, "鴴形目": EcoGroup.WATERBIRD,
    "podicipediformes": EcoGroup.WATERBIRD, "鸊鷉目": EcoGroup.WATERBIRD,
    "suliformes": EcoGroup.WATERBIRD, "鰹鳥目": EcoGroup.WATERBIRD,
    "ciconiiformes": EcoGroup.WATERBIRD, "鸛形目": EcoGroup.WATERBIRD,
    "gruiformes": EcoGroup.WATERBIRD, "鶴形目": EcoGroup.WATERBIRD,
    "gaviiformes": EcoGroup.WATERBIRD, "潛鳥目": EcoGroup.WATERBIRD,
    "phoenicopteriformes": EcoGroup.WATERBIRD, "紅鶴目": EcoGroup.WATERBIRD,
    # 鳴禽類
    "passeriformes": EcoGroup.SONGBIRD, "雀形目": EcoGroup.SONGBIRD,
    # 陸禽類
    "galliformes": EcoGroup.LANDFOWL, "雞形目": EcoGroup.LANDFOWL,
    "columbiformes": EcoGroup.LANDFOWL, "鴿形目": EcoGroup.LANDFOWL,
}


def _genus_of(scientific_name):
    """取學名的屬名（第一個字），小寫化；空白學名回空字串。"""
    if not scientific_name:
        return ""
    return scientific_name.strip().split(" ", 1)[0].lower()


def eco_group_of(species):
    """推導單一物種的生態分群（回傳 EcoGroup 的 value 字串）。

    判斷優先序：屬名對照表 → 已填的「目」 → 其他。
    屬名表優先，故燕科／翠鳥科等特殊生態科即使「目」有填仍維持「其他」。
    """
    genus = _genus_of(species.scientific_name)
    if genus in GENUS_TO_ECO:
        return GENUS_TO_ECO[genus]
    order = (species.order or "").strip().lower()
    if order in ORDER_TO_ECO:
        return ORDER_TO_ECO[order]
    return EcoGroup.OTHER.value


def is_unmapped(species):
    """是否「屬名與目皆未命中」而只能落入其他（供清查未對應物種）。

    注意：夜鷹／翠鳥／燕科等是「有意」歸其他（屬名已收錄），不算未對應。
    """
    genus = _genus_of(species.scientific_name)
    if genus in GENUS_TO_ECO:
        return False
    order = (species.order or "").strip().lower()
    return order not in ORDER_TO_ECO
