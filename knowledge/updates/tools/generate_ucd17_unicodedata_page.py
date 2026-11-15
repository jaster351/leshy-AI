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
def uax(checked):
    return {"url":"https://www.unicode.org/reports/tr44/tr44-36.html","title":"UAX #44 revision 36 — Unicode Character Database 17.0.0","source_family":"unicode-uax44-17.0.0","source_version":"Revision 36, Unicode 17.0.0","published_at":"2025-08-27","checked_at":checked,"license":"Unicode-3.0","license_url":"https://www.unicode.org/license.txt"}
def fields(line,n):
    f=line.split(";")
    if len(f)!=15: raise ValueError(f"line {n}: expected 15 fields")
    return f
def props(f):
    keys=("general_category","canonical_combining_class","bidi_class","decomposition_mapping","decimal_digit_value","digit_value","numeric_value","bidi_mirrored","unicode_1_name","iso_comment","simple_uppercase_mapping","simple_lowercase_mapping","simple_titlecase_mapping")
    values=(f[2],f[3],f[4],f[5] or None,f[6] or None,f[7] or None,f[8] or None,f[9],f[10] or None,f[11] or None,f[12] or None,f[13] or None,f[14] or None)
    return dict(zip(keys,values))
def single(f,n,checked,manifest):
    cp,name=f[0],f[1]
    claim=f"В UnicodeData 17.0.0 для U+{cp} указаны Name={json.dumps(name,ensure_ascii=False)}, General_Category={f[2]}, Canonical_Combining_Class={f[3]} и Bidi_Class={f[4]}."
    return {"schema_version":1,"id":"ucd17-u"+cp.lower(),"version":1,"language":"ru","topic":f"Свойства Unicode U+{cp} в UCD 17.0.0","claim":claim,"category":"language","data_class":"static","checked_at":checked,"ttl_seconds":None,"review_status":"verified","sources":[source(checked)],"event_date":None,"published_at":"2025-08-15","source_version":"Unicode 17.0.0 final","limitations":"Свойства относятся к Unicode 17.0.0; значения и сокращения интерпретируются по UAX #44.","curation_method":"deterministic_structured_source_transform","dataset":"unicode-ucd-17.0.0-unicodedata","dataset_section":"UnicodeData.txt","dataset_key":f"U+{cp}","dataset_license":"Unicode-3.0","dataset_manifest":manifest,"source_line":n,"ucd_properties":{"code_point":cp,"name":name,**props(f)}}
def range_row(first,last,n,checked,manifest):
    start,end=first[0],last[0];first_name,last_name=first[1],last[1]
    identifier=first_name[1:-8]
    if not last_name.endswith(", Last>") or last_name[1:-7]!=identifier: raise ValueError(f"line {n}: mismatched range pair")
    if first[2:]!=last[2:]: raise ValueError(f"line {n}: range properties differ")
    claim=f"В UnicodeData 17.0.0 диапазон U+{start}–U+{end} обозначен парой {json.dumps(first_name,ensure_ascii=False)}/{json.dumps(last_name,ensure_ascii=False)}; для диапазона указаны General_Category={first[2]}, Canonical_Combining_Class={first[3]} и Bidi_Class={first[4]}."
    return {"schema_version":1,"id":f"ucd17-range-u{start.lower()}-u{end.lower()}","version":1,"language":"ru","topic":f"Диапазон Unicode U+{start}–U+{end} в UCD 17.0.0","claim":claim,"category":"language","data_class":"static","checked_at":checked,"ttl_seconds":None,"review_status":"verified","sources":[source(checked),uax(checked)],"event_date":None,"published_at":"2025-08-15","source_version":"Unicode 17.0.0 final","limitations":"UnicodeData.txt хранит этот диапазон парой First/Last; имена отдельных символов внутри диапазона алгоритмически выводятся по UAX #44 и здесь не разворачиваются в отдельные записи.","curation_method":"deterministic_structured_range_pair_transform","dataset":"unicode-ucd-17.0.0-unicodedata","dataset_section":"UnicodeData.txt","dataset_key":f"U+{start}..U+{end}","dataset_license":"Unicode-3.0","dataset_manifest":manifest,"source_lines":[n,n+1],"ucd_range":{"start_code_point":start,"end_code_point":end,"range_identifier":identifier,**props(first)}}
def page(lines,start,checked,manifest):
    rows=[];i=0
    while i<len(lines):
        f=fields(lines[i],start+i)
        if f[1].endswith(", First>"):
            if i+1>=len(lines): raise ValueError(f"line {start+i}: unpaired First")
            rows.append(range_row(f,fields(lines[i+1],start+i+1),start+i,checked,manifest));i+=2
        elif f[1].endswith(", Last>"): raise ValueError(f"line {start+i}: orphan Last")
        else: rows.append(single(f,start+i,checked,manifest));i+=1
    return rows
def main():
    p=argparse.ArgumentParser();p.add_argument("unicode_data",type=Path);p.add_argument("output_root",type=Path);p.add_argument("--line-start",type=int,required=True);p.add_argument("--line-end",type=int,required=True);p.add_argument("--checked-at",required=True);p.add_argument("--manifest",required=True);p.add_argument("--output-prefix",required=True);a=p.parse_args()
    raw=a.unicode_data.read_bytes()
    if git_blob_sha1(raw)!=SOURCE_GIT_BLOB_SHA1: raise ValueError("source checksum mismatch")
    lines=raw.decode("utf-8").splitlines();rows=page(lines[a.line_start-1:a.line_end],a.line_start,a.checked_at,a.manifest)
    chunks=[];current=[];size=0
    for row in rows:
        b=(json.dumps(row,ensure_ascii=False,separators=(",",":"))+"\n").encode()
        if current and size+len(b)>MAX_BYTES: chunks.append(current);current=[];size=0
        current.append(b);size+=len(b)
    if current: chunks.append(current)
    for i,chunk in enumerate(chunks,1):
        path=a.output_root/f"{a.output_prefix}{i:02d}.jsonl";path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"".join(chunk))
if __name__=="__main__": main()
