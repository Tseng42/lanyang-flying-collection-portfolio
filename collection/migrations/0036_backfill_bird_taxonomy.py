"""資料回填：依人工核校過的對照表，補上鳥類物種的「目／科／屬」。

資料來源：鳥類目科屬對照表_20260807.csv（人工核校）。本遷移只做「補空」，
不覆蓋既有值，具冪等性，可安全重跑。

比對與寫入規則（保守、不臆測）：
- **以「學名」比對**（scientific_name，模型上為 unique）定位物種，非以主鍵，
  避免不同資料庫主鍵編排不一致造成誤寫。學名去除前後空白後做「精確比對」，
  不做模糊比對、不自動修正。查無相符學名者一律略過並記錄。
- **只填空欄位**：目／科／屬三欄各自獨立判斷，僅在該欄目前為空白時才寫入；
  已有值者保留、不覆蓋（視為他人已維護）。
- **已排除 6 列**（不納入下方對照資料）：信心註記為「低」或涉及「待查證／疑似
  重複建檔」者——66 鳳頭燕鷗（學名可能過時）、26 白腹秧雞、28 白冠雞、
  25 栗小鷺（屬中文名待查證）、16 臺灣擬啄木、17 五色鳥（疑似同種異名重複建檔）。

反向遷移設為 no-op：本遷移為「補空」，無法安全區分「原本就空」與「本遷移填入」，
故不提供自動回退（避免誤刪他人資料）。

註：對照資料直接內嵌於本檔，不在執行時讀取外部 CSV——正式部署環境（Render）
沒有該檔案，且遷移應自帶所需資料、可重現。
"""

from django.db import migrations

# (學名, 中文俗名, 目, 科, 屬)；中文俗名僅供 log 可讀，比對只用學名。
# 共 41 列（已排除上述 6 列低信心／疑似重複）。
TAXONOMY = [
    ('Accipiter gularis', '日本松雀鷹', '鷹形目', '鷹科', '鷹屬'),
    ('Accipiter trivirgatus', '鳳頭蒼鷹', '鷹形目', '鷹科', '鷹屬'),
    ('Spilornis cheela', '大冠鷲', '鷹形目', '鷹科', '蛇鵰屬'),
    ('Falco tinnunculus', '紅隼', '隼形目', '隼科', '隼屬'),
    ('Acridotheres tristis', '家八哥', '雀形目', '椋鳥科', '八哥屬'),
    ('Copsychus malabaricus', '白腰鵲鴝', '雀形目', '鶲科', '鵲鴝屬'),
    ('Dicrurus macrocercus', '大卷尾', '雀形目', '卷尾科', '卷尾屬'),
    ('Hypothymis azurea', '黑枕藍鶲', '雀形目', '王鶲科', '藍鶲屬'),
    ('Hypsipetes leucocephalus', '紅嘴黑鵯', '雀形目', '鵯科', '短腳鵯屬'),
    ('Lanius cristatus', '紅尾伯勞', '雀形目', '伯勞科', '伯勞屬'),
    ('Lanius schach', '棕背伯勞', '雀形目', '伯勞科', '伯勞屬'),
    ('Oriolus chinensis', '黃鸝', '雀形目', '黃鸝科', '黃鸝屬'),
    ('Oriolus traillii', '朱鸝', '雀形目', '黃鸝科', '黃鸝屬'),
    ('Passer montanus', '麻雀', '雀形目', '雀科', '麻雀屬'),
    ('Phoenicurus auroreus', '黃尾鴝', '雀形目', '鶲科', '紅尾鴝屬'),
    ('Prinia inornata', '褐頭鷦鶯', '雀形目', '扇尾鶯科', '鷦鶯屬'),
    ('Pycnonotus sinensis', '白頭翁', '雀形目', '鵯科', '鵯屬'),
    ('Turdus pallidus', '白腹鶇', '雀形目', '鶇科', '鶇屬'),
    ('Turdus poliocephalus', '白頭鶇', '雀形目', '鶇科', '鶇屬'),
    ('Urocissa caerulea', '臺灣藍鵲', '雀形目', '鴉科', '長尾山鵲屬'),
    ('Zoothera dauma', '小虎鶇', '雀形目', '鶇科', '虎鶇屬'),
    ('Horornis canturians', '遠東樹鶯', '雀形目', '樹鶯科', '樹鶯屬'),
    ('Hirundo rustica', '家燕', '雀形目', '燕科', '燕屬'),
    ('Actitis hypoleucos', '磯鷸', '鴴形目', '鷸科', '磯鷸屬'),
    ('Calidris acuminata', '尖尾鷸', '鴴形目', '鷸科', '濱鷸屬'),
    ('Gallinago gallinago', '田鷸', '鴴形目', '鷸科', '沙錐屬'),
    ('Gallinago stenura', '針尾鷸', '鴴形目', '鷸科', '沙錐屬'),
    ('Hydrophasianus chirurgus', '大水薙鳥', '鴴形目', '水雉科', '水雉屬'),
    ('Rostratula benghalensis', '彩鷸', '鴴形目', '彩鷸科', '彩鷸屬'),
    ('Vanellus vanellus', '小辮鴴', '鴴形目', '鴴科', '麥雞屬'),
    ('Gorsachius melanolophus', '黑冠麻鷺', '鵜形目', '鷺科', '麻鷺屬'),
    ('Egretta garzetta', '小白鷺', '鵜形目', '鷺科', '白鷺屬'),
    ('Nycticorax nycticorax', '夜鷺', '鵜形目', '鷺科', '夜鷺屬'),
    ('Alcedo atthis', '翠鳥', '佛法僧目', '翠鳥科', '翠鳥屬'),
    ('Chalcophaps indica indica', '翠翼鳩', '鴿形目', '鳩鴿科', '金鳩屬'),
    ('Treron sieboldii', '綠鳩', '鴿形目', '鳩鴿科', '綠鳩屬'),
    ('Streptopelia tranquebarica', '紅鳩', '鴿形目', '鳩鴿科', '斑鳩屬'),
    ('Otus bakkamoena', '領角鴞', '鴞形目', '鴟鴞科', '角鴞屬'),
    ('Otus spilocephalus', '黃嘴角鴞', '鴞形目', '鴟鴞科', '角鴞屬'),
    ('Bambusicola sonorivox', '竹雞', '雞形目', '雉科', '竹雞屬'),
    ('Tachybaptus ruficollis', '小鸊鷉', '鸊鷉目', '鸊鷉科', '小鸊鷉屬'),
]

