"""Reproduce the discovery-only NASA PS snapshot; no app imports or network calls.

Run from repository root:
python knowledge/updates/tools/generate_nea_discovery_snapshot.py
Existing published id/version pairs are never rewritten by this generator.
"""
import csv
import hashlib
import html
import json
import re
from pathlib import Path

BASE = Path('knowledge/updates')
MANIFEST = BASE / 'datasets/nea-discovery-20261112T150210.manifest.json'

def stable_id(name):
    slug = re.sub(r'[^a-z0-9]+', '-', name.casefold()).strip('-')[:90] or 'planet'
    return 'nea-ps-' + slug + '-' + hashlib.sha256(name.encode()).hexdigest()[:12]

def generate():
    m = json.loads(MANIFEST.read_text())
    raw = Path(m['snapshot']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == m['source']['sha256']
    rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
    assert len(rows) == m['source_records']
    assert len({r['pl_name'].casefold() for r in rows}) == len(rows)
    records = []
    for n, r in enumerate(rows, 2):
        assert all(r[k] for k in ('pl_name','hostname','discoverymethod','disc_year','disc_facility','soltype'))
        assert r['pl_controv_flag'] in ('0','1')
        year = int(r['disc_year'])
        assert 1900 <= year <= 2026
        disputed = r['pl_controv_flag'] == '1'
        title = html.unescape(re.sub('<[^>]+>', '', r['pl_refname']))
        match = re.search(r'href=[\"\']?([^\s\"\'>]+)',r['pl_refname'])
        refurl = match.group(1) if match else None
        record = dict(schema_version=1,id=stable_id(r['pl_name']),version=1,language='ru',
            topic='NASA PS: '+r['pl_name'],
            claim=f"В снимке NASA Exoplanet Archive PS объект «{r['pl_name']}» связан с хозяином «{r['hostname']}». Метод обнаружения: {r['discoverymethod']}; год: {year}; установка: {r['disc_facility']}. Статус выбранного набора параметров: {r['soltype']}. Оспаривание подтверждения в литературе отмечено архивом: {'да' if disputed else 'нет'}.",
            category='astronomy',data_class='dynamic',checked_at=m['source']['checked_at'],ttl_seconds=604800,
            review_status='candidate' if disputed or r['soltype']!='Published Confirmed' else 'verified',
            sources=[dict(url=m['source']['url'],title='NASA Exoplanet Archive PS discovery snapshot',source_family='nasa-exoplanet-archive',checked_at=m['source']['checked_at'])],
            event_date=None,published_at=None,source_version='PS default_flag=1 snapshot 2026-11-12T15:02:10Z',
            limitations='Атрибуция записи архиву, а не независимое подтверждение открытия. soltype относится к выбранному набору параметров. Отсутствие флага спора не доказывает отсутствие любых возражений. Физические параметры и их погрешности не импортированы. TTL 7 суток отражает обычное еженедельное обновление архива; повторный импорт не продлевает срок.',
            dataset='nea-discovery-20261112T150210',dataset_key=r['pl_name'],dataset_manifest=str(MANIFEST),source_row=n,
            canonical_key=['nasa-exoplanet-archive','ps',r['pl_name'].casefold(),'discovery-and-archive-disposition'],
            source_sha256=m['source']['sha256'],usage_terms_url='https://exoplanetarchive.ipac.caltech.edu/docs/acknowledge.html',
            archive_fields={k:(v or None) for k,v in r.items() if k!='pl_refname'},
            literature_reference=dict(title=title,url=refurl,verification='linked by archive; individual paper not opened'),
            date_semantics=dict(published_at='unknown for this aggregate claim',pl_pubdate='publication of selected parameter set; precision preserved verbatim in archive_fields',rowupdate='last update of planet parameters',releasedate='archive release of selected parameter set'))
        records.append(record)
    chunks=[]; current=[]; size=0
    for r in records:
        b=(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        if size+len(b)>=1900000:
            chunks.append(b''.join(current));current=[];size=0
        current.append(b);size+=len(b)
    if current:chunks.append(b''.join(current))
    outputs=[]
    for i,b in enumerate(chunks,1):
        p=BASE/f'2026-11-12/2000-nea-discovery-part-{i:02}.jsonl'
        if p.exists() and p.read_bytes()!=b:raise ValueError('Refusing to rewrite existing record versions')
        p.write_bytes(b)
        outputs.append(dict(path=str(p),records=len(b.splitlines()),bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
    return records,outputs

if __name__=='__main__':
    records,outputs=generate()
    print(json.dumps(outputs,indent=2))
