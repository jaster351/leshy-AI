"""Reproduce DNS registry facts from a SHA-256-pinned IANA XML download."""
import json,hashlib,sys,re,collections
from pathlib import Path
from xml.etree import ElementTree as ET
B=Path('knowledge/updates');M=B/'datasets/iana-dns-20261113.manifest.json';NS={'i':'http://www.iana.org/assignments'}
def normalize(raw):
 raw.decode('utf-8');root=ET.fromstring(raw);rows=[]
 for reg in root.findall('.//i:registry',NS):
  title=reg.findtext('i:title',namespaces=NS)
  for el in reg.findall('i:record',NS):
   fields={};refs=[]
   for child in el:
    tag=child.tag.split('}')[-1]
    if tag in ('contact','controller','xref'):continue
    val=' '.join(''.join(child.itertext()).split()) or None
    fields[tag]=val
   for x in el.findall('i:xref',NS):
    if x.get('type')!='person':refs.append(dict(x.attrib))
   key=next((fields[k] for k in ('value','bit','number','version','prefix','scheme','name') if fields.get(k)),None);assert key is not None,(reg.get('id'),fields)
   if reg.get('id')=='underscored-globally-scoped-dns-node-names':key+=':'+str(fields.get('description'))
   # RCODE 16 has two context-specific mnemonics (TSIG and EDNS).
   if reg.get('id')=='dns-parameters-6':key+=':'+str(fields.get('name'))+':'+','.join(x.get('data','') for x in refs)
   if reg.get('id')=='server-cookie-methods':key+=':size='+str(fields.get('size'))
   rows.append(dict(registry=reg.get('id'),title=title,key=key,fields=fields,references=refs,registration_date=el.get('date'),source_updated=root.findtext('i:updated',namespaces=NS)))
 assert len({(r['registry'],r['key']) for r in rows})==len(rows)
 return rows

def generate(source):
 m=json.loads(M.read_text());raw=Path(source).read_bytes();assert hashlib.sha256(raw).hexdigest()==m['source_sha256'];rows=normalize(raw)
 normalized=(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert hashlib.sha256(normalized).hexdigest()==m['normalized_sha256'];(B/'datasets/iana-dns-20261113.normalized.json').write_bytes(normalized)
 parts=[];buf=b''
 for row in rows:
  key=[row['registry'],row['key']];rid='iana-dns-'+hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]
  details='; '.join(k+'='+str(v) for k,v in row['fields'].items() if v is not None)
  r=dict(schema_version=1,id=rid,version=1,language='ru-en',topic=row['title']+': '+row['key'],claim='В реестре IANA DNS Parameters, обновлённом '+row['source_updated']+', раздел '+row['title']+' содержит назначение: '+details+'.',category='technology',data_class='dynamic',ttl_seconds=604800,checked_at=m['checked_at'],review_status='verified',sources=[dict(url=m['source_url'],title='IANA Domain Name System Parameters XML',source_family='iana-protocol-registries',checked_at=m['checked_at'])],source_version=row['source_updated'],published_at=None,event_date=None,registration_date=row['registration_date'],canonical_key=['iana-dns',*key],dataset_license='CC0-1.0',license_url=m['license_url'],source_sha256=m['source_sha256'],dataset_manifest=str(M),registry_record=row,limitations='Проверено назначение в реестре, а не поддержка конкретным DNS-сервером. Reserved, Unassigned, obsolete и experimental не означают рекомендации к использованию. Диапазоны сохранены целиком. Ссылки RFC и проектов сохранены как метаданные; их полный текст отдельно не проверялся. Пустое поле означает отсутствие значения, не ноль. TTL семь дней выбран из-за изменяемости реестра.')
  line=(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert len(line)<2097152
  if len(buf)+len(line)>=2097152:parts.append(buf);buf=b''
  buf+=line
 if buf:parts.append(buf)
 for n,data in enumerate(parts,1):
  f=B/f'2026-11-13/1403-iana-dns-{n:02d}.jsonl';assert not f.exists() or f.read_bytes()==data;f.write_bytes(data)
 return len(rows)
if __name__=='__main__':print(generate(sys.argv[1]))
