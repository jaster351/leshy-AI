#!/usr/bin/env python3
"""Reproduce CLDR 49 Russian script and locale-variant display-name packages."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import xml.etree.ElementTree as ET
CHECKED_AT="2026-11-11T19:01:06Z"
SOURCE_URL="https://github.com/unicode-org/cldr/blob/release-49/common/main/ru.xml"
LICENSE_URL="https://github.com/unicode-org/cldr/blob/release-49/LICENSE"
SOURCE_SHA="0307a700b08204fa8f0df18934b1c6f5ee30689c"
PUBLISHED_AT="2026-10-21T22:13:30Z"
MANIFEST="knowledge/updates/datasets/cldr49-ru-script-variant-names.manifest.json"
def blob_sha(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
def source(): return {"url":SOURCE_URL,"title":"Unicode CLDR 49 — Russian locale data (common/main/ru.xml)","source_family":"unicode-cldr-49-ru","source_version":"CLDR 49, tag release-49, Git blob "+SOURCE_SHA,"published_at":PUBLISHED_AT,"checked_at":CHECKED_AT,"license":"Unicode-3.0","license_url":LICENSE_URL,"source_checksum":{"algorithm":"git_blob_sha1","value":SOURCE_SHA}}
def make(kind,code,name):
    script=kind=="script"; section="scripts" if script else "variants"; label="кода письменности" if script else "вариантного кода локали"
    limit="Это локализованная подпись кода письменности; она не описывает историю, алфавитный состав или применимость письменности к конкретному языку." if script else "Это локализованная подпись варианта локали; она не заменяет спецификацию орфографии или правил варианта."
    return {"schema_version":1,"id":f"cldr49-ru-{kind}-{code.lower()}","version":1,"language":"ru","topic":f"Русское отображаемое название {label} {code} в CLDR 49","claim":f"В русской локали CLDR 49 для {label} «{code}» задано отображаемое название «{name}».","category":"language","data_class":"static","checked_at":CHECKED_AT,"ttl_seconds":None,"review_status":"verified","sources":[source()],"event_date":None,"published_at":PUBLISHED_AT,"source_version":"CLDR 49, release-49","limitations":limit,"curation_method":"deterministic_structured_source_transform","dataset":"unicode-cldr-49-ru-script-variant-names","dataset_section":f"localeDisplayNames/{section}","dataset_key":code,"dataset_license":"Unicode-3.0","dataset_manifest":MANIFEST}
def select(root,plural,singular):
    parent=root.find("./localeDisplayNames/"+plural)
    if parent is None: raise ValueError("missing "+plural)
    rows=[(n.attrib["type"],n.text.strip()) for n in parent.findall(singular) if set(n.attrib)=={"type"} and n.text and n.text.strip()!="↑↑↑"]
    if len(rows)!=len({k for k,_ in rows}): raise ValueError("duplicate key")
    return rows
def write(path,rows,kind):
    content="".join(json.dumps(make(kind,*row),ensure_ascii=False,separators=(",",":"))+"\n" for row in rows)
    if len(content.encode())>=2*1024*1024: raise ValueError("output exceeds importer limit")
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content,encoding="utf-8",newline="")
def main():
    p=argparse.ArgumentParser();p.add_argument("source_xml",type=Path);p.add_argument("output_root",type=Path);a=p.parse_args();raw=a.source_xml.read_bytes()
    if blob_sha(raw)!=SOURCE_SHA: raise ValueError("source checksum mismatch")
    root=ET.fromstring(raw);s=select(root,"scripts","script");v=select(root,"variants","variant")
    if (len(s),len(v))!=(116,6): raise ValueError("selection count mismatch")
    write(a.output_root/"knowledge/updates/2026-11-12/0000-cldr49-russian-script-display-names.jsonl",s,"script");write(a.output_root/"knowledge/updates/2026-11-12/0000-cldr49-russian-variant-display-names.jsonl",v,"variant")
if __name__=="__main__": main()
