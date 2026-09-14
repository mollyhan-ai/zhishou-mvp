"""Lexicons and normalisation used by the grounding checker.

These are seed lists, deliberately small and editable. They are *matching
vocabulary*, not clinical guidance -- nothing here tells the product what to
recommend, only which tokens deserve extra scrutiny when they appear in a
generated note.

Extend by editing lexicon_custom.json next to this file:
    {"drugs": ["..."], "symptoms": ["..."], "unit_aliases": {"毫克": "mg"}}
"""
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_CUSTOM = os.path.join(_HERE, "lexicon_custom.json")

# Common small-animal drugs. Matching vocabulary only.
DRUGS = [
    "阿莫西林", "克拉维酸", "头孢曲松", "头孢维星", "甲硝唑", "恩诺沙星",
    "马波沙星", "多西环素", "强力霉素", "新霉素", "硫酸新霉素", "庆大霉素",
    "呋塞米", "速尿", "苯巴比妥", "地西泮", "美洛昔康", "罗贝考昔",
    "卡洛芬", "泼尼松", "泼尼松龙", "地塞米松", "马罗匹坦", "昂丹司琼",
    "甲氧氯普胺", "胃复安", "奥美拉唑", "法莫替丁", "硫糖铝", "白陶土",
    "蒙脱石散", "益生菌", "乳酸林格", "生理盐水", "葡萄糖", "维生素K1",
    "阿托品", "丙泊酚", "异氟烷", "布托啡诺", "美托咪定", "阿替美唑",
    "吡喹酮", "米尔贝肟", "塞拉菌素", "非泼罗尼", "氟雷拉纳", "伊维菌素",
    "环孢素", "奥克拉替尼", "左旋甲状腺素", "美国甲巯咪唑", "甲巯咪唑",
    "苯丙酸诺龙", "氨苄西林", "林可霉素", "克林霉素", "酮康唑", "伊曲康唑",
]

# Findings/symptoms whose negation matters clinically.
SYMPTOMS = [
    "呕吐", "腹泻", "便血", "血便", "黑便", "发热", "脱水", "精神沉郁",
    "精神萎靡", "食欲不振", "厌食", "咳嗽", "打喷嚏", "流鼻涕", "呼吸困难",
    "抽搐", "跛行", "疼痛", "腹痛", "腹胀", "多饮", "多尿", "排尿困难",
    "血尿", "黄疸", "瘙痒", "脱毛", "皮疹", "耳部分泌物", "眼分泌物",
    "体重下降", "腹水", "心杂音", "苍白", "虚弱", "异物", "外伤", "出血",
]

UNIT_ALIASES = {
    "毫克": "mg", "毫升": "ml", "公斤": "kg", "千克": "kg", "克": "g",
    "微克": "ug", "微升": "ul", "国际单位": "IU", "摄氏度": "℃",
    "度": "℃", "小时": "h", "天": "d", "日": "d", "周": "w", "次": "次",
}

# Dose / frequency / duration patterns. Anything matching gets checked
# against the transcript verbatim.
NUMBER_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(mg/kg|ml/kg|ug/kg|IU/kg|mg|ml|ug|ul|kg|g|IU|℃|%|h|d|w)",
    re.IGNORECASE,
)
FREQ_RE = re.compile(
    r"(q\s*\d+\s*h|bid|tid|qid|sid|qd|每\s*\d+\s*小时|每日\s*\d+\s*次|"
    r"一日\s*\d+\s*次|每天\s*\d+\s*次|\d+\s*次\s*/\s*[日天])",
    re.IGNORECASE,
)
DURATION_RE = re.compile(r"(连用|持续|共)?\s*\d+\s*(天|日|周|个月)")

NEGATION_CUES = ["没有", "未见", "未出现", "无", "否认", "不曾", "未", "不"]

_CN_NUM = {"零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
           "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def _load_custom():
    if not os.path.exists(_CUSTOM):
        return
    try:
        with open(_CUSTOM, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    DRUGS.extend(x for x in data.get("drugs", []) if x not in DRUGS)
    SYMPTOMS.extend(x for x in data.get("symptoms", []) if x not in SYMPTOMS)
    UNIT_ALIASES.update(data.get("unit_aliases", {}))


_load_custom()


def normalize(text: str) -> str:
    """Fold the variation that makes literal comparison fail: full-width
    punctuation and digits, unit synonyms, simple Chinese numerals, spacing."""
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:          # full-width digits
            out.append(chr(code - 0xFEE0))
        elif 0xFF21 <= code <= 0xFF5A:        # full-width letters
            out.append(chr(code - 0xFEE0))
        elif ch in _CN_NUM:
            out.append(_CN_NUM[ch])
        else:
            out.append(ch)
    s = "".join(out)
    for alias, canon in sorted(UNIT_ALIASES.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(alias, canon)
    s = re.sub(r"\s+", "", s)
    return s.lower()
