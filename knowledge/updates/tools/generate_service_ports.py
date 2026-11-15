"""Reproduce named IANA service/port assertions from a hash-pinned UTF-8 CSV."""
import csv,io,json,hashlib,sys
from pathlib import Path
B=Path('knowledge/updates');M=B/'datasets/iana-service-ports-20261114.manifest.json'
def normalize(raw):
 rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))));groups={}
 for row in rows:
  name=row['Service Name'].strip();port=row['Port Number'].strip()
  if not name:continue
  if port:
   for n in port.split('-'):assert n.isdigit() and 0<=int(n)<=65535
  key=(name,port);g=groups.setdefault(key,dict(service=name,port=port or None,assignments=[]))
  a={k:(' '.join(row[k].split()) or None) for k in ['Transport Protocol','Description','Registration Date','Modification Date','Service Code']}
  assert a['Transport Protocol'] in [None,'tcp','udp','sctp','dccp']
  if a not in g['assignments']:g['assignments'].append(a)
 return rows,list(groups.values())
def generate(source):
 m=json.loads(M.read_text());raw=Path(source).read_bytes();assert hashlib.sha256(raw).hexdigest()==m['source_sha256'];rows,groups=normalize(raw);assert len(rows)==m['source_rows']
 parts=[];buf=b'';count=0
 for g in groups:
  key=['iana-service-port',g['service'],g['port']];rid='iana-service-port-'+hashlib.sha256(json.dumps(key,sort_keys=True).encode()).hexdigest()[:24]
  detail='; '.join((a['Transport Protocol'] or 'transport unspecified')+': '+str(a['Description']) for a in g['assignments'])
  r=dict(schema_version=1,id=rid,version=1,language='ru-en',topic='IANA service '+g['service'],claim='В снимке реестра IANA '+m['checked_at']+' служба '+g['service']+' зарегистрирована '+('для порта '+g['port'] if g['port'] else 'без назначенного номера порта')+'. Назначения: '+detail+'.',category='technology',data_class='dynamic',ttl_seconds=604800,checked_at=m['checked_at'],review_status='verified',sources=[dict(url=m['source_url'],title='IANA Service Name and Transport Protocol Port Number Registry',source_family='iana-protocol-registries',checked_at=m['checked_at'])],canonical_key=key,source_version='snapshot-sha256:'+m['source_sha256'],published_at=None,event_date=None,dataset_license='CC0-1.0',license_url=m['license_url'],dataset_manifest=str(M),registry_record=g,limitations='Это регистрационное назначение, а не подтверждение работающей службы, открытого порта, безопасности трафика или ОС. Исторические назначения и описания сохраняются без рекомендации использовать их. Условия реализации следует проверять по спецификации. Контакты, владельцы и свободные примечания не переносятся; эта запись описывает только имя, порт, транспорт и назначение. Пустой порт означает отсутствие номера, не ноль. TTL 7 дней: реестр изменяемый.')
  line=(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert len(line)<2097152
  if len(buf)+len(line)>=2097152:parts.append(buf);buf=b''
  buf+=line;count+=1
 if buf:parts.append(buf)
 for i,raw in enumerate(parts,1):
  p=B/f'2026-11-14/0019-service-ports-{i:02d}.jsonl';assert not p.exists() or p.read_bytes()==raw;p.write_bytes(raw)
 return count
if __name__=='__main__':print(generate(sys.argv[1]))
