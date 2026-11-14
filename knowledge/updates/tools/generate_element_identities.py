"""Reproduce factual element identities; no contributed descriptions or property tables."""
from pathlib import Path
import json,hashlib,sys,re
B=Path('knowledge/updates');M=B/'datasets/pubchem-element-identities-20261114.manifest.json'
def generate(source):
 m=json.loads(M.read_text());raw=Path(source).read_bytes();assert hashlib.sha256(raw).hexdigest()==m['source_sha256'];table=json.loads(raw.decode('utf-8'))['Table'];cols=table['Columns']['Column'];rows=[]
 for row in table['Row']:
  assert len(row['Cell'])==len(cols);d=dict(zip(cols,row['Cell']));z=int(d['AtomicNumber']);sym=d['Symbol'];name=d['Name'];assert re.fullmatch('[A-Z][a-z]?',sym);assert re.fullmatch('[A-Za-z]+',name)
  r=dict(schema_version=1,id=f'chemical-element-identity-{z}',version=1,topic=f'Химический элемент {sym}: {name}',claim=f'Химический элемент с атомным номером {z} имеет символ {sym} и английское название {name}.',category='chemistry',language='ru-en',data_class='static',checked_at=m['checked_at'],review_status='verified',sources=[dict(url=m['source_url'],title='PubChem Periodic Table PUG REST',source_family='ncbi-pubchem',checked_at=m['checked_at'])],canonical_key=['chemical-element',z,'identity'],atomic_number=z,symbol=sym,english_name=name,source_version='snapshot-sha256:'+m['source_sha256'],published_at=None,event_date=None,dataset_manifest=str(M),rights_basis='Самостоятельное изложение общеизвестных фактических идентификаторов; охраняемые описания и таблицы свойств сторонних поставщиков не копируются.',terms_url=m['terms_url'],limitations='Название приведено на английском в написании PubChem; не русское название и не перевод. Атомный номер не является атомной массой. Изотопные массы, электронные конфигурации, состояния и экспериментальные свойства в эту запись не включены.')
  rows.append(r)
 assert [r['atomic_number'] for r in rows]==list(range(1,119));assert len({r['symbol'] for r in rows})==118
 data=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows).encode();assert len(data)<2097152;p=B/'2026-11-14/0057-element-identities.jsonl';assert not p.exists() or p.read_bytes()==data;p.write_bytes(data);return len(rows)
if __name__=='__main__':print(generate(sys.argv[1]))
