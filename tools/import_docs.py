#!/usr/bin/env python3
"""Knowledge base admin.

    python3 tools/import_docs.py import kb_docs/
    python3 tools/import_docs.py import kb_docs/diarrhea.md
    python3 tools/import_docs.py list
    python3 tools/import_docs.py disable d_ab12...     # take offline, keep the file
    python3 tools/import_docs.py enable  d_ab12...
    python3 tools/import_docs.py delete  d_ab12...     # remove doc + chunks
    python3 tools/import_docs.py coverage

This tool reads local files only. It never fetches anything, by design: the
documents in your knowledge base should be ones you can point at a licence
for. Import refuses any file without title, source and license_note.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app import db  # noqa: E402
from app.kb import store  # noqa: E402


def cmd_import(target):
    db.init_db()
    if os.path.isdir(target):
        results = store.import_dir(target)
    else:
        try:
            r = store.import_file(target)
            r["file"] = os.path.basename(target)
            r["ok"] = True
            results = [r]
        except (store.ImportError_, OSError) as exc:
            results = [{"file": os.path.basename(target), "ok": False, "error": str(exc)}]

    ok = sum(1 for r in results if r["ok"])
    for r in results:
        if r["ok"]:
            print(f"  [OK]   {r['file']}  ->  {r['title']}  ({r['chunks']} 个片段, {r['doc_id']})")
        else:
            print(f"  [SKIP] {r['file']}  ->  {r['error']}")
    print(f"\n导入完成：成功 {ok} / 共 {len(results)}")
    return 0 if ok or not results else 1


def cmd_list():
    db.init_db()
    docs = db.list_docs(active_only=False)
    if not docs:
        print("知识库为空。")
        return 0
    for d in docs:
        state = "在线" if d["active"] else "已下线"
        print(f"{d['id']}  [{state}]  {d['title']}")
        print(f"    来源: {d['source']}   版本: {d['version'] or '—'}   发布: {d['published_on'] or '—'}")
        print(f"    使用权: {d['license_note']}")
        print(f"    主题: {'、'.join(d['topics']) or '—'}   物种: {'、'.join(d['species']) or '—'}")
    return 0


def cmd_active(doc_id, active):
    db.init_db()
    if db.set_doc_active(doc_id, active):
        print(f"{doc_id} 已{'上线' if active else '下线'}。下线后它不会再被检索，引用也会失效。")
        return 0
    print("文档不存在。")
    return 1


def cmd_delete(doc_id):
    db.init_db()
    if db.delete_doc(doc_id):
        print(f"{doc_id} 及其全部片段已删除。")
        return 0
    print("文档不存在。")
    return 1


def cmd_coverage():
    db.init_db()
    c = store.coverage()
    print(f"文档数: {c['doc_count']}")
    print(f"覆盖主题: {'、'.join(c['topics']) or '—'}")
    print(f"覆盖物种: {'、'.join(c['species']) or '—'}")
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "import" and len(argv) > 2:
        return cmd_import(argv[2])
    if cmd == "list":
        return cmd_list()
    if cmd == "disable" and len(argv) > 2:
        return cmd_active(argv[2], False)
    if cmd == "enable" and len(argv) > 2:
        return cmd_active(argv[2], True)
    if cmd == "delete" and len(argv) > 2:
        return cmd_delete(argv[2])
    if cmd == "coverage":
        return cmd_coverage()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
