"""Generate factual identifier mappings; no license texts. Args: licenses exceptions checked_at output."""
import json,hashlib,sys
from pathlib import Path
COMMIT='31846481de7ea17d92f2f46fad76abe58b2e9bb0'
def generate(paths,checked):
 for path,kind,array,idfield in zip(paths,['license','exception'],['licenses','exceptions'],['licenseId','licenseExceptionId']):
  data=json.loads(Path(path).read_text(encoding='utf-8'))
  for item in data[array]:
   code=item[idfield]; name=item['name']; deprecated=item['isDeprecatedLicenseId'];assert isinstance(deprecated,bool)
   yield dict(schema_version=1,id='spdx-'+kind+'-'+hashlib.sha256(code.casefold().encode()).hexdigest()[:24],version=1,topic='SPDX '+code,claim=f'В снимке SPDX {COMMIT[:12]} идентификатор {code} обозначает '+('лицензию' if kind=='license' else 'лицензионное исключение')+f' «{name}». Идентификатор '+('помечен устаревшим.' if deprecated else 'не помечен устаревшим.'),category='technology',language='ru-en',data_class='static',checked_at=checked,review_status='verified',sources=[dict(url=f'https://raw.githubusercontent.com/spdx/license-list-data/{COMMIT}/json/{array}.json',title='SPDX '+array+' registry',source_family='spdx-license-list')],canonical_key=['spdx',kind,code.casefold(),'registry-identity'],spdx_identifier=code,registry_kind=kind,english_name=name,is_deprecated=deprecated,source_version=COMMIT,source_list_version=data['licenseListVersion'],published_at=data['releaseDate'],event_date=None,rights_basis='Самостоятельные формулировки фактических соответствий идентификаторов, названий и реестрового статуса. Тексты лицензий и охраняемые пояснения не воспроизводятся. Официальный README разрешает потребление опубликованных форматов; общая лицензия CC0 не предполагается.',terms_url='https://github.com/spdx/license-list-XML#consuming-license-data-from-this-repository',limitations='Факт относится к указанному снимку, не к текущему статусу. Наличие в SPDX не означает совместимость лицензий, разрешение конкретного использования или одобрение OSI. Исключение не является самостоятельной лицензией. Deprecated относится к идентификатору.',dataset_manifest='knowledge/updates/datasets/spdx-20261114.manifest.json')
if __name__=='__main__':
 rows=list(generate(sys.argv[1:3],sys.argv[3]));assert len({r['id'] for r in rows})==len(rows)
 Path(sys.argv[4]).write_text(''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows),encoding='utf-8')
