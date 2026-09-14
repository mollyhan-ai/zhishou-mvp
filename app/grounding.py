"""Deterministic verification of a generated note against its transcript.

This is plain code, not a model call. It runs after generation and before the
vet sees the note, and it produces a checklist rather than a verdict.

What it checks
  drug        a drug name in the note that is not in the transcript
  dose        a number+unit in the note that is not in the transcript
  frequency   a dosing frequency in the note that is not in the transcript
  duration    a course length in the note that is not in the transcript
  negation    the transcript negates a finding the note asserts
  placeholder a required field left blank instead of marked 未提及

What it deliberately does NOT do
  It does not score clinical correctness, and it cannot catch a paraphrase
  that changes meaning while reusing words that are present. It narrows what
  a vet must read closely; it does not replace reading.
"""
import re

from .lexicon import (
    DRUGS, SYMPTOMS, NEGATION_CUES,
    NUMBER_UNIT_RE, FREQ_RE, DURATION_RE, normalize,
)

SEVERITY = {"drug": "high", "dose": "high", "frequency": "high",
            "duration": "medium", "negation": "high", "placeholder": "low"}

UNMENTIONED = ("未提及", "待确认")

NOTE_FIELDS = [
    ("subjective", "S 主观"),
    ("objective", "O 客观"),
    ("assessment", "A 评估"),
    ("plan", "P 计划"),
]


def _field_text(note: dict, key: str) -> str:
    v = note.get(key)
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return str(v or "")


def _windows(hay: str, needle: str, radius: int = 12):
    """Yield normalised context windows around each occurrence of needle."""
    start = 0
    while True:
        i = hay.find(needle, start)
        if i < 0:
            return
        yield hay[max(0, i - radius): i + len(needle) + radius]
        start = i + len(needle)


def _is_negated(context: str, term: str) -> bool:
    """True if a negation cue sits immediately before term in this window."""
    i = context.find(term)
    if i < 0:
        return False
    before = context[:i]
    tail = before[-6:]
    return any(cue in tail for cue in NEGATION_CUES)


def _check_single(transcript: str, note: dict) -> list:
    """Return a list of flag dicts. Empty list means nothing to reconcile."""
    flags = []
    t_norm = normalize(transcript or "")

    for key, label in NOTE_FIELDS:
        raw = _field_text(note, key)
        n_norm = normalize(raw)
        if not raw.strip():
            flags.append({
                "kind": "placeholder", "severity": SEVERITY["placeholder"],
                "field": key, "field_label": label, "term": "",
                "message": f"{label} 为空。若录音中确实没有相关内容，请写明「未提及」而不是留空。",
            })
            continue

        # --- drugs ---------------------------------------------------------
        for drug in DRUGS:
            d = normalize(drug)
            if d and d in n_norm and d not in t_norm:
                flags.append({
                    "kind": "drug", "severity": SEVERITY["drug"],
                    "field": key, "field_label": label, "term": drug,
                    "message": f"{label} 出现药名「{drug}」，但逐字稿中未找到。请核对是否为转写遗漏或模型补写。",
                })

        # --- doses ---------------------------------------------------------
        for m in NUMBER_UNIT_RE.finditer(raw):
            token = normalize(m.group(0))
            if token and token not in t_norm:
                flags.append({
                    "kind": "dose", "severity": SEVERITY["dose"],
                    "field": key, "field_label": label, "term": m.group(0),
                    "message": f"{label} 出现剂量「{m.group(0)}」，但逐字稿中未找到该数值与单位组合。",
                })

        # --- frequency -----------------------------------------------------
        for m in FREQ_RE.finditer(raw):
            token = normalize(m.group(0))
            if token and token not in t_norm:
                flags.append({
                    "kind": "frequency", "severity": SEVERITY["frequency"],
                    "field": key, "field_label": label, "term": m.group(0),
                    "message": f"{label} 出现给药频次「{m.group(0)}」，但逐字稿中未找到。",
                })

        # --- duration ------------------------------------------------------
        for m in DURATION_RE.finditer(raw):
            token = normalize(m.group(0))
            if token and token not in t_norm:
                flags.append({
                    "kind": "duration", "severity": SEVERITY["duration"],
                    "field": key, "field_label": label, "term": m.group(0).strip(),
                    "message": f"{label} 出现疗程「{m.group(0).strip()}」，但逐字稿中未找到。",
                })

        # --- negation flips -------------------------------------------------
        for sym in SYMPTOMS:
            s = normalize(sym)
            if not s or s not in n_norm or s not in t_norm:
                continue
            t_negated = any(_is_negated(w, s) for w in _windows(t_norm, s))
            if not t_negated:
                continue
            n_negated = all(_is_negated(w, s) for w in _windows(n_norm, s))
            if not n_negated:
                flags.append({
                    "kind": "negation", "severity": SEVERITY["negation"],
                    "field": key, "field_label": label, "term": sym,
                    "message": f"逐字稿中「{sym}」是被否定的（如「没有{sym}」），"
                               f"但 {label} 中以肯定形式出现。请核对否定关系。",
                })

    # stable order: high severity first, then by field order
    order = {k: i for i, (k, _) in enumerate(NOTE_FIELDS)}
    rank = {"high": 0, "medium": 1, "low": 2}
    flags.sort(key=lambda f: (rank[f["severity"]], order.get(f["field"], 9), f["kind"]))
    for i, f in enumerate(flags):
        f["id"] = f"f{i}"
    return flags


