#!/usr/bin/env python3
"""Generate one deterministic page of UnicodeData 17.0.0 knowledge records."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
SOURCE_GIT_BLOB_SHA1="fca68e3e154e62e49727ecf69f1df442582bfa96"
MAX_BYTES=1850000
def git_blob_sha1(raw): return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
def source(checked):
    return {"url":"https://www.unicode.org/Public/17.0.0/ucd/UnicodeData.txt","title":"Unicode Character Database 17.0.0 — UnicodeData.txt","source_family":"unicode-ucd-17.0.0","source_version":"Unicode 17.0.0 final, 2025-08-15","published_at":"2025-08-15","checked_at":checked,"license":"Unicode-3.0","license_url":"https://www.unicode.org/license.txt","source_checksum":{"algorithm":"git_blob_sha1","value":SOURCE_GIT_BLOB_SHA1},"checksum_mirror":"unicode-org/unicodetools main"}
def make(line,n,checked,manifest):
    f=line.split(";")
    if len(f)!=15: raise ValueError(f"line {n}: expected 15 fields")
    cp,name=f[0],f[1]
    if name.endswith(", First>") or name.endswith(", Last>"): return None
    claim=f"В UnicodeData 17.0.0 для U+{cp} указаны Name={json.dumps(name,ensure_ascii=False)}, General_Category={f[2]}, Canonical_Combining_Class={f[3]} и Bidi_Class={f[4]}."
    return {"schema_version":1,"id":"ucd17-u"+cp.lower(),"version":1,"language":"ru","topic":f"Свойства Unicode U+{cp} в UCD 17.0.0","claim":claim,"category":"language","data_class":"static","checked_at":checked,"ttl_seconds":None,"review_status":"verified","sources":[source(checked)],"event_date":None,"published_at":"2025-08-15","source_version":"Unicode 17.0.0 final","limitations":"Свойства относятся к Unicode 17.0.0; значения и сокращения интерпретируются по UAX #44.","curation_method":"deterministic_structured_source_transform","dataset":"unicode-ucd-17.0.0-unicodedata","dataset_section":"UnicodeData.txt","dataset_key":f"U+{cp}","dataset_license":"Unicode-3.0","dataset_manifest":manifest,"source_line":n,"ucd_properties":{"code_point":cp,"name":f[1],"general_category":f[2],"canonical_combining_class":f[3],"bidi_class":f[4],"decomposition_mapping":f[5] or None,"decimal_digit_value":f[6] or None,"digit_value":f[7] or None,"numeric_value":f[8] or None,"bidi_mirrored":f[9],"unicode_1_name":f[10] or None,"iso_comment":f[11] or None,"simple_uppercase_mapping":f[12] or None,"simple_lowercase_mapping":f[13] or None,"simple_titlecase_mapping":f[14] or None}}
def main():
    p=argparse.ArgumentParser();p.add_argument("unicode_data",type=Path);p.add_argument("output_root",type=Path);p.add_argument("--line-start",type=int,required=True);p.add_argument("--line-end",type=int,required=True);p.add_argument("--checked-at",required=True);p.add_argument("--manifest",required=True);p.add_argument("--output-prefix",required=True);a=p.parse_args()
    raw=a.unicode_data.read_bytes()
    if git_blob_sha1(raw)!=SOURCE_GIT_BLOB_SHA1: raise ValueError("source checksum mismatch")
    lines=raw.decode("utf-8").splitlines();rows=[r for n,line in enumerate(lines[a.line_start-1:a.line_end],a.line_start) if (r:=make(line,n,a.checked_at,a.manifest)) is not None]
    chunks=[];current=[];size=0
    for row in rows:
        b=(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n").encode()
        if current and size+len(b)>MAX_BYTES: chunks.append(current);current=[];size=0
        current.append(b);size+=len(b)
    if current: chunks.append(current)
    for i,chunk in enumerate(chunks,1):
        path=a.output_root/f"{a.output_prefix}{i:02d}.jsonl";path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"".join(chunk))
if __name__=="__main__": main()
