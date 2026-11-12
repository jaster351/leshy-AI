"""Reproduce the pinned IANA protocol registry snapshot from repository root."""
import csv
import hashlib
import json
from pathlib import Path

BASE=Path('knowledge/updates')
MANIFEST=BASE/'datasets/iana-protocol-numbers-20261112.manifest.json'
OUTPUT=BASE/'2026-11-12/2358-iana-protocol-numbers.jsonl'

def generate():
    m=json.loads(MANIFEST.read_text())
    raw=Path(m['snapshot']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==m['source']['sha256']
    rows=list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
    assert len(rows)==152
    records=[]
    seen=set()
    for row in rows:
        decimal=row['Decimal']
        if decimal=='254':
            previous=next(r for r in rows if r['Decimal']=='253')
            assert all(row[k]==previous[k] for k in row if k!='Decimal')
            continue
        if decimal=='253':decimal='253-254'
        lo,_,hi=decimal.partition('-');lo=int(lo);hi=int(hi or lo)
        assert 0<=lo<=hi<=255
        assert not seen.intersection(range(lo,hi+1));seen.update(range(lo,hi+1))
        keyword=row['Keyword'] or None;label=row['Protocol'] or keyword
        assert label
        ext={'Y':True,'N':False,'':None}[row['IPv6 Extension Header']]
        suffix=(' Отметка IPv6 Extension Header: '+('да.' if ext else 'нет.')) if ext is not None else ''
        records.append(dict(schema_version=1,id='iana-ip-protocol-'+decimal,version=1,language='ru',
            topic='IP Protocol / IPv6 Next Header: '+decimal,
            claim=f'В реестре IANA Protocol Numbers редакции 2026-03-09 значение {decimal} обозначено как «{label}»'+(f'; ключевое слово — {keyword}.' if keyword else '.')+suffix,
            category='technology',data_class='dynamic',checked_at=m['source']['checked_at'],ttl_seconds=604800,review_status='verified',
            sources=[dict(url=m['source']['url'],title='IANA Protocol Numbers CSV',source_family='iana-protocol-numbers',checked_at=m['source']['checked_at'])],
            source_version='IANA registry last updated 2026-03-09',published_at=None,event_date=None,
            limitations='Номер поля Protocol/Next Header не является номером TCP/UDP-порта. Реестровое назначение не доказывает использование, поддержку или безопасность протокола. Пустая отметка расширения сохранена как null, а не false. TTL 7 суток — консервативный срок редакторской перепроверки, не обещание частоты обновлений IANA.',
            canonical_key=['iana','protocol-numbers',decimal,'assignment'],dataset_manifest=str(MANIFEST),dataset_license='CC0-1.0',source_sha256=m['source']['sha256'],
            protocol_number=dict(first=lo,last=hi,keyword=keyword,description=row['Protocol'] or None,ipv6_extension_header=ext,registry_references=row['Reference'] or None)))
    assert seen==set(range(256))
    data=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in records).encode()
    assert len(data)<2*1024*1024
    if OUTPUT.exists() and OUTPUT.read_bytes()!=data:raise ValueError('Refusing to overwrite an existing version')
    OUTPUT.write_bytes(data)
    return records

if __name__=='__main__':
    print(len(generate()))