def summarize(flags: list) -> dict:
    kinds = {}
    for f in flags:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    return {
        "total": len(flags),
        "high": sum(1 for f in flags if f["severity"] == "high"),
        "kinds": kinds,
    }


def find_spans(text: str, term: str) -> list:
    """Character offsets of term in text, for highlighting the transcript.
    Falls back to a loose search when exact match fails."""
    if not text or not term:
        return []
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(term), text)]
    if spans:
        return spans
    core = re.sub(r"[\s]", "", term)
    if core and core != term:
        spans = [(m.start(), m.end()) for m in re.finditer(re.escape(core), text)]
    return spans


HISTORY_MARKER = "（既往病史）"


def _clauses(text):
    return [part.strip() for part in re.split(r"[。！？!?；;\n]+", text) if part.strip()]


def _negated_in(text, term):
    # Preserve source and sentence boundaries: a historical negation cannot
    # attach itself to today's finding just because the text was concatenated.
    return any(_is_negated(w, term) for part in _clauses(text)
               for w in _windows(normalize(part), term))


def check(transcript: str, note: dict, history_summary: str = "") -> list:
    """Token support is a union; negations remain tied to their labelled source.

    With no active history the original checker is used byte-for-byte. This
    remains a lexical checklist, not a proof of clinical correctness.
    """
    if not (history_summary or "").strip():
        return _check_single(transcript, note)
    t_norm, h_norm = normalize(transcript or ""), normalize(history_summary)
    flags = []
    for flag in _check_single(transcript, note):
        if flag['kind'] == 'negation':
            continue
        if flag['kind'] in ('drug', 'dose', 'frequency', 'duration'):
            if normalize(flag['term']) in h_norm:
                continue
            flag['message'] = flag['message'].replace('逐字稿中', '逐字稿和既往摘要中均')
            flag['sources'] = ['transcript', 'history_summary']
        flags.append(flag)
    for key, label in NOTE_FIELDS:
        for clause in _clauses(_field_text(note, key)):
            marked_history = HISTORY_MARKER in clause or '(既往病史)' in clause
            mixed_time = marked_history and bool(re.search(r"本次|今天|今日", clause))
            historical = marked_history and not mixed_time
            if mixed_time:
                flags.append(dict(kind='source', severity='medium', field=key, field_label=label,
                                  term='', sources=['transcript', 'history_summary'],
                                  message=f'{label} 同一句混合了本次时点与既往病史标记，请拆成两句并核对来源。'))
            n_norm = normalize(clause)
            # A supported token still needs an honest source label. This is
            # separate from unsupported-content flags, so history is not called a hallucination.
            tokens = [word for word in DRUGS + SYMPTOMS if normalize(word) in n_norm]
            tokens += [m.group(0).strip() for pattern in (NUMBER_UNIT_RE, FREQ_RE, DURATION_RE)
                       for m in pattern.finditer(clause)]
            if not historical:
                for term in sorted(set(tokens)):
                    if normalize(term) in h_norm and normalize(term) not in t_norm:
                        flags.append(dict(kind='source', severity='medium', field=key, field_label=label,
                                          term=term, sources=['history_summary'],
                                          message=f'{label} 的「{term}」仅在既往摘要中找到依据，请在相关内容后标注「（既往病史）」。'))
            for sym in SYMPTOMS:
                token = normalize(sym)
                if token not in n_norm:
                    continue
                # Marked history is checked against history; unmarked current
                # findings always retain the transcript's negation constraints.
                if historical and token in h_norm:
                    source, source_name, source_id = history_summary, '既往摘要', 'history_summary'
                elif token in t_norm:
                    source, source_name, source_id = transcript, '逐字稿', 'transcript'
                elif token in h_norm:
                    source, source_name, source_id = history_summary, '既往摘要', 'history_summary'
                else:
                    continue
                if _negated_in(source, token) and not all(_is_negated(w, token) for w in _windows(n_norm, token)):
                    flags.append(dict(kind='negation', severity='high', field=key, field_label=label,
                                      term=sym, sources=[source_id],
                                      message=f'{source_name}中「{sym}」存在否定表述，但 {label} 的对应来源内容以肯定形式出现，请核对。'))
    unique = {}
    for flag in flags:
        identity = (flag['field'], flag['kind'], flag['term'], tuple(flag.get('sources', [])))
        unique.setdefault(identity, flag)
    flags = list(unique.values())
    order = {key:i for i,(key,_) in enumerate(NOTE_FIELDS)}
    rank = {'high':0, 'medium':1, 'low':2}
    flags.sort(key=lambda flag:(rank[flag['severity']], order[flag['field']], flag['kind']))
    for i, flag in enumerate(flags):
        flag['id'] = f'f{i}'
    return flags
