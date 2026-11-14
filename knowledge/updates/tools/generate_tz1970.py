"""Reproduce: python generate_tz1970.py archive checked_at output.jsonl"""
import hashlib,json,re,sys,tarfile
from pathlib import Path

def generate(archive,checked):
    with tarfile.open(archive) as t:
        version=t.extractfile('version').read().decode('utf-8').strip()
        raw=t.extractfile('zone1970.tab').read()
    for line in raw.decode('utf-8').splitlines():
        if not line or line.startswith('#'): continue
        parts=line.split('\t'); assert len(parts) in (3,4)
        countries,coords,zone=parts[:3]; comment=parts[3] if len(parts)==4 else None
        assert re.fullmatch(r'[A-Z]{2}(,[A-Z]{2})*',countries)
        assert re.fullmatch(r'[+-]\d{4}(\d{2})?[+-]\d{5}(\d{2})?',coords)
        key=['iana-tzdb',zone,'zone1970-geographic-selection']
        yield dict(schema_version=1,id='iana-tz1970-'+hashlib.sha256(zone.encode()).hexdigest()[:24],version=1,topic='Часовая зона '+zone,claim=f'В таблице zone1970.tab IANA tzdb {version} зона {zone} сопоставлена кодам стран {countries}; координаты её главного представительного места: {coords} (ISO 6709, широта затем долгота).'+(f' Примечание области выбора в источнике: {comment}.' if comment else ''),category='geography',language='ru',data_class='static',checked_at=checked,review_status='verified',sources=[dict(url=f'https://data.iana.org/time-zones/releases/tzdata{version}.tar.gz',title='IANA tzdb '+version+' / zone1970.tab',source_family='iana-tzdb')],canonical_key=key,source_version=version,country_codes=countries.split(','),principal_coordinates_iso6709=coords,zone=zone,selection_comment=comment,published_at=None,event_date=None,license='Public domain; zone1970.tab header and archive LICENSE',limitations='Факт о фиксированной редакции. Строка объединяет места с совпадающими гражданскими временными метками с 1970 года. Координаты относятся к представительному месту, а не границе зоны. Коды не означают покрытие всей страны. Не устанавливает текущее смещение UTC и не выражает позицию о территориальных притязаниях.',dataset_manifest='knowledge/updates/datasets/iana-tz1970-2026f.manifest.json')
if __name__=='__main__':
    rows=list(generate(sys.argv[1],sys.argv[2]));assert len({r['zone'] for r in rows})==len(rows)
    Path(sys.argv[3]).write_text(''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows),encoding='utf-8')
