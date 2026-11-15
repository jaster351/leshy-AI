"""Reproduce TLS registry records from a pinned IANA XML; preserve ranges and statuses."""
import json,hashlib,sys,collections
from pathlib import Path
from xml.etree import ElementTree as ET
B=Path('knowledge/updates');M=B/'datasets/iana-tls-extensions-20261114.manifest.json';NS={'i':'http://www.iana.org/assignments'}
def normalize(raw):
 raw.decode('utf-8');root=ET.fromstring(raw);rows=[];count=0
 for reg in root.findall('.//i:registry',NS):
  title=reg.findtext('i:title',namespaces=NS);groups={}
  notes=[' '.join(''.join(n.itertext()).split()) for n in reg.findall('i:note',NS)]
  notes=[n for n in notes if '@' not in n]
  for el in reg.findall('i:record',NS):
   count+=1;fields={};refs=[]
   for child in el:
    tag=child.tag.split('}')[-1]
    if tag in ('contact','controller','xref'):continue
    assert tag not in fields
    fields[tag]=' '.join(''.join(child.itertext()).split()) or None
   for x in el.findall('.//i:xref',NS):
    if x.get('type')!='person':refs.append(dict(x.attrib))
   if reg.get('id')=='alpn-protocol-ids':fields['value'],fields['description']=fields['description'],fields['value']
   value=fields.pop('value',None);desc=fields.get('description') or fields.get('name');assert desc
   # Only identical registry meanings/status/comments are grouped; value ranges remain literal.
   group=json.dumps(fields,ensure_ascii=False,sort_keys=True)
   if group not in groups:groups[group]=dict(registry=reg.get('id'),title=title,values=[],fields=fields,references=[],registry_notes=notes,source_updated=root.findtext('i:updated',namespaces=NS))
   row=groups[group]
   if value is not None:row['values'].append(value)
   for ref in refs:
    if ref not in row['references']:row['references'].append(ref)
  rows.extend(groups.values())
 assert count==186
 return rows

def generate(source):
 m=json.loads(M.read_text());raw=Path(source).read_bytes();assert hashlib.sha256(raw).hexdigest()==m['source_sha256'];rows=normalize(raw)
 data=(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert hashlib.sha256(data).hexdigest()==m['normalized_sha256'];(B/'datasets/iana-tls-extensions-20261114.normalized.json').write_bytes(data)
 parts=[];buf=b''
 for row in rows:
  key=[row['registry'],row['values'] or (row['fields'].get('description') or row['fields']['name'])];rid='iana-tls-extension-'+hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()[:24]
  details='; '.join(k+'='+str(v) for k,v in row['fields'].items() if v is not None);values=', '.join(row['values']) or 'текстовая метка, числовой код не задан'
  r=dict(schema_version=1,id=rid,version=1,language='ru-en',topic=row['title']+': '+(row['fields'].get('description') or row['fields']['name']),claim='В реестре IANA TLS Extensions редакции '+row['source_updated']+' раздел '+row['title']+' содержит: '+details+'. Значения: '+values+'.',category='technology',data_class='dynamic',ttl_seconds=604800,checked_at=m['checked_at'],review_status='verified',sources=[dict(url=m['source_url'],title='IANA Transport Layer Security Extensions XML',source_family='iana-protocol-registries',checked_at=m['checked_at'])],source_version=row['source_updated'],published_at=None,event_date=None,canonical_key=['iana-tls-extensions',*key],dataset_license='CC0-1.0',license_url=m['license_url'],source_sha256=m['source_sha256'],dataset_manifest=str(M),registry_record=row,limitations='Назначение идентификатора не доказывает поддержку сервером или безопасность настройки. Recommended=N не обязательно означает дефект; D означает discouraged с условиями из спецификации. Поле tls13 задаёт допустимые сообщения TLS 1.3, dtls-only — ограничение DTLS; пояснения и исключения сохранены в registry_notes. ALPN — идентификатор протокола, не доказательство поддержки сервером. Reserved и Unassigned не являются рабочими алгоритмами. Диапазоны сохранены, отсутствие значения не превращено в ноль. Ссылки RFC сохранены как метаданные, полный текст всех спецификаций не проверялся. TTL семь дней обусловлен изменяемостью реестра.')
  line=(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert len(line)<2097152
  if len(buf)+len(line)>=2097152:parts.append(buf);buf=b''
  buf+=line
 if buf:parts.append(buf)
 for n,data in enumerate(parts,1):
  f=B/f'2026-11-14/0002-iana-tls-extensions-{n:02d}.jsonl';assert not f.exists() or f.read_bytes()==data;f.write_bytes(data)
 return len(rows)
if __name__=='__main__':print(generate(sys.argv[1]))
