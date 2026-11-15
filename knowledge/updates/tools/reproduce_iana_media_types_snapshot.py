"""Fetch and verify pinned upstream XML; discard contacts and reproduce snapshot."""
import json,hashlib,urllib.request,xml.etree.ElementTree as E
from pathlib import Path
from datetime import datetime,timezone
p=Path('knowledge/updates');url='https://www.iana.org/assignments/media-types/media-types.xml';raw=urllib.request.urlopen(url).read();manifest=json.loads((p/'datasets/iana-media-types-20261112.manifest.json').read_text());assert hashlib.sha256(raw).hexdigest()==manifest['source']['sha256'], 'Upstream changed: create a new snapshot and versions, do not overwrite';checked=manifest['source']['checked_at'];r=E.fromstring(raw);ns={'i':'http://www.iana.org/assignments'}
def label(el):
 s=el.text or ''
 for c in el:
  s+=(c.attrib.get('data','').upper() if c.attrib.get('type')=='rfc' else (c.text or ''))+(c.tail or '')
 return s.strip()
rows=[];groups={}
for g in r.findall('i:registry',ns):
 groups[g.attrib['id']]=len(g.findall('i:record',ns))
 for x in g.findall('i:record',ns):
  name=label(x.find('i:name',ns));mt=g.attrib['id']+'/'+name.split()[0];f=x.find('i:file',ns)
  rows.append(dict(media_type=mt,registry_label=name,template_path=f.text,template_name=f.attrib.get('name'),registered_date=x.attrib.get('date'),updated_date=x.attrib.get('updated'),references=[dict(type=z.attrib['type'],data=z.attrib.get('data'),label=z.text) for z in x.iter('{http://www.iana.org/assignments}xref') if z.attrib['type']!='person']))
assert len(rows)==2383 and len({x['media_type'].lower() for x in rows})==2383
snap=p/'datasets/iana-media-types-20261112.normalized.json';data=(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n').encode();assert hashlib.sha256(data).hexdigest()==manifest['snapshot_sha256'];assert not snap.exists() or snap.read_bytes()==data;snap.write_bytes(data)
