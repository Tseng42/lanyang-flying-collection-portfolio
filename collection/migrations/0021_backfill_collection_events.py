"""為既有標本回填採集事件，並連結 Specimen.collection_event。

- 逐筆走訪標本；以（採集地點＋緯度＋經度＋採集日期＋採集者）為鍵去重，
  相同鍵共用同一個採集事件（順帶去重既有資料）。
- 五個採集欄位全為空／NULL 的標本 → 不建立事件，collection_event 留 None。
- 具冪等性：已連結事件的標本略過（可安全重跑）。
- Specimen 舊採集欄位原封保留（僅複製到事件），故資料不會遺失。
- reverse 為 noop：不刪除、不改動任何資料，避免造成資料遺失。
"""

from django.db import migrations


def backfill_events(apps, schema_editor):
    Specimen = apps.get_model("collection", "Specimen")
    CollectionEvent = apps.get_model("collection", "CollectionEvent")
    db = schema_editor.connection.alias

    cache = {}  # (loc, lat, lon, date, collector) -> CollectionEvent
    qs = Specimen.objects.using(db).all()
    for sp in qs.iterator():
        if sp.collection_event_id:            # 已連結 → 冪等略過
            continue
        loc = sp.collection_location or ""
        lat, lon, date = sp.latitude, sp.longitude, sp.collection_date
        collector = sp.collector or ""
        # 五欄全空 → 不建事件
        if not loc and lat is None and lon is None and date is None and not collector:
            continue
        key = (loc, str(lat), str(lon), str(date), collector)
        event = cache.get(key)
        if event is None:
            event = CollectionEvent.objects.using(db).create(
                collection_location=loc, latitude=lat, longitude=lon,
                collection_date=date, collector=collector,
            )
            cache[key] = event
        sp.collection_event = event
        sp.save(update_fields=["collection_event"])


def noop_reverse(apps, schema_editor):
    # 不刪除事件、不解除關聯：Specimen 舊採集欄位仍在，回退不損失來源資料。
    # （0020 反向會移除 collection_event 欄位與 CollectionEvent 表，屬 schema 回退。）
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("collection", "0020_collectionevent_specimen_collection_event"),
    ]

    operations = [
        migrations.RunPython(backfill_events, noop_reverse),
    ]
