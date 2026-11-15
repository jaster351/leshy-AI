"""Reproduce pinned HTTP registry packages from repository root (no app startup)."""
import csv
import hashlib
import io
import json
import re
from pathlib import Path
BASE=Path('knowledge/updates')
MANIFEST=BASE/'datasets/http-registries-20261113.manifest.json'
def generate():
 m=json.loads(MANIFEST.read_text(encoding='utf-8'));out=[]
 for s in m['sources']:
  raw=Path(s['snapshot']).read_bytes();assert hashlib.sha256(raw).hexdigest()==s['sha256']
  rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))));assert len(rows)==s['rows'];seen=set();recs=[];coverage=set()
  for row in rows:
   kind=s['key'];special=False
   if kind=='http-fields':
    entity=row['Field Name'];key=entity.lower();assert set(row)=={'Field Name','Status','Structured Type','Reference','Comments'}
    claim=f'В реестре IANA HTTP Field Name Registry поле «{entity}» имеет статус {row["Status"]}.'
    if row['Structured Type']:claim+=f' Structured Type: {row["Structured Type"]}.'
    if row['Comments']:claim+=f' Примечание реестра: {row["Comments"]}.'
    limitation='Реестровый статус не доказывает поддержку браузером/сервером или безопасность поля. Пустой Structured Type означает отсутствие значения в снимке, а не запрет структурированного синтаксиса. Звёздочка — зарезервированная запись, не обычное имя поля.'
   elif kind=='http-methods':
    entity=row['Method Name'];key=entity;assert set(row)=={'Method Name','Safe','Idempotent','Reference'}
    assert row['Safe'] in ('yes','no') and row['Idempotent'] in ('yes','no')
    claim=f'В реестре IANA HTTP Method Registry для «{entity}» указано Safe={row["Safe"]}, Idempotent={row["Idempotent"]}.'
    limitation='Safe и Idempotent — свойства семантики HTTP, не гарантия безопасности сервера или одинаковых ответов. Регистрация не подтверждает поддержку конкретным ПО; специальные токены PRI и * нельзя трактовать как обычные прикладные операции. Для выполнения и повторов нужны применимая спецификация и контекст.'
   else:
    assert set(row)=={'Value','Description','Reference'};entity=row['Value'];key=entity;lo,_,hi=entity.partition('-');lo=int(lo);hi=int(hi or lo);assert 100<=lo<=hi<=599 and not coverage.intersection(range(lo,hi+1));coverage.update(range(lo,hi+1))
    claim=f'В реестре IANA HTTP Status Codes для значения {entity} указано: «{row["Description"]}».'
    special=entity=='104'
    limitation='Назначение кода не устанавливает причину конкретного сбоя и не подтверждает поддержку ПО. Диапазоны Unassigned не развёрнуты в искусственные отдельные факты.'
    if special:limitation+=' В источнике указан срок временной регистрации 2026-11-13; продление или окончательное назначение не подтверждено. Это атрибуция строки снимка, не утверждение о действительности назначения после срока. Требуется перепроверка.'
   assert key not in seen;seen.add(key)
   ident='iana-'+kind+'-'+re.sub('[^a-z0-9.-]','-',key.lower())[:90]+'-'+hashlib.sha256(key.encode()).hexdigest()[:10]
   recs.append(dict(schema_version=1,id=ident,version=1,language='ru',topic=f'IANA {kind}: {entity}',claim=claim,category='technology',data_class='dynamic',checked_at=s['checked_at'],ttl_seconds=3600 if special else 604800,review_status='candidate' if special else 'verified',sources=[dict(url=s['url'],title='IANA '+kind+' CSV',source_family='iana-'+kind,checked_at=s['checked_at'])],source_version=s['source_version'],published_at=None,event_date=None,limitations=limitation+' TTL задаёт срок перепроверки, а не частоту изменения реестра.',canonical_key=['iana',kind,key,'registry-entry'],dataset_manifest=str(MANIFEST),dataset_license='CC0-1.0',source_sha256=s['sha256'],registry_fields={k:v or None for k,v in row.items()}))
  if kind=='http-status-codes':assert coverage==set(range(100,600))
  data=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in recs).encode('utf-8');assert len(data)<2097152
  f=BASE/f'2026-11-13/0603-{kind}.jsonl'
  if f.exists() and f.read_bytes()!=data:raise ValueError('Existing version differs')
  f.write_bytes(data);out.extend(recs)
 return out
if __name__=='__main__':print(len(generate()))
