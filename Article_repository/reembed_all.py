#!/usr/bin/env python3
"""把 ai-localbase 现有全文(.txt)重新 embedding 到一个新的 bge-m3(1024维) 知识库。

用法:
    python reembed_all.py
可用环境变量:
    AILB_URL      后端地址, 默认 http://localhost:8080
    UPLOADS_DIR   全文文件目录, 默认 backend/data/uploads
    WORKERS       上传并发数, 默认 4
"""
import os
import glob
import time
import sys
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.environ.get("AILB_URL", "http://localhost:8080")
UPLOADS = os.environ.get("UPLOADS_DIR", r"f:\Article_repository\ai-localbase\backend\data\uploads")
KB_NAME = os.environ.get("KB_NAME", "BGE-M3 知识库")
WORKERS = int(os.environ.get("WORKERS", "4"))


def create_kb():
    r = requests.post(f"{BASE}/api/knowledge-bases",
                      json={"name": KB_NAME, "description": "bge-m3(1024) 跨语言向量库"},
                      timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("id") or data.get("knowledgeBaseId") or data


def upload_one(kb, path):
    base = os.path.basename(path)
    with open(path, "rb") as f:
        r = requests.post(f"{BASE}/api/knowledge-bases/{kb}/documents",
                          files={"file": (base, f, "text/plain"), "name": (None, base)},
                          timeout=900)
    doc_id = None
    try:
        doc_id = r.json().get("id")
    except Exception:
        pass
    return base, r.status_code, doc_id, r.text[:160]


def list_docs(kb):
    r = requests.get(f"{BASE}/api/knowledge-bases/{kb}/documents", timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    kb = create_kb()
    print("[created kb]", kb)

    files = sorted(glob.glob(os.path.join(UPLOADS, "*.txt")))
    print("[files]", len(files))

    ok = fail = 0
    shown = [0]
    lock = threading.Lock()

    def _print(line):
        with lock:
            print(line)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(upload_one, kb, p): p for p in files}
        for j, f in enumerate(as_completed(futs), 1):
            base, code, doc_id, text = f.result()
            if code in (200, 201):
                ok += 1
                if j % 5 == 0 or j == len(files):
                    _print(f"[upload] {j}/{len(files)} ok={ok}")
            else:
                fail += 1
                _print(f"[FAIL] {base} code={code} {text}")

    print("[upload done] ok=%d fail=%d" % (ok, fail))

    # 等待全部 indexed
    print("[waiting indexed] ...")
    deadline = time.time() + 900
    while time.time() < deadline:
        docs = list_docs(kb)
        items = docs.get("documents") or docs.get("items") or (docs if isinstance(docs, list) else [])
        statuses = {}
        for it in items:
            statuses[it.get("status")] = statuses.get(it.get("status"), 0) + 1
        print("[status]", statuses, "total", len(items))
        if statuses.get("failed"):
            print("[WARN] some documents failed", flush=True)
        if all(s == "indexed" for s in statuses) and statuses.get("indexed"):
            print("[ALL INDEXED] kb=%s docs=%d" % (kb, len(items)))
            return
        time.sleep(10)

    print("[timeout waiting indexed] check docs manually; kb=%s" % kb)


if __name__ == "__main__":
    main()