#!/usr/bin/env python3
"""Reproduce CLDR 49 Russian locale-key, type, currency and long-unit packages."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import xml.etree.ElementTree as ET
CHECKED_AT="2026-11-11T20:05:11Z"
SOURCE_URL="https://github.com/unicode-org/cldr/blob/release-49/common/main/ru.xml"
LICENSE_URL="https://github.com/unicode-org/cldr/blob/release-49/LICENSE"
SOURCE_SHA="0307a700b08204fa8f0df18934b1c6f5ee30689c"
PUBLISHED_AT="2026-10-21T22:13:30Z"
MANIFEST="knowledge/updates/datasets/cldr49-ru-locale-technical-names.manifest.json"
OUTPUTS={"key":"knowledge/updates/2026-11-12/0100-cldr49-russian-locale-key-names.jsonl","type":"knowledge/updates/2026-11-12/0100-cldr49-russian-locale-type-names.jsonl","currency":"knowledge/updates/2026-11-12/0100-cldr49-russian-currency-display-names.jsonl","unit":"knowledge/updates/2026-11-12/0100-cldr49-russian-long-unit-display-names.jsonl"}
def blob_sha(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
def source(): return {"url":SOURCE_URL,"title":"Unicode CLDR 49 — Russian locale data (common/main/ru.xml)","source_family":"unicode-cldr-49-ru","source_version":"CLDR 49, tag release-49, Git blob "+SOURCE_SHA,"published_at":PUBLISHED_AT,"checked_at":CHECKED_AT,"license":"Unicode-3.0","license_url":LICENSE_URL,"source_checksum":{"algorithm":"git_blob_sha1","value":SOURCE_SHA}}
def base(i,t,c,section,key,limit): return {"schema_version":1,"id":i,"version":1,"language":"ru","topic":t,"claim":c,"category":"language","data_class":"static","checked_at":CHECKED_AT,"ttl_seconds":None,"review_status":"verified","sources":[source()],"event_date":None,"published_at":PUBLISHED_AT,"source_version":"CLDR 49, release-49","limitations":limit,"curation_method":"deterministic_structured_source_transform","dataset":"unicode-cldr-49-ru-locale-technical-names","dataset_section":section,"dataset_key":key,"dataset_license":"Unicode-3.0","dataset_manifest":MANIFEST}
def make(kind,row):
    if kind=="key":
        k,n=row; return base(f"cldr49-ru-locale-key-{k.lower()}",f"Русское название ключа локали {k} в CLDR 49",f"В русской локали CLDR 49 для ключа локали «{k}» задано отображаемое название «{n}».","localeDisplayNames/keys",k,"Это локализованная подпись технического ключа; допустимые значения и поведение определяются спецификациями Unicode Locale Extensions.")
    if kind=="type":
        k,v,n=row; return base(f"cldr49-ru-locale-type-{k.lower()}-{v.lower()}",f"Русское название значения {v} ключа {k} в CLDR 49",f"В русской локали CLDR 49 для значения «{v}» ключа локали «{k}» задано отображаемое название «{n}».","localeDisplayNames/types",f"{k}|{v}","Это локализованная подпись значения ключа; она не является полной спецификацией календаря, сортировки, системы счисления или другого режима.")
    if kind=="currency":
        k,n=row; return base(f"cldr49-ru-currency-{k.lower()}",f"Русское отображаемое название валютного кода {k} в CLDR 49",f"В русской локали CLDR 49 для валютного кода «{k}» задано базовое отображаемое название «{n}».","numbers/currencies",k,"CLDR содержит современные и исторические коды. Название не подтверждает статус законного платёжного средства, период обращения или текущий обменный курс.")
    k,n=row; return base(f"cldr49-ru-unit-{k.lower()}",f"Русское длинное название единицы {k} в CLDR 49",f"В русской локали CLDR 49 для кода единицы «{k}» задано длинное отображаемое название «{n}».","units/unitLength-long",k,"Это локализованная подпись единицы, а не определение физической величины, коэффициента преобразования или допустимости применения.")
def text(n): return n.text.strip() if n.text and n.text.strip()!="↑↑↑" else None
def select(root):
    keys=[(n.attrib["type"],text(n)) for n in root.findall("./localeDisplayNames/keys/key") if set(n.attrib)=={"type"} and text(n)]
    types=[(n.attrib["key"],n.attrib["type"],text(n)) for n in root.findall("./localeDisplayNames/types/type") if set(n.attrib)=={"key","type"} and text(n)]
    currencies=[(n.attrib["type"],text(n.find("displayName"))) for n in root.findall("./numbers/currencies/currency") if n.find("displayName") is not None and not n.find("displayName").attrib and text(n.find("displayName"))]
    long=root.find("./units/unitLength[@type='long']")
    units=[(n.attrib["type"],text(n.find("displayName"))) for n in long.findall("unit") if n.find("displayName") is not None and not n.find("displayName").attrib and text(n.find("displayName"))]
    return {"key":keys,"type":types,"currency":currencies,"unit":units}
def write(root,kind,rows):
    content="".join(json.dumps(make(kind,row),ensure_ascii=False,separators=(",",":"))+"\n" for row in rows)
    if len(content.encode())>=2*1024*1024: raise ValueError("output exceeds importer limit")
    p=root/OUTPUTS[kind];p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding="utf-8",newline="")
def main():
    p=argparse.ArgumentParser();p.add_argument("source_xml",type=Path);p.add_argument("output_root",type=Path);a=p.parse_args();raw=a.source_xml.read_bytes()
    if blob_sha(raw)!=SOURCE_SHA: raise ValueError("source checksum mismatch")
    rows=select(ET.fromstring(raw));expected={"key":37,"type":89,"currency":282,"unit":185}
    if {k:len(v) for k,v in rows.items()}!=expected: raise ValueError("selection count mismatch")
    for k,v in rows.items(): write(a.output_root,k,v)
if __name__=="__main__": main()
