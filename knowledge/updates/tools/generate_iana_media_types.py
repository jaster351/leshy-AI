"""Reproduce immutable packages from the sanitized pinned IANA snapshot.
Run from repository root; no network or application imports.
"""
import hashlib
import json
import re
from pathlib import Path

BASE=Path('knowledge/updates')
MANIFEST=BASE/'datasets/iana-media-types-20261112.manifest.json'

def generate():
    m=json.loads(MANIFEST.read_text());raw=Path(m['snapshot']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==m['snapshot_sha256']
    rows=json.loads(raw);assert len(rows)==m['source_rows']
    seen=set();parts=[];buf=b'';records=[]
    for row in rows:
        mt=row['media_type'];key=mt.lower()
        assert re.fullmatch(r'[a-z]+/[A-Za-z0-9!#$&^_.+\-]+',mt) and key not in seen
        seen.add(key)
        ident='iana-media-'+re.sub('[^a-z0-9.-]','-',key)[:120]+'-'+hashlib.sha256(key.encode()).hexdigest()[:12]
        record=dict(schema_version=1,id=ident,version=1,language='ru',topic='Медиатип IANA: '+mt,
            claim=f'В реестре IANA Media Types редакции {m["source"]["registry_updated"]} медиатип {mt} находится в разделе {mt.split("/")[0]}; реестровая подпись: «{row["registry_label"]}».',
            category='technology',data_class='dynamic',checked_at=m['source']['checked_at'],ttl_seconds=604800,review_status='verified',
            sources=[dict(url=m['source']['url'],title='IANA Media Types registry XML',source_family='iana-media-types',checked_at=m['source']['checked_at'])],
            source_version=m['source']['registry_updated'],published_at=None,event_date=None,
            limitations='Реестровое наличие не означает рекомендацию, поддержку приложением или безопасность содержимого. Пометки OBSOLETE/OBSOLETED/DEPRECATED сохранены дословно; отсутствие пометки не является проверкой безопасности. Параметры, расширения файлов и правила обработки из регистрации здесь не проверены. Даты реестровых записей не объявляются датами публикации спецификаций. TTL 7 суток — срок перепроверки снимка.',
            canonical_key=['iana','media-types',key,'registry-assignment'],dataset_manifest=str(MANIFEST),dataset_license='CC0-1.0',source_sha256=m['source']['sha256'],media_type=row)
        line=(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        if len(buf)+len(line)>=2*1024*1024:parts.append(buf);buf=b''
        buf+=line;records.append(record)
    if buf:parts.append(buf)
    for n,data in enumerate(parts,1):
        out=BASE/f'2026-11-12/2006-iana-media-types-part-{n:02d}.jsonl'
        if out.exists() and out.read_bytes()!=data:raise ValueError('Refusing to mutate an existing id/version')
        out.write_bytes(data)
    return records

if __name__=='__main__':print(len(generate()))
