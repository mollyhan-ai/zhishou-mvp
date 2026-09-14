"""Importing knowledge documents.

The importer is deliberately obstructive. It refuses any document that does
not declare title, source, and an explicit licence/permission note, because
the failure mode we are guarding against is a scraped textbook quietly
becoming a citation. Nothing fetches anything from the network: documents
are files you place in kb_docs/ yourself.

File format: Markdown with a YAML-ish front matter block.

    ---
    title: 犬急性腹泻院内诊疗规范
    source: XX动物医院集团 内部诊疗规范
    version: v2.1
    published_on: 2026-03-01
    license_note: 集团自有文件，已获医疗管理部书面授权用于本系统
    topics: [犬腹泻, 补液]
    species: [犬]
    ---

    ## 3.1 初步评估
    正文...

Headings starting with ## become chunk boundaries and supply the locator.
"""
import os
import re

from .. import db
from ..lexicon import normalize

REQUIRED = ("title", "source", "license_note")
MAX_CHUNK_CHARS = 900
MIN_CHUNK_CHARS = 60


class ImportError_(Exception):
    pass


def _parse_front_matter(text: str):
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise ImportError_("缺少 front matter。文件必须以 --- 包裹的元数据开头。")
    head, body = m.group(1), m.group(2)
    meta = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ImportError_(f"元数据行无法解析：{line!r}")
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
        else:
            meta[k] = v
    return meta, body


def tokenize(text: str) -> list:
    """Dependency-free tokeniser: latin/number words plus CJK character
    bigrams. Good enough for a 10-20 document corpus."""
    s = normalize(text)
    tokens = re.findall(r"[a-z0-9]+(?:[./][a-z0-9]+)*", s)
    cjk = re.findall(r"[\u4e00-\u9fff]+", s)
    for run in cjk:
        if len(run) == 1:
            tokens.append(run)
        for i in range(len(run) - 1):
            tokens.append(run[i:i + 2])
    return tokens


def _split_body(body: str):
    """Yield (locator, text). Sections split on '## '; long sections split
    further on blank lines while keeping the section locator."""
    sections = []
    current_loc, buf = "全文", []
    for line in body.splitlines():
        h = re.match(r"^##+\s+(.*)$", line)
        if h:
            if "".join(buf).strip():
                sections.append((current_loc, "\n".join(buf).strip()))
            current_loc = h.group(1).strip()
            buf = []
        else:
            buf.append(line)
    if "".join(buf).strip():
        sections.append((current_loc, "\n".join(buf).strip()))

    # Never drop content: a passage that vanishes here can never be cited.
    # Short pieces are merged forward within the same locator, because the
    # locator is what a citation points at -- merging across sections would
    # make the citation wrong.
    for loc, text in sections:
        if len(text) <= MAX_CHUNK_CHARS:
            yield loc, text
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        cur = ""
        for p in paras:
            if cur and len(cur) + len(p) + 1 > MAX_CHUNK_CHARS and len(cur) >= MIN_CHUNK_CHARS:
                yield loc, cur.strip()
                cur = p
            else:
                cur = f"{cur}\n{p}" if cur else p
        if cur.strip():
            yield loc, cur.strip()


def import_file(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    meta, body = _parse_front_matter(raw)

    missing = [k for k in REQUIRED if not str(meta.get(k, "")).strip()]
    if missing:
        raise ImportError_(
            "缺少必填元数据：" + ", ".join(missing) +
            "。license_note 必须写明你对这份文档的使用权来源，未声明的文档不会被导入。"
        )

    doc_id = db.new_id("d")
    db.insert_doc({
        "id": doc_id,
        "title": meta["title"],
        "source": meta["source"],
        "version": meta.get("version"),
        "published_on": meta.get("published_on"),
        "license_note": meta["license_note"],
        "topics": meta.get("topics", []),
        "species": meta.get("species", []),
    })

    chunks = []
    for i, (loc, text) in enumerate(_split_body(body)):
        # The heading and the document title are indexed alongside the body:
        # they are the highest-signal terms a vet is likely to type, and
        # without them a vague paragraph can outrank the section that is
        # literally named after the question.
        indexed = f"{meta['title']} {loc} {text}"
        chunks.append({
            "id": db.new_id("c"), "doc_id": doc_id, "locator": loc,
            "ordinal": i, "text": text, "tokens": tokenize(indexed),
        })
    if not chunks:
        db.delete_doc(doc_id)
        raise ImportError_("文档正文为空，没有可检索的内容。")
    db.insert_chunks(chunks)
    return {"doc_id": doc_id, "title": meta["title"], "chunks": len(chunks)}


def import_dir(directory: str) -> list:
    results = []
    for name in sorted(os.listdir(directory)):
        # '_' prefix marks templates and notes that are not knowledge.
        if name.startswith("_") or name.startswith("."):
            continue
        if not name.lower().endswith((".md", ".markdown")):
            continue
        path = os.path.join(directory, name)
        try:
            r = import_file(path)
            r["file"] = name
            r["ok"] = True
        except (ImportError_, OSError) as exc:
            r = {"file": name, "ok": False, "error": str(exc)}
        results.append(r)
    return results


def coverage() -> dict:
    """What the knowledge base can and cannot speak to, derived from the
    imported documents themselves rather than declared by hand."""
    docs = db.list_docs(active_only=True)
    topics, species = [], []
    for d in docs:
        for t in d["topics"]:
            if t not in topics:
                topics.append(t)
        for s in d["species"]:
            if s not in species:
                species.append(s)
    return {
        "doc_count": len(docs),
        "topics": topics,
        "species": species,
        "documents": [
            {"id": d["id"], "title": d["title"], "source": d["source"],
             "version": d["version"], "published_on": d["published_on"],
             "topics": d["topics"], "species": d["species"]}
            for d in docs
        ],
    }
