"""De-identification for the requirements log.

Policy implemented here:
  * event_log never receives clinical text, audio, or identifiers.
  * session ids are salted-hashed before they reach the log, so a log row
    cannot be joined back to a consult without the salt.
  * raw query text is stored only when the operator has switched it on AND
    the user consented on that specific query. It always carries a TTL.
  * scrub() is a defence in depth for the cases where free text really must
    be kept -- it is a filter, not a guarantee. See README limitations.
"""
import hashlib
import os
import re

_SALT_FILE_ENV = "ZS_LOG_SALT"


def _salt() -> bytes:
    s = os.environ.get(_SALT_FILE_ENV, "")
    if not s:
        # Ephemeral salt: rotates on restart, which makes cross-restart
        # joining impossible. Set ZS_LOG_SALT to keep refs stable.
        s = "ephemeral-" + str(os.getpid())
    return s.encode("utf-8")


def session_ref(session_id: str) -> str:
    return hashlib.sha256(_salt() + session_id.encode("utf-8")).hexdigest()[:16]


# --- patterns -------------------------------------------------------------
# Ordered: longer / more specific first so they do not eat each other.
PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("id_card", re.compile(r"\b\d{17}[\dXx]\b")),
    ("phone_cn", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("landline", re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")),
    ("case_no", re.compile(r"(?:病例号|病历号|就诊号|档案号|会员号)\s*[:：]?\s*[A-Za-z0-9-]{4,}")),
    ("chip", re.compile(r"(?<!\d)\d{15}(?!\d)")),
    ("long_digits", re.compile(r"(?<!\d)\d{9,}(?!\d)")),
    ("owner_name", re.compile(r"(?:主人|宠主|家长|车主|联系人)\s*[:：]?\s*[\u4e00-\u9fa5]{2,4}(?=\s|,|，|。|$)")),
    ("addr", re.compile(r"[\u4e00-\u9fa5]{2,10}(?:省|市|区|县)[\u4e00-\u9fa5\d]{2,20}(?:路|街|道|号|栋|室)")),
]


def scrub(text: str):
    """Replace identifying spans with typed placeholders.

    Returns (scrubbed_text, counts_by_type).
    """
    if not text:
        return "", {}
    counts = {}
    out = text
    for name, pat in PATTERNS:
        def _repl(m, _n=name):
            counts[_n] = counts.get(_n, 0) + 1
            return f"[{_n}]"
        out = pat.sub(_repl, out)
    return out, counts


def contains_pii(text: str) -> bool:
    _, counts = scrub(text or "")
    return bool(counts)


# --- log payload shaping --------------------------------------------------

def bucket(n, edges=(0, 20, 50, 100, 300, 1000)):
    """Coarsen a number so the log keeps shape without keeping content."""
    if n is None:
        return None
    prev = edges[0]
    for e in edges[1:]:
        if n < e:
            return f"{prev}-{e - 1}"
        prev = e
    return f"{edges[-1]}+"


SAFE_KEYS = {
    "mode", "status", "ok", "error_kind", "provider", "latency_ms",
    "audio_bytes_bucket", "transcript_len_bucket", "query_len_bucket",
    "flag_count", "flag_kinds", "chunks_matched", "top_score",
    "asr_chunk_count", "asr_completed_chunks", "audio_duration_seconds",
    "refused", "refuse_reason", "doc_count", "edited", "part",
}


def safe_payload(payload: dict) -> dict:
    """Drop anything not on the allow-list. Fail closed."""
    return {k: v for k, v in (payload or {}).items() if k in SAFE_KEYS}
