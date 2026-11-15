"""Reproduce: python generate_charsets.py source.xml checked_at output.jsonl"""
import json,sys,xml.etree.ElementTree as E
from pathlib import Path
URL='https://www.iana.org/assignments/character-sets/character-sets.xml'
def generate(path,checked):
 root=E.fromstring(Path(path).read_bytes().decode('utf-8'));ns={'i':'http://www.iana.org/assignments'};edition=root.findtext('i:updated',namespaces=ns)
 for item in root.findall('.//i:record',ns):
  name=item.findtext('i:name',namespaces=ns);value=int(item.findtext('i:value',namespaces=ns));preferred=item.findtext('i:preferred_alias',namespaces=ns);aliases=[a.text for a in item.findall('i:alias',ns)];assert name and all(isinstance(a,str) for a in aliases)
  yield dict(schema_version=1,id=f'iana-charset-mibenum-{value}',version=1,topic='Кодировка IANA: '+name,claim=f'В реестре IANA Character Sets редакции {edition} имя {name} имеет MIBenum {value}.'+(f' Предпочтительное имя MIME: {preferred}.' if preferred else ' Предпочтительное имя MIME в записи не указано.')+(' Зарегистрированные псевдонимы: '+', '.join(aliases)+'.' if aliases else ' Псевдонимы в записи не перечислены.'),category='technology',language='ru-en',data_class='static',checked_at=checked,review_status='verified',sources=[dict(url=URL,title='IANA Character Sets',source_family='iana-character-sets')],canonical_key=['iana-character-sets',value,'identity-aliases'],mibenum=value,name=name,preferred_mime_name=preferred,aliases=aliases,source_version=edition,published_at=None,event_date=None,license='CC0-1.0',terms_url='https://www.iana.org/help/licensing-terms',limitations='Снимок реестра, не таблица преобразования байтов. Имена регистронезависимы. Наличие записи не гарантирует поддержку в конкретной библиотеке и не определяет кодировку неизвестного файла. Пустое поле не заменено предположением.',dataset_manifest='knowledge/updates/datasets/iana-charsets-20261114.manifest.json')
if __name__=='__main__':
 rows=list(generate(sys.argv[1],sys.argv[2]));assert len({r['id'] for r in rows})==len(rows)
 Path(sys.argv[3]).write_text(''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows),encoding='utf-8')