LOG_PREFIX = "[bird-taxonomy-backfill]"


def backfill_bird_taxonomy(apps, schema_editor):
    Species = apps.get_model("collection", "species")

    filled = 0        # 有實際寫入（至少填了一個空欄位）的物種數
    field_writes = 0  # 實際寫入的欄位總數（目/科/屬分開計）
    not_found = 0     # 查無相符學名
    all_present = 0   # 找到但三欄皆已有值，無可填
    print(f"{LOG_PREFIX} 開始：對照表 {len(TAXONOMY)} 列（已排除低信心／疑似重複 6 列）")

    for sci, cn, order, family, genus in TAXONOMY:
        sp = Species.objects.filter(scientific_name=sci.strip()).first()
        if sp is None:
            not_found += 1
            print(f"{LOG_PREFIX} 略過（查無此學名）：{sci}（{cn}）")
            continue

        updates = {}
        for field, value in (("order", order), ("family", family), ("genus", genus)):
            if not (getattr(sp, field) or "").strip():   # 只填空欄位
                updates[field] = value

        if not updates:
            all_present += 1
            print(f"{LOG_PREFIX} 略過（目/科/屬已有值）：{sci}（{cn}）")
            continue

        for field, value in updates.items():
            setattr(sp, field, value)
        sp.save(update_fields=list(updates.keys()))
        filled += 1
        field_writes += len(updates)
        done = "／".join(f"{k}={v}" for k, v in updates.items())
        print(f"{LOG_PREFIX} 填入 {sci}（{cn}）：{done}")

    print(
        f"{LOG_PREFIX} 完成："
        f"寫入 {filled} 筆物種（共 {field_writes} 個欄位）、"
        f"查無學名 {not_found} 筆、三欄皆已有值略過 {all_present} 筆。"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0035_alter_species_scientific_name_and_more"),
    ]

    operations = [
        migrations.RunPython(
            backfill_bird_taxonomy, migrations.RunPython.noop
        ),
    ]
