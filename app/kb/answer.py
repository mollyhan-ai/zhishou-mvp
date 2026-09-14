"""Answering from the knowledge base.

The model is given retrieved passages and nothing else, and is required to
return claims that each carry a citation. Then plain code validates every
citation against the passages actually supplied. If any claim lacks a valid
citation, the whole answer is discarded and the question is refused.

Fail closed. An unsupported answer is worse than no answer.
"""
import json
import re

from ..config import get_config
from ..providers import ProviderError
from ..providers.llm import chat
from . import retrieve
from .store import coverage

REFUSAL_TEXT = "当前知识库无法回答这个问题。"

SYSTEM_PROMPT = """你是兽医知识检索助手。你只能依据下面提供的资料片段回答，不得使用任何自身的医学常识。

绝对规则：
1. 只使用 <资料> 中的内容。资料没写的，就是知识库没有，不得补充。
2. 每一条结论都必须标注它来自哪个片段编号。没有片段支持的话不要说。
3. 如果资料不足以回答，或资料之间互相冲突，把 answerable 设为 false。
4. 不得编造片段编号、文献名、页码或章节号。
5. 不要给出资料之外的剂量、疗程或处置建议。

输出格式：只输出一个 JSON 对象，不要任何其他文字。
{
  "answerable": true 或 false,
  "reason": 当 answerable 为 false 时填 "no_evidence" 或 "conflict"，否则填 null,
  "claims": [
    {"text": "一句结论", "citations": ["C1"]},
    {"text": "另一句结论", "citations": ["C2", "C3"]}
  ],
  "caveats": ["资料中明确写出的注意事项"]
}
claims 中每一项的 citations 不得为空数组。"""


def _build_context(hits):
    lines, cmap = [], {}
    for i, h in enumerate(hits, start=1):
        tag = f"C{i}"
        cmap[tag] = h
        meta = f"{h['title']}"
        if h.get("version"):
            meta += f" {h['version']}"
        meta += f" · {h['locator']}"
        lines.append(f"[{tag}] （{meta}）\n{h['text']}")
    return "\n\n".join(lines), cmap


def _parse(raw: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except ValueError:
                pass
    raise ProviderError("知识问答模型没有返回合法 JSON。", detail=raw[:400])


def _refuse(reason: str, hits=None, detail=None):
    return {
        "answerable": False,
        "reason": reason,
        "answer_text": REFUSAL_TEXT,
        "detail": detail,
        "claims": [],
        "caveats": [],
        "sources": [_source_stub(h) for h in (hits or [])],
        "coverage": coverage(),
    }


def _source_stub(h):
    return {
        "chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "title": h["title"],
        "source": h["source"], "version": h["version"],
        "published_on": h["published_on"], "locator": h["locator"],
        "score": h["score"], "coverage": h.get("coverage"),
        "matched_terms": h.get("matched_terms", []),
    }


def ask(question: str) -> dict:
    cfg = get_config()
    question = (question or "").strip()
    if not question:
        return _refuse("no_evidence", detail="问题为空。")

    cov = coverage()
    if cov["doc_count"] == 0:
        return _refuse(
            "no_documents",
            detail="知识库中还没有任何文档。请先用 tools/import_docs.py 导入你有权使用的资料。",
        )

    hits = retrieve.search(question, top_k=cfg.kb_top_k)
    strong = [h for h in hits if h["coverage"] >= cfg.kb_min_coverage]
    if not strong:
        return _refuse(
            "no_evidence", hits=hits[:3],
            detail=(f"最相关片段只命中了问题中 {int((hits[0]['coverage'] if hits else 0) * 100)}% 的检索词，"
                    f"低于 {int(cfg.kb_min_coverage * 100)}% 的阈值。"),
        )

    context, cmap = _build_context(strong)
    raw = chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"<资料>\n{context}\n</资料>\n\n问题：{question}"},
        ],
        response_json=True, max_tokens=1200,
    )
    data = _parse(raw)

    if not data.get("answerable"):
        reason = data.get("reason") or "no_evidence"
        return _refuse(reason if reason in ("no_evidence", "conflict") else "no_evidence",
                       hits=strong)

    # --- citation validation: fail closed --------------------------------
    claims, violations = [], []
    for c in data.get("claims") or []:
        text = str(c.get("text", "")).strip()
        cites = [str(x).strip() for x in (c.get("citations") or []) if str(x).strip()]
        if not text:
            continue
        if not cites:
            violations.append(f"结论缺少引用：{text[:40]}")
            continue
        bad = [x for x in cites if x not in cmap]
        if bad:
            violations.append(f"引用编号不存在：{', '.join(bad)}")
            continue
        claims.append({
            "text": text,
            "citations": [{
                "tag": tag,
                "chunk_id": cmap[tag]["chunk_id"],
                "title": cmap[tag]["title"],
                "locator": cmap[tag]["locator"],
                "version": cmap[tag]["version"],
            } for tag in cites],
        })

    if violations or not claims:
        return _refuse(
            "unverified_citation", hits=strong,
            detail="模型返回的结论未能全部通过引用校验，已整体丢弃。" +
                   ("；".join(violations[:3]) if violations else ""),
        )

    return {
        "answerable": True,
        "reason": None,
        "answer_text": None,
        "claims": claims,
        "caveats": [str(x).strip() for x in (data.get("caveats") or []) if str(x).strip()],
        "sources": [_source_stub(h) for h in strong],
        "coverage": cov,
    }
