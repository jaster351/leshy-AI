"""Normalize a pinned GO OBO file and generate immutable cellular-component facts.
Usage: python knowledge/updates/tools/generate_go_cellular_components.py SOURCE.obo
A later release must use a new manifest and version-aware review.
"""
import collections
import hashlib
import json
import re
import sys
from pathlib import Path
BASE=Path('knowledge/updates')
MANIFEST=BASE/'datasets/go-cellular-components-20260726.manifest.json'
def normalize(raw):
 text=raw.decode('utf-8');assert 'data-version: releases/2026-07-26\n' in text
 rows=[];obsolete=0
 for block in re.split(r'\n\[(?:Term|Typedef)\]\n',text)[1:]:
  d=collections.defaultdict(list)
  for line in block.splitlines():
   if ': ' in line:k,v=line.split(': ',1);d[k].append(v)
  if d['namespace']!=['cellular_component']:continue
  if d['is_obsolete']==['true']:obsolete+=1;continue
  assert len(d['id'])==len(d['name'])==len(d['def'])==1
  match=re.fullmatch(r'("(?:[^"\\]|\\.)*") (\[.*\])(?: .*)?',d['def'][0]);assert match,d['id']
  definition=json.loads(match.group(1))
  rows.append(dict(go_id=d['id'][0],name=d['name'][0],definition=definition,definition_xrefs=match.group(2),is_a=[x.split(' ! ')[0] for x in d['is_a']],relationships=[x.split(' ! ')[0] for x in d['relationship']],subsets=d['subset']))
 assert len(rows)==4077 and obsolete==523
 assert len({x['go_id'] for x in rows})==len(rows)
 return rows

def generate(source):
 m=json.loads(MANIFEST.read_text());raw=Path(source).read_bytes();assert hashlib.sha256(raw).hexdigest()==m['source_sha256']
 rows=normalize(raw);snap=BASE/'datasets/go-cellular-components-20260726.normalized.json';data=(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert hashlib.sha256(data).hexdigest()==m['normalized_sha256']
 if snap.exists() and snap.read_bytes()!=data:raise ValueError('Snapshot differs')
 snap.write_bytes(data);buf=b'';parts=[]
 for row in rows:
  if row['go_id'] in m.get('excluded_semantic_duplicates',{}):continue
  ident=row['go_id'];parents=', '.join(row['is_a']) or 'нет прямых is_a в снимке'
  r=dict(schema_version=1,id='go-cellular-component-'+ident[3:],version=1,language='ru-en',topic=f'{ident}: {row["name"]}',claim=f'В Gene Ontology releases/2026-07-26 термин {ident} ({row["name"]}) относится к cellular_component. Определение GO на языке источника: {row["definition"]} Прямые родительские классы is_a: {parents}.',category='biology',data_class='static',checked_at=m['checked_at'],review_status='verified',sources=[dict(url=m['source_url'],title='Gene Ontology go-basic OBO; releases/2026-07-26',source_family='gene-ontology-consortium',checked_at=m['checked_at'])],source_version='releases/2026-07-26',published_at=None,event_date=None,limitations='Исторический снимок онтологии, а не утверждение о текущей редакции. Классификация термина не доказывает наличие компонента у конкретного организма и не является аннотацией гена. Ссылки определения сохранены, но отдельные первичные статьи не проверены. is_a и part_of не смешиваются; отсутствие связи в снимке не означает её биологическую невозможность.',canonical_key=['gene-ontology',ident,'definition-and-direct-classification'],dataset_license='CC-BY-4.0',attribution='Gene Ontology Consortium; definitions and ontology relations reused under CC BY 4.0; Russian framing and JSON normalization added.',license_url=m['license_url'],dataset_manifest=str(MANIFEST),source_sha256=m['source_sha256'],ontology=row)
  line=(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert len(line)<2097152
  if len(buf)+len(line)>=2097152:parts.append(buf);buf=b''
  buf+=line
 if buf:parts.append(buf)
 for i,data in enumerate(parts,1):
  f=BASE/f'2026-11-13/0635-go-cellular-components-{i:02d}.jsonl'
  if f.exists() and f.read_bytes()!=data:raise ValueError('Existing version differs')
  f.write_bytes(data)
 return sum(r["go_id"] not in m.get("excluded_semantic_duplicates",{}) for r in rows)
if __name__=='__main__':print(generate(sys.argv[1]))
