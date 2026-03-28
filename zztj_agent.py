"""
《资治通鉴·周纪》智能比对 Agent — 轻量版
使用 sentence_transformers + ChromaDB + Ollama，完全本地运行

流程：加载史料 → 构建向量索引 → 语义检索 → Diff比对 → LLM分析
"""

import os
import re
import html
import json
import difflib
import warnings
import gc
import time as time_module
import chromadb
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

try:
    from opencc import OpenCC
except Exception:  # pragma: no cover - optional dependency for script conversion
    OpenCC = None

warnings.filterwarnings("ignore")

# ─── 配置 ───────────────────────────────────────────────────────────
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
LLM_MODEL       = os.getenv("ZZTJ_LLM_MODEL", "qwen2.5:3b")
EMBED_MODEL     = "shibing624/text2vec-base-chinese"  # 中文Embedding
INDEX_VERSION   = 3                     # 索引版本，分块策略变化后自动重建新索引
CHUNK_SIZE      = 280                   # 每段字数（按事件粒度收缩窗口）
CHUNK_OVERLAP   = 70                    # 重叠字数（保留前后文）
TOP_K           = 3                     # Top-K 检索结果
SEMANTIC_CANDIDATES = 12                # 语义检索候选数，供二次重排
KEYWORD_CANDIDATES = 48                 # 关键词召回进入重排的最大候选数
MAX_RERANK_CANDIDATES = 72              # 混合召回后进入精排的最大候选数
CORPUS_CACHE_VERSION = 1                # 语料缓存版本
MIN_RESULT_SCORE = 0.12                # 低于该分数的结果不进入最终报告
INDEX_DIR       = "./index_storage"
SOURCES_DIR     = "./sources"
COLLECTION_NAME = f"zztj_sources_v{INDEX_VERSION}"
LLM_TEMPERATURE = float(os.getenv("ZZTJ_LLM_TEMPERATURE", "0.15"))
LLM_NUM_PREDICT = int(os.getenv("ZZTJ_LLM_NUM_PREDICT", "1200"))
LLM_NUM_CTX     = int(os.getenv("ZZTJ_LLM_NUM_CTX", "8192"))

# ─── 路径自动解析 ────────────────────────────────────────────────────
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
INDEX_DIR       = os.path.join(SCRIPT_DIR, INDEX_DIR.lstrip("./"))
SOURCES_DIR     = os.path.join(SCRIPT_DIR, SOURCES_DIR.lstrip("./"))
os.makedirs(INDEX_DIR, exist_ok=True)
os.makedirs(SOURCES_DIR, exist_ok=True)

# ─── 全局变量 ─────────────────────────────────────────────────────────
_embed_model   = None
_chroma_client = None
_collection    = None
_chunked_docs  = []  # 存储 chunk 文本，供后续 Diff 使用
_corpus_cache  = None
_opencc_converters = {}

_SCRIPT_PAIR_TEXT = (
    "赵趙韩韓卫衛齐齊晋晉陈陳郑鄭鲁魯吴吳颜顏显顯战戰国國东東"
    "西西诸諸临臨兴興师師问問请請谓謂与與为為举舉刘劉礼禮"
    "纲綱钟鍾乐樂说說会會观觀后後"
)
_S2T_FALLBACK_MAP = str.maketrans({pair[0]: pair[1] for pair in zip(_SCRIPT_PAIR_TEXT[::2], _SCRIPT_PAIR_TEXT[1::2])})
_T2S_FALLBACK_MAP = str.maketrans({pair[1]: pair[0] for pair in zip(_SCRIPT_PAIR_TEXT[::2], _SCRIPT_PAIR_TEXT[1::2])})

STATE_CHARS = "周秦晉晋魏趙赵韓韩楚齊齐燕衛卫魯鲁宋鄭郑陳陈蔡曹吳吴越"
YEAR_CHARS = "元零〇一二三四五六七八九十百千廿卅"
EVENT_TERMS = (
    "初命", "命", "為諸侯", "为诸侯", "諸侯", "诸侯", "九鼎", "分封",
    "伐", "攻", "弒", "弑", "盟", "立", "借救", "救周", "臨周", "临周",
    "胡服", "騎射", "骑射", "晉陽", "晋阳", "雍氏", "河西", "徐州",
    "相王", "稱王", "称王", "高都", "從約", "从约", "三版", "易子而食",
    "封禪", "封禅", "泰山", "嶧山", "峄山",
)
ROLE_WORDS = (
    "大夫", "卿", "公子", "太子", "天子", "王后", "夫人", "將軍", "将军",
    "國君", "国君", "使者", "君臣", "子孫", "子孙", "後", "后", "之君",
    "儒生", "博士",
)
NON_NAME_CHARS = set("之而其以於于為为乃且皆亦則则使令命告曰興兴師求患攻伐立賜赐與与會会盟入出來来去請请問问言臨临國国兵君王公侯年集")
PLACE_SUFFIX_CHARS = set("陽阴陵邑里臺台城關陘阪水河澤山原谷津渡池鄉野亭陌鄙塞道")
KNOWN_PLACES = {
    "晉陽", "晋陽", "晋阳", "安邑", "平陽", "平阳", "邯鄲", "邯郸", "大梁",
    "朝歌", "上黨", "上党", "咸陽", "咸阳", "絳", "绛",
}
NAME_CONTEXT_PATTERNS = (
    r'(?:以告|告|謂|谓|命|使|令|遣|請|请|召|問|问|對|对|與|与)([\u4e00-\u9fff]{2,3})',
    r'([\u4e00-\u9fff]{2,3})(?=曰)',
)
TITLE_NAME_PATTERNS = (
    r'(公子[\u4e00-\u9fff]{1,2})',
    r'(太史[\u4e00-\u9fff]{1,2})',
    r'(司馬[\u4e00-\u9fff]{1,2})',
)
PERSON_ALIAS_GROUPS = (
    ("知伯", ("知伯", "智伯", "知伯瑤", "智伯瑤", "知氏", "智氏")),
    ("趙襄子", ("趙襄子", "赵襄子", "趙毋卹", "趙毋恤", "赵毋恤", "趙无恤", "赵无恤")),
    ("韓康子", ("韓康子", "韩康子")),
    ("魏桓子", ("魏桓子",)),
    ("公子成", ("公子成",)),
    ("太史儋", ("太史儋",)),
    ("蘇代", ("蘇代", "苏代")),
    ("蘇秦", ("蘇秦", "苏秦")),
    ("張儀", ("張儀", "张仪")),
    ("顏率", ("顏率", "颜率")),
    ("肥義", ("肥義", "肥义")),
    ("張孟同", ("張孟同", "张孟同")),
    ("商君", ("商君",)),
)
SEMANTIC_REPLACEMENTS = (
    ("知伯瑤", "知伯"),
    ("智伯瑤", "知伯"),
    ("智伯", "知伯"),
    ("知氏", "知伯"),
    ("智氏", "知伯"),
    ("趙毋卹", "趙襄子"),
    ("趙毋恤", "趙襄子"),
    ("赵毋恤", "赵襄子"),
    ("趙无恤", "趙襄子"),
    ("赵无恤", "赵襄子"),
)
SEMANTIC_CHAR_MAP = str.maketrans({
    "帥": "率",
    "湛": "浸",
    "圍": "攻",
    "伐": "攻",
})


def _normalize_text(text: str) -> str:
    """清理空白，便于做字符串匹配。"""
    return re.sub(r"\s+", "", text.replace("\u3000", " ")).strip()


def _get_opencc_converter(config: str):
    if OpenCC is None:
        return None
    converter = _opencc_converters.get(config)
    if converter is None:
        converter = OpenCC(config)
        _opencc_converters[config] = converter
    return converter


def _convert_script(text: str, config: str) -> str:
    converter = _get_opencc_converter(config)
    if converter is not None:
        return converter.convert(text)
    if config == "s2t":
        return text.translate(_S2T_FALLBACK_MAP)
    if config == "t2s":
        return text.translate(_T2S_FALLBACK_MAP)
    return text


def _script_variants(text: str):
    normalized = _normalize_text(text)
    if not normalized:
        return []
    return _unique_keep_order([
        normalized,
        _convert_script(normalized, "s2t"),
        _convert_script(normalized, "t2s"),
    ])


def _unique_keep_order(items):
    seen = set()
    result = []
    for item in items:
        normalized = _normalize_text(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _is_place_like(term: str) -> bool:
    normalized = _normalize_text(term)
    if not normalized:
        return False
    if normalized in KNOWN_PLACES:
        return True
    if normalized[-1] in PLACE_SUFFIX_CHARS:
        return True
    return False


def _semantic_normalize(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""
    for source, target in SEMANTIC_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    return normalized.translate(SEMANTIC_CHAR_MAP)


def _comparison_variants(text: str):
    variants = []
    for variant in _script_variants(text):
        variants.append(variant)
        semantic_variant = _semantic_normalize(variant)
        if semantic_variant:
            variants.append(semantic_variant)
    return _unique_keep_order(variants)


def _extract_bigrams(text: str):
    bigrams = set()
    for normalized in _comparison_variants(text):
        if len(normalized) < 2:
            continue
        bigrams.update(
            normalized[i:i + 2]
            for i in range(len(normalized) - 1)
            if re.search(r'[\u4e00-\u9fff]', normalized[i:i + 2])
        )
    return bigrams


def _variants_overlap(left_items, right_items):
    left_variants = set()
    right_variants = set()
    for item in left_items:
        left_variants.update(_comparison_variants(item))
    for item in right_items:
        right_variants.update(_comparison_variants(item))
    return bool(left_variants & right_variants)


def _term_in_text_variants(term: str, text_variants) -> bool:
    for term_variant in _comparison_variants(term):
        for text_variant in text_variants:
            if term_variant in text_variant:
                return True
    return False


def _split_text_sentences(text: str):
    """按古文常见句读切句。"""
    parts = re.split(r'([。！？；\n])', text)
    sentences = []
    buf = ""
    for part in parts:
        if not part:
            continue
        buf += part
        if part in "。！？；\n":
            cleaned = buf.strip()
            if cleaned:
                sentences.append(cleaned)
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    return [s for s in sentences if _normalize_text(s)]


def _truncate_text(text: str, limit: int = 120) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _candidate_sentence_starts(sentences, focus_terms, max_starts: int = 8):
    if len(sentences) <= 2 or not focus_terms:
        return list(range(len(sentences)))

    starts = []
    for idx, sentence in enumerate(sentences):
        sentence_variants = _comparison_variants(sentence)
        if any(_term_in_text_variants(term, sentence_variants) for term in focus_terms):
            starts.extend([max(0, idx - 1), idx])

    if not starts:
        return list(range(len(sentences)))

    ordered = []
    seen = set()
    for idx in starts:
        if idx in seen or idx >= len(sentences):
            continue
        seen.add(idx)
        ordered.append(idx)
        if len(ordered) >= max_starts:
            break
    return ordered


def _dedupe_preserve_text(items, limit=None):
    seen = set()
    result = []
    for item in items:
        normalized = _normalize_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item.strip())
        if limit is not None and len(result) >= limit:
            break
    return result


def _build_text_evidence(source_text: str, target_text: str, max_items: int = 6):
    source_sentences = _split_text_sentences(source_text)
    target_sentences = _split_text_sentences(target_text)
    matcher = difflib.SequenceMatcher(a=source_sentences, b=target_sentences)

    deleted = []
    added = []
    changed = []
    retained = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            deleted.extend(source_sentences[i1:i2])
        elif tag == "insert":
            added.extend(target_sentences[j1:j2])
        elif tag == "replace":
            left = _truncate_text(" ".join(source_sentences[i1:i2]), 160)
            right = _truncate_text(" ".join(target_sentences[j1:j2]), 160)
            deleted.extend(source_sentences[i1:i2])
            added.extend(target_sentences[j1:j2])
            if left or right:
                changed.append((left or "（无）", right or "（无）"))
        elif tag == "equal":
            retained.extend(source_sentences[i1:i2])

    dedup_changed = []
    seen_pairs = set()
    for left, right in changed:
        key = (_normalize_text(left), _normalize_text(right))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        dedup_changed.append((_truncate_text(left, 160), _truncate_text(right, 160)))
        if len(dedup_changed) >= max_items:
            break

    return {
        "deleted": _dedupe_preserve_text(deleted, limit=max_items),
        "added": _dedupe_preserve_text(added, limit=max_items),
        "changed": dedup_changed,
        "retained": _dedupe_preserve_text(retained, limit=max_items),
    }


def _format_evidence_block(items, prefix="- "):
    if not items:
        return "- （未提取到明显项）"
    return "\n".join(f"{prefix}{item}" for item in items)


def _format_changed_block(items):
    if not items:
        return "- （未提取到明显改写对）"
    lines = []
    for left, right in items:
        lines.append(f"- 原句：{left}")
        lines.append(f"  改写：{right}")
    return "\n".join(lines)


def _build_analysis_prompt(source_title: str, window_text: str, target_text: str):
    evidence = _build_text_evidence(window_text, target_text)
    system_prompt = """你是严谨的古文文本分析助手，任务只是在“最相似的史料窗口”和“用户输入文段”之间做比较。

要求：
1. 只能比较这两段文本，不要把分析扩展到整篇史料或整部《资治通鉴》。
2. 先指出对应关系和差异，再解释这些差异可能为什么出现。
3. 可以讨论“节录、压缩、改写、换称谓、改叙事重心”等文本层面的原因，但必须紧扣可见证据。
4. 不得默认套用“怪力乱神”“政治隐喻”“敏感记载”等理由；证据不足就直接说明。
5. 每个判断尽量引用窗口文本或输入文本里的具体词句。"""

    user_prompt = f"""请比较下面两段文本，并分析它们为什么会出现这些差异。

【史料窗口 - {source_title}】
{window_text[:1200]}

【输入文段】
{target_text[:1200]}

【程序提取的差异证据】
### 窗口文本中有、输入文段中弱化或未出现的内容
{_format_evidence_block([_truncate_text(item, 160) for item in evidence["deleted"]])}

### 输入文段中有、窗口文本中未直接出现的内容
{_format_evidence_block([_truncate_text(item, 160) for item in evidence["added"]])}

### 可能对应但表述不同的句子
{_format_changed_block(evidence["changed"])}

### 两边共同保留的内容
{_format_evidence_block([_truncate_text(item, 160) for item in evidence["retained"]])}

请用 Markdown 输出，严格使用以下结构：

## 对应关系
- 说明这两个文本片段在讲同一件什么事，哪些信息是对应上的。

## 主要差异
- 具体指出哪部分信息更详细、哪部分更简略、哪部分换了说法。

## 差异成因分析
- 只分析这两个片段之间为什么会有这些差异。
- 每条标注为“高置信”或“低置信（推测）”。

## 结论
- 用 2 到 4 句话总结窗口文本和输入文本之间最关键的关系。

不要讨论红绿 diff，也不要泛泛谈整部书的编纂原则。"""
    return system_prompt, user_prompt


def _slice_long_sentence(sentence: str, chunk_size: int, overlap: int):
    """极长句子按字符滑窗切开，避免单段过长。"""
    cleaned = sentence.strip()
    if len(cleaned) <= chunk_size:
        return [cleaned]

    step = max(chunk_size - overlap, 40)
    windows = []
    start = 0
    while start < len(cleaned):
        window = cleaned[start:start + chunk_size].strip()
        if window:
            windows.append(window)
        if start + chunk_size >= len(cleaned):
            break
        start += step
    return windows


def _build_overlap_sentences(sentences, overlap: int):
    carry = []
    carry_len = 0
    for sent in reversed(sentences):
        carry.insert(0, sent)
        carry_len += len(sent)
        if carry_len >= overlap:
            break
    return carry


def _clean_source_text(raw: str) -> str:
    """去掉 Wikisource 导航、注释和编辑噪音。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r'◄[^►\n]+►', ' ', text)
    text = re.sub(r'姊妹计划\s*:\s*数据项', ' ', text)
    text = re.sub(r'回主目錄|回主目录|閲文言|阅读原文', ' ', text)
    text = re.sub(r'维基百科\s*中的：|维基百科\s*條目：|維基百科\s*中的：|維基百科\s*條目：', ' ', text)
    text = re.sub(r'\[[^\]]*编辑[^\]]*\]', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'〈[^〉]{0,120}〉', ' ', text)
    text = re.sub(r'○', ' ', text)

    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if any(marker in line for marker in ("来源:", "時間:", "时间:", "作者：")):
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_passage_features(text: str):
    rulers = []
    years = []
    names = []
    states = []

    for normalized in _script_variants(text):
        ruler_patterns = [
            rf'(?:東|西)?周[\u4e00-\u9fff]{{1,2}}王',
            rf'[{STATE_CHARS}][\u4e00-\u9fff]{{1,2}}(?:王|公|侯|君)',
            rf'[\u4e00-\u9fff]{{1,2}}(?:王|公|侯|君)(?=(?:[{YEAR_CHARS}]|\d)+年)',
            r'(?:秦)?始皇(?:帝)?',
        ]
        for pattern in ruler_patterns:
            rulers.extend(re.findall(pattern, normalized))

        years.extend(re.findall(rf'(?:[{YEAR_CHARS}]|\d)+年', normalized))

        for match in re.finditer(rf'[{STATE_CHARS}][\u4e00-\u9fff]{{1,2}}', normalized):
            term = match.group()
            suffix = term[1:]
            if term in rulers:
                continue
            if _is_place_like(term):
                continue
            if any(role in term for role in ROLE_WORDS):
                continue
            if any(ch in NON_NAME_CHARS for ch in suffix):
                continue
            if term.endswith(("之", "人", "者", "年")):
                continue
            names.append(term)

        for pattern in NAME_CONTEXT_PATTERNS:
            for term in re.findall(pattern, normalized):
                if term in rulers or term in years:
                    continue
                if len(term) < 2:
                    continue
                if _is_place_like(term):
                    continue
                if term[0] in NON_NAME_CHARS:
                    continue
                if any(role in term for role in ROLE_WORDS):
                    continue
                if term[-1] in "王公侯君年":
                    continue
                if any(ch in NON_NAME_CHARS for ch in term[1:]):
                    continue
                names.append(term)

        for pattern in TITLE_NAME_PATTERNS:
            for term in re.findall(pattern, normalized):
                if term in rulers or term in years:
                    continue
                if _is_place_like(term):
                    continue
                names.append(term)

        for canonical, aliases in PERSON_ALIAS_GROUPS:
            if any(alias in normalized for alias in aliases):
                names.append(canonical)

        for state in STATE_CHARS:
            if state in normalized:
                states.append(state)

    return {
        "rulers": _unique_keep_order(rulers),
        "years": _unique_keep_order(years),
        "names": _unique_keep_order(names),
        "states": _unique_keep_order(states),
    }


def _extract_query_features(query: str):
    features = _extract_passage_features(query)
    variants = _script_variants(query)
    comparison_variants = _comparison_variants(query)
    normalized = variants[0] if variants else ""
    phrases = []

    for variant in comparison_variants:
        segments = [seg for seg in re.split(r'[，。！？；、\s]+', variant) if seg.strip()]
        for seg in segments:
            compact = _normalize_text(seg)
            if len(compact) >= 4:
                phrases.append(compact[:12])

    events = []
    for term in EVENT_TERMS:
        if any(term in variant for variant in variants):
            events.append(term)

    focus_terms = (
        features["rulers"]
        + features["years"]
        + features["names"]
        + _unique_keep_order(phrases)[:4]
        + _unique_keep_order(events)
    )
    if not focus_terms:
        focus_terms = _unique_keep_order(phrases)[:4]
    if not focus_terms and normalized:
        focus_terms = [normalized[: min(len(normalized), 8)]]

    features.update({
        "normalized": normalized,
        "variants": variants,
        "comparison_variants": comparison_variants,
        "phrases": _unique_keep_order(phrases)[:4],
        "events": _unique_keep_order(events),
        "focus_terms": _unique_keep_order(focus_terms),
        "bigrams": _extract_bigrams(normalized),
    })
    return features


def _score_text_window(text: str, query_features: dict):
    text_variants = _comparison_variants(text)
    normalized = text_variants[0] if text_variants else ""
    if not normalized:
        return 0.0, [], 0.0

    matched_terms = []
    weighted_hits = 0.0
    total_weight = 0.0

    weighted_groups = [
        ("rulers", 3.2),
        ("years", 2.8),
        ("names", 3.0),
        ("phrases", 2.2),
        ("events", 1.6),
        ("states", 0.5),
    ]
    for key, weight in weighted_groups:
        terms = query_features.get(key, [])
        total_weight += len(terms) * weight
        for term in terms:
            if _term_in_text_variants(term, text_variants):
                weighted_hits += weight
                matched_terms.append(term)

    coverage = weighted_hits / total_weight if total_weight else 0.0
    ngram_overlap = (
        len(query_features["bigrams"] & _extract_bigrams(text)) / max(len(query_features["bigrams"]), 1)
    )
    sequence_ratio = max(
        difflib.SequenceMatcher(None, query_variant, text_variant).ratio()
        for query_variant in query_features.get("comparison_variants", query_features.get("variants", [query_features["normalized"]]))
        for text_variant in text_variants
    )

    passage_features = _extract_passage_features(text)
    penalty = 0.0
    if query_features["rulers"]:
        if not any(_term_in_text_variants(term, text_variants) for term in query_features["rulers"]):
            penalty += 0.32
        if passage_features["rulers"] and not _variants_overlap(query_features["rulers"], passage_features["rulers"]):
            penalty += 0.30
    if query_features["years"]:
        if not any(_term_in_text_variants(term, text_variants) for term in query_features["years"]):
            penalty += 0.24
        if passage_features["years"] and not _variants_overlap(query_features["years"], passage_features["years"]):
            penalty += 0.22
    if query_features["names"]:
        if not any(_term_in_text_variants(term, text_variants) for term in query_features["names"]):
            penalty += 0.24
        if passage_features["names"] and not _variants_overlap(query_features["names"], passage_features["names"]):
            penalty += 0.22
    if query_features["phrases"]:
        if not any(_term_in_text_variants(term, text_variants) for term in query_features["phrases"][:2]):
            # 短语是软锚点；若整体重叠已很强，不再额外惩罚古文换词/异文。
            if ngram_overlap < 0.35 and sequence_ratio < 0.45:
                penalty += 0.10
    if query_features["states"] and passage_features["states"]:
        if not _variants_overlap(query_features["states"], passage_features["states"]):
            penalty += 0.08

    bonus = 0.0
    if query_features["rulers"] and any(_term_in_text_variants(term, text_variants) for term in query_features["rulers"]):
        bonus += 0.10
    if query_features["years"] and any(_term_in_text_variants(term, text_variants) for term in query_features["years"]):
        bonus += 0.08
    if query_features["names"] and any(_term_in_text_variants(term, text_variants) for term in query_features["names"]):
        bonus += 0.08

    score = coverage * 0.58 + ngram_overlap * 0.24 + sequence_ratio * 0.18 + bonus - penalty
    return max(score, 0.0), _unique_keep_order(matched_terms), penalty


def _trim_around_focus_terms(text: str, focus_terms, target_len: int):
    compact = text.strip()
    if len(compact) <= target_len:
        return compact

    text_variants = _script_variants(compact)
    for term in sorted(focus_terms, key=len, reverse=True):
        for term_variant in _script_variants(term):
            for text_variant in text_variants:
                idx = text_variant.find(term_variant)
                if idx == -1:
                    continue
                start = max(0, idx - target_len // 3)
                end = min(len(compact), start + target_len)
                start = max(0, end - target_len)
                return compact[start:end].strip()

    return compact[:target_len].strip()


def _extract_best_snippet(doc_text: str, query: str, query_features: dict, sentences=None):
    target_len = max(90, min(220, len(_normalize_text(query)) * 3 + 60))
    sentences = sentences or _split_text_sentences(doc_text)
    if not sentences:
        return _trim_around_focus_terms(doc_text, query_features["focus_terms"], target_len), [], 0.0

    best_text = _trim_around_focus_terms(doc_text, query_features["focus_terms"], target_len)
    best_score, best_terms, best_penalty = _score_text_window(best_text, query_features)

    for start in _candidate_sentence_starts(sentences, query_features["focus_terms"]):
        window_parts = []
        window_len = 0
        for end in range(start, len(sentences)):
            sentence = sentences[end]
            window_parts.append(sentence)
            window_len += len(sentence)
            if window_len < max(60, target_len // 2):
                continue

            window_text = "".join(window_parts).strip()
            if window_len > int(target_len * 1.35):
                window_text = _trim_around_focus_terms(window_text, query_features["focus_terms"], target_len)

            score, matched_terms, penalty = _score_text_window(window_text, query_features)
            better_score = score > best_score + 1e-6
            similar_score = abs(score - best_score) <= 1e-6
            closer_length = abs(len(window_text) - target_len) < abs(len(best_text) - target_len)
            if better_score or (similar_score and closer_length):
                best_text = window_text
                best_score = score
                best_terms = matched_terms
                best_penalty = penalty

            if window_len >= target_len:
                break

    return best_text, best_terms, best_penalty


def _window_target_length(query: str) -> int:
    """自适应窗口长度：略长于输入文本，但不过度扩张。"""
    query_len = len(_normalize_text(query))
    return max(60, min(140, int(query_len * 1.6 + 20)))


def _extract_best_window(doc_text: str, query: str, query_features: dict, sentences=None):
    """提取用于展示和分析的紧凑窗口。"""
    target_len = _window_target_length(query)
    sentences = sentences or _split_text_sentences(doc_text)
    if not sentences:
        return _trim_around_focus_terms(doc_text, query_features["focus_terms"], target_len), [], 0.0

    best_text = _trim_around_focus_terms(doc_text, query_features["focus_terms"], target_len)
    best_score, best_terms, best_penalty = _score_text_window(best_text, query_features)

    for start in _candidate_sentence_starts(sentences, query_features["focus_terms"]):
        window_parts = []
        window_len = 0
        for end in range(start, len(sentences)):
            sentence = sentences[end]
            window_parts.append(sentence)
            window_len += len(sentence)
            if window_len < max(30, target_len // 2):
                continue

            window_text = "".join(window_parts).strip()
            if window_len > int(target_len * 1.2):
                window_text = _trim_around_focus_terms(window_text, query_features["focus_terms"], target_len)

            score, matched_terms, penalty = _score_text_window(window_text, query_features)
            improved = score > best_score + 0.02
            similar_score = abs(score - best_score) <= 0.02
            closer_length = abs(len(window_text) - target_len) < abs(len(best_text) - target_len)
            if improved or (similar_score and closer_length):
                best_text = window_text
                best_score = score
                best_terms = matched_terms
                best_penalty = penalty

            if window_len >= target_len:
                break

    return best_text, best_terms, best_penalty


def _prepare_retrieval_text(input_text: str):
    """检索前剥离司马光按语，避免污染查询。"""
    cleaned = input_text.strip()
    for pattern in (r'臣光曰[:：]?', r'臣光按[:：]?', r'光曰[:：]?'):
        match = re.search(pattern, cleaned)
        if not match:
            continue
        main_text = cleaned[:match.start()].strip(" \n：:，。")
        commentary = cleaned[match.start():].strip()
        if len(_normalize_text(main_text)) >= 5:
            return main_text, commentary
    return cleaned, ""


def _score_keyword_candidate(doc_text: str, title: str, query_features: dict, haystack_variants=None):
    """关键词打分按实体权重，而不是简单计数。"""
    if haystack_variants is None:
        haystack = _normalize_text(doc_text) + _normalize_text(title)
        haystack_variants = _comparison_variants(haystack)
    weighted_groups = [
        ("rulers", 3.6),
        ("years", 3.0),
        ("names", 3.4),
        ("phrases", 2.2),
        ("events", 1.2),
    ]

    matched_terms = []
    score = 0.0
    total = 0.0
    for key, weight in weighted_groups:
        terms = query_features.get(key, [])
        total += len(terms) * weight
        for term in terms:
            if _term_in_text_variants(term, haystack_variants):
                score += weight
                matched_terms.append(term)

    if query_features["states"]:
        state_hits = sum(1 for state in query_features["states"] if _term_in_text_variants(state, haystack_variants))
        score += min(state_hits * 0.2, 0.6)
        total += min(len(query_features["states"]) * 0.2, 0.6)

    normalized_score = score / total if total else 0.0
    return normalized_score, _unique_keep_order(matched_terms)


def _file_preference_key(item: dict):
    return (
        item.get("anchor_hits", 0),
        round(item.get("window_score", 0.0), 6),
        round(item.get("keyword_score", 0.0), 6),
        -round(item.get("penalty", 0.0), 6),
        round(item.get("semantic_score", 0.0), 6),
        round(item.get("score", 0.0), 6),
    )


def _select_best_candidates_per_file(items):
    best_by_file = {}
    for item in items:
        key = item["file"]
        existing = best_by_file.get(key)
        if existing is None or _file_preference_key(item) > _file_preference_key(existing):
            best_by_file[key] = item
    return list(best_by_file.values())


def _keyword_candidate_key(item: dict):
    return (
        item.get("anchor_hits", 0),
        round(item.get("keyword_score", 0.0), 6),
        len(item.get("keyword_terms", [])),
    )


def _rerank_seed_key(item: dict):
    return (
        item.get("anchor_hits", 0),
        round(item.get("keyword_score", 0.0), 6),
        round(item.get("semantic_score", 0.0), 6),
    )


def _select_keyword_candidates(corpus_entries, query_features: dict, limit: int = KEYWORD_CANDIDATES):
    anchor_terms = set(query_features["rulers"] + query_features["years"] + query_features["names"])
    scored = []
    for entry in corpus_entries:
        keyword_score, keyword_terms = _score_keyword_candidate(
            entry["text"],
            entry["title"],
            query_features,
            haystack_variants=entry.get("haystack_variants"),
        )
        if keyword_score <= 0:
            continue
        scored.append({
            "id": entry["id"],
            "text": entry["text"],
            "title": entry["title"],
            "file": entry["file"],
            "sentences": entry.get("sentences", []),
            "keyword_score": keyword_score,
            "keyword_terms": keyword_terms,
            "anchor_hits": len(anchor_terms.intersection(keyword_terms)),
        })

    scored.sort(key=_keyword_candidate_key, reverse=True)
    anchored = [item for item in scored if item["anchor_hits"] > 0]
    return (anchored or scored)[:limit]


def _limit_rerank_candidates(items, limit: int = MAX_RERANK_CANDIDATES):
    if len(items) <= limit:
        return list(items)
    return sorted(items, key=_rerank_seed_key, reverse=True)[:limit]

# ─── Embedding 模型（懒加载，用完即释放）──────────────────────────────
def get_embedder():
    """加载中文 Embedding 模型"""
    global _embed_model
    if _embed_model is None:
        print("📦 加载中文Embedding模型: shibing624/text2vec-base-chinese")
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
        print("✅ Embedding模型加载完成")
    return _embed_model

def free_embedder():
    """释放 Embedding 模型内存"""
    global _embed_model
    if _embed_model is not None:
        del _embed_model
        _embed_model = None
        gc.collect()
        print("🗑️  Embedding模型已释放")

# ─── ChromaDB 初始化 ──────────────────────────────────────────────────
def get_chroma_collection():
    """获取或创建 ChromaDB 集合"""
    global _chroma_client, _collection
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(
            path=INDEX_DIR,
            settings=ChromaSettings(anonymized_telemetry=False)
        )
    if _collection is None:
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"description": f"资治通鉴史料向量库 v{INDEX_VERSION}", "hnsw:space": "cosine"}
        )
    return _collection


def _invalidate_collection():
    global _collection
    _collection = None


def _call_collection(method_name: str, *args, **kwargs):
    """调用 Chroma collection；若句柄失效则自动重连一次。"""
    for attempt in range(2):
        coll = get_chroma_collection()
        try:
            return getattr(coll, method_name)(*args, **kwargs)
        except NotFoundError:
            if attempt == 0:
                print("⚠️ Chroma collection 句柄失效，正在重新连接...")
                _invalidate_collection()
                continue
            raise


def _collection_count() -> int:
    return _call_collection("count")


def _corpus_cache_path() -> str:
    return os.path.join(INDEX_DIR, f"{COLLECTION_NAME}_corpus_cache_v{CORPUS_CACHE_VERSION}.json")


def _reset_corpus_cache(remove_file: bool = False):
    global _corpus_cache
    _corpus_cache = None
    if remove_file:
        try:
            os.remove(_corpus_cache_path())
        except FileNotFoundError:
            pass


def _load_corpus_cache(expected_count: int):
    cache_path = _corpus_cache_path()
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None

    if payload.get("collection_name") != COLLECTION_NAME:
        return None
    if payload.get("collection_count") != expected_count:
        return None
    if payload.get("opencc_enabled") != (OpenCC is not None):
        return None

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None
    return entries


def _write_corpus_cache(entries, collection_count: int):
    cache_path = _corpus_cache_path()
    payload = {
        "collection_name": COLLECTION_NAME,
        "collection_count": collection_count,
        "opencc_enabled": OpenCC is not None,
        "entries": entries,
    }
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _build_corpus_entry(doc_id: str, text: str, metadata: dict):
    title = metadata.get("title", "")
    file_name = metadata.get("file", "")
    haystack = _normalize_text(text) + _normalize_text(title)
    return {
        "id": doc_id,
        "text": text,
        "title": title,
        "file": file_name,
        "sentences": _split_text_sentences(text),
        "haystack_variants": _comparison_variants(haystack),
    }


def _get_corpus_entries():
    global _corpus_cache
    if _corpus_cache is not None:
        return _corpus_cache

    collection_count = _collection_count()
    if collection_count <= 0:
        _corpus_cache = []
        return _corpus_cache

    cached_entries = _load_corpus_cache(collection_count)
    if cached_entries is not None:
        _corpus_cache = cached_entries
        return _corpus_cache

    all_docs = _call_collection("get", include=["documents", "metadatas"])
    entries = []
    for i, doc in enumerate(all_docs["documents"]):
        entries.append(_build_corpus_entry(all_docs["ids"][i], doc, all_docs["metadatas"][i]))

    _corpus_cache = entries
    _write_corpus_cache(entries, collection_count)
    return _corpus_cache

# ─── 文本分块（古文友好）──────────────────────────────────────────────
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """按事件粒度分块，避免一段混入多个时间点。"""
    cleaned = _clean_source_text(text)
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', cleaned) if p.strip()]
    if not paragraphs:
        paragraphs = [cleaned]

    chunks = []
    current = []
    current_len = 0

    def flush():
        nonlocal current, current_len
        if not current:
            return
        chunk = "".join(current).strip()
        if len(_normalize_text(chunk)) >= 40:
            chunks.append(chunk)
        current = _build_overlap_sentences(current, overlap)
        current_len = sum(len(part) for part in current)

    for para in paragraphs:
        sentences = _split_text_sentences(para) or [para]
        for sentence in sentences:
            for piece in _slice_long_sentence(sentence, chunk_size, overlap):
                if current and current_len + len(piece) > chunk_size:
                    flush()
                current.append(piece)
                current_len += len(piece)
        flush()

    if current:
        chunk = "".join(current).strip()
        if len(_normalize_text(chunk)) >= 40:
            chunks.append(chunk)

    return _unique_keep_order(c for c in chunks if 40 <= len(_normalize_text(c)) <= 1200)

# ─── 史料加载 ─────────────────────────────────────────────────────────
def load_source_documents():
    """从 sources/ 目录加载所有 TXT 文件"""
    docs = []
    if not os.path.exists(SOURCES_DIR):
        os.makedirs(SOURCES_DIR)
        print(f"⚠️ 已创建 {SOURCES_DIR}/，请放入史料TXT文件！")
        return docs

    for fname in sorted(os.listdir(SOURCES_DIR)):
        if not fname.endswith('.txt'):
            continue
        fpath = os.path.join(SOURCES_DIR, fname)
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            raw = f.read()
        text = _clean_source_text(raw)
        if len(text) > 50:
            title = fname.replace('.txt', '')
            docs.append({"text": text, "title": title, "file": fname})
            print(f"  ✅ 加载: {fname} ({len(text)} chars)")

    return docs

# ─── 构建向量索引 ─────────────────────────────────────────────────────
def build_index():
    """构建向量索引（从 sources/ 加载史料，向量化，存入 ChromaDB）"""
    global _chunked_docs

    coll = get_chroma_collection()

    # 检查是否已有索引
    existing = _collection_count()
    if existing > 0:
        print(f"📂 发现已有索引（{existing} 条），直接加载")
        return coll

    print("🆕 正在构建向量索引...")
    _reset_corpus_cache(remove_file=True)
    docs = load_source_documents()
    if not docs:
        print("⚠️ 没有找到任何史料文件！")
        return None

    # 分块
    embedder = get_embedder()
    all_chunks = []
    seen_chunks = set()
    duplicate_chunks = 0
    for doc in docs:
        chunks = chunk_text(doc["text"])
        for chunk in chunks:
            normalized_chunk = _normalize_text(chunk)
            if normalized_chunk in seen_chunks:
                duplicate_chunks += 1
                continue
            seen_chunks.add(normalized_chunk)
            all_chunks.append({
                "text": chunk,
                "title": doc["title"],
                "file": doc["file"],
            })

    print(f"📚 共 {len(docs)} 个文件，分 {len(all_chunks)} 个文本块")
    if duplicate_chunks:
        print(f"   ↪ 跳过重复文本块 {duplicate_chunks} 条")

    # 批量向量化（分批避免内存溢出）
    BATCH = 50
    ids = []
    embeddings = []
    metadatas = []
    documents = []

    for i, chunk in enumerate(all_chunks):
        emb = embedder.encode([chunk["text"]], convert_to_numpy=True)[0].tolist()
        chunk_id = f"chunk_{i:05d}"
        ids.append(chunk_id)
        embeddings.append(emb)
        metadatas.append({
            "title": chunk["title"],
            "file": chunk["file"],
        })
        documents.append(chunk["text"])

        if len(ids) >= BATCH or i == len(all_chunks) - 1:
            _call_collection("add", ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
            print(f"  已写入 {len(ids)} 条... ({(i+1)*100//len(all_chunks)}%)")
            ids, embeddings, metadatas, documents = [], [], [], []

    # 保存 chunk 文本供 Diff 使用
    _chunked_docs = {f"chunk_{i:05d}": c for i, c in enumerate(all_chunks)}

    print(f"✅ 向量索引构建完成！共 {_collection_count()} 条记录")
    print(f"   保存位置: {INDEX_DIR}/")
    return coll

# ─── 语义检索 ─────────────────────────────────────────────────────────
def retrieve(query: str, top_k: int = TOP_K):
    """混合检索：加权关键词召回 + 语义召回 + 片段重排。"""
    query = _normalize_text(query)
    if not query:
        return []

    embedder = get_embedder()
    get_chroma_collection()
    query_features = _extract_query_features(query)
    print(f"  🔑 检索锚点: {query_features['focus_terms']}")
    anchor_terms = set(
        query_features["rulers"] + query_features["years"] + query_features["names"]
    )
    corpus_entries = _get_corpus_entries()
    corpus_by_id = {entry["id"]: entry for entry in corpus_entries}

    candidate_map = {}

    def upsert_candidate(
        result_id,
        text,
        title,
        file_name,
        semantic_score=0.0,
        keyword_score=0.0,
        keyword_terms=None,
        anchor_hits=0,
        sentences=None,
    ):
        candidate = candidate_map.setdefault(result_id, {
            "id": result_id,
            "source_text": text,
            "title": title,
            "file": file_name,
            "semantic_score": 0.0,
            "keyword_score": 0.0,
            "keyword_terms": [],
            "anchor_hits": 0,
            "sentences": sentences or [],
        })
        candidate["semantic_score"] = max(candidate["semantic_score"], semantic_score)
        candidate["keyword_score"] = max(candidate["keyword_score"], keyword_score)
        candidate["keyword_terms"] = _unique_keep_order(candidate["keyword_terms"] + (keyword_terms or []))
        candidate["anchor_hits"] = max(candidate["anchor_hits"], anchor_hits)
        if sentences and not candidate["sentences"]:
            candidate["sentences"] = sentences

    keyword_candidates = _select_keyword_candidates(corpus_entries, query_features, limit=KEYWORD_CANDIDATES)
    for item in keyword_candidates:
        upsert_candidate(
            item["id"],
            item["text"],
            item["title"],
            item["file"],
            keyword_score=item["keyword_score"],
            keyword_terms=item["keyword_terms"],
            anchor_hits=item["anchor_hits"],
            sentences=item.get("sentences"),
        )

    query_emb = embedder.encode([query], convert_to_numpy=True)[0].tolist()
    results = _call_collection(
        'query',
        query_embeddings=[query_emb],
        n_results=max(top_k * 4, SEMANTIC_CANDIDATES),
        include=["documents", "metadatas", "distances"]
    )
    for i in range(len(results["documents"][0])):
        dist = results["distances"][0][i]
        semantic_score = max(0.0, 1.0 - dist / 2.0)
        result_id = results["ids"][0][i]
        corpus_entry = corpus_by_id.get(result_id)
        upsert_candidate(
            result_id,
            results["documents"][0][i],
            results["metadatas"][0][i].get("title", ""),
            results["metadatas"][0][i].get("file", ""),
            semantic_score=semantic_score,
            sentences=(corpus_entry or {}).get("sentences", []),
        )

    reranked = []
    for candidate in _limit_rerank_candidates(candidate_map.values(), limit=MAX_RERANK_CANDIDATES):
        snippet, snippet_terms, penalty = _extract_best_snippet(
            candidate["source_text"],
            query,
            query_features,
            sentences=candidate.get("sentences"),
        )
        window_score, matched_terms, _ = _score_text_window(snippet, query_features)
        all_matched_terms = _unique_keep_order(candidate["keyword_terms"] + snippet_terms + matched_terms)
        anchor_hits = max(candidate.get("anchor_hits", 0), len(anchor_terms.intersection(all_matched_terms)))
        final_score = (
            candidate["semantic_score"] * 0.15
            + candidate["keyword_score"] * 0.30
            + window_score * 0.55
        )
        final_score = max(0.0, min(final_score - penalty * 0.05, 0.99))
        reranked.append({
            "id": candidate["id"],
            "text": snippet,
            "source_text": candidate["source_text"],
            "title": candidate["title"],
            "file": candidate["file"],
            "sentences": candidate.get("sentences", []),
            "score": final_score,
            "matched_terms": all_matched_terms,
            "semantic_score": candidate["semantic_score"],
            "keyword_score": candidate["keyword_score"],
            "window_score": window_score,
            "penalty": penalty,
            "anchor_hits": anchor_hits,
        })

    reranked.sort(key=lambda item: item["score"], reverse=True)

    collapsed = []
    seen_snippets = set()
    for item in reranked:
        key = _normalize_text(item["text"])[:140]
        if not key or key in seen_snippets:
            continue
        seen_snippets.add(key)
        collapsed.append(item)

    anchor_matched = [
        item for item in collapsed
        if not anchor_terms or anchor_terms.intersection(item.get("matched_terms", []))
    ]
    candidate_pool = [item for item in (anchor_matched or collapsed) if item["score"] >= MIN_RESULT_SCORE]
    if not candidate_pool:
        candidate_pool = anchor_matched or collapsed

    file_best = _select_best_candidates_per_file(candidate_pool)
    final_results = sorted(
        file_best,
        key=lambda item: (item["score"], item["window_score"], item["anchor_hits"]),
        reverse=True,
    )
    if len(final_results) >= top_k:
        return final_results[:top_k]

    for item in candidate_pool:
        if item in final_results:
            continue
        final_results.append(item)
        if len(final_results) >= top_k:
            break

    return final_results

def _normalized_index_map(text: str):
    normalized_chars = []
    index_map = []
    for idx, char in enumerate(text):
        if char.isspace() or char == "\u3000":
            continue
        normalized_chars.append(char)
        index_map.append(idx)
    return "".join(normalized_chars), index_map


def _find_window_span(source_text: str, window_text: str):
    normalized_source, source_map = _normalized_index_map(source_text)
    normalized_window = _normalize_text(window_text)
    if not normalized_source or not normalized_window:
        return None

    idx = normalized_source.find(normalized_window)
    if idx >= 0:
        start = source_map[idx]
        end = source_map[idx + len(normalized_window) - 1] + 1
        return start, end

    matcher = difflib.SequenceMatcher(a=normalized_source, b=normalized_window)
    match = matcher.find_longest_match(0, len(normalized_source), 0, len(normalized_window))
    if match.size < max(12, len(normalized_window) // 3):
        return None

    start = source_map[match.a]
    end = source_map[min(match.a + match.size - 1, len(source_map) - 1)] + 1
    return start, end


def _html_similarity_window(source_text: str, window_text: str, context_chars: int = 60) -> str:
    """显示原始史料中的最佳相似窗口，用红框标记。"""
    span = _find_window_span(source_text, window_text)
    if span is None:
        escaped_window = html.escape(window_text.strip()).replace("\n", "<br>")
        return f"""<div style="font-family:serif;line-height:1.9;padding:10px;
                     background:#fafafa;border-radius:6px;border:1px solid #eee;">
          <div style="color:#991b1b;font-size:0.92em;margin-bottom:8px;">红框内是与输入文段最相似的窗口</div>
          <div style="border:2px solid #dc2626;border-radius:8px;padding:10px 12px;background:#fff5f5;">{escaped_window}</div>
        </div>"""

    start, end = span
    prefix_start = max(0, start - context_chars)
    suffix_end = min(len(source_text), end + context_chars)
    prefix = source_text[prefix_start:start].strip()
    window = source_text[start:end].strip() or window_text.strip()
    suffix = source_text[end:suffix_end].strip()
    prefix_ellipsis = "…" if prefix_start > 0 and prefix else ""
    suffix_ellipsis = "…" if suffix_end < len(source_text) and suffix else ""

    def render_text(text):
        return html.escape(text).replace("\n", "<br>")

    return f"""<div style="font-family:serif;line-height:1.9;padding:10px;
                 background:#fafafa;border-radius:6px;border:1px solid #eee;">
      <div style="color:#991b1b;font-size:0.92em;margin-bottom:8px;">红框内是与输入文段最相似的窗口</div>
      <div style="color:#4b5563;">{render_text(prefix_ellipsis + prefix)}</div>
      <div style="border:2px solid #dc2626;border-radius:8px;padding:10px 12px;margin:8px 0;background:#fff5f5;">{render_text(window)}</div>
      <div style="color:#4b5563;">{render_text(suffix + suffix_ellipsis)}</div>
    </div>"""

# ─── LLM 分析（调用 Ollama） ──────────────────────────────────────────
def call_llm(prompt: str, timeout: int = 120, system_prompt: str | None = None) -> str:
    """通过 Ollama API 调用本地 LLM"""
    import json
    import http.client
    from urllib.parse import urlparse

    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": LLM_TEMPERATURE,
            "num_predict": LLM_NUM_PREDICT,
            "num_ctx": LLM_NUM_CTX,
        },
    }
    if system_prompt:
        payload["system"] = system_prompt
    parsed = urlparse(OLLAMA_BASE_URL)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    try:
        conn.request(
            "POST",
            "/api/generate",
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status >= 400:
            return f"⚠️ LLM 调用失败：HTTP {resp.status} {resp.reason}"
        result = json.loads(raw.decode("utf-8"))
        return result.get("response", "").strip()
    except Exception as e:
        return f"⚠️ LLM 调用失败：{str(e)}\n请确认 Ollama 已启动，且本机可访问 {OLLAMA_BASE_URL}"
    finally:
        conn.close()

# ─── 完整分析流程 ─────────────────────────────────────────────────────
def analyze_zztj_text(input_text: str) -> str:
    """
    核心函数：输入周纪文本 → 检索 → Diff → LLM分析 → Markdown报告
    """
    if not input_text or len(input_text.strip()) < 5:
        return "# ⚠️ 输入文本太短\n请输入一段完整的《资治通鉴·周纪》文本。"

    retrieval_text, stripped_commentary = _prepare_retrieval_text(input_text)
    if len(_normalize_text(retrieval_text)) < 5:
        retrieval_text = input_text.strip()

    # Step 1: 确保索引存在
    if _collection_count() == 0:
        print("⚠️ 索引为空，正在构建...")
        build_index()
        if _collection_count() == 0:
            return "# ⚠️ 史料库为空\n请先将《左传》《史记》等史料 TXT 文件放入 `sources/` 目录。"

    # Step 2: 语义检索
    print(f"🔍 检索相关史料: {retrieval_text[:30]}...")
    query_features = _extract_query_features(retrieval_text)
    results = retrieve(retrieval_text, top_k=TOP_K)

    # Step 3: 生成报告
    report_lines = [
        "# 《资治通鉴·周纪》文本分析报告",
        f"**输入文本**：{input_text.strip()[:200]}{'...' if len(input_text) > 200 else ''}",
        f"**检索片段**：{retrieval_text[:200]}{'...' if len(retrieval_text) > 200 else ''}",
        f"**检索模型**：`shibing624/text2vec-base-chinese`（中文专用，768维）",
        f"**LLM模型**：`{LLM_MODEL}`（本地 Ollama）",
        f"**匹配史料数**：{len(results)} 条",
        "",
        "---",
        "",
    ]
    if stripped_commentary:
        report_lines += [
            "> 已自动忽略 `臣光曰` 之后的按语内容，避免污染检索结果。",
            "",
        ]

    for i, res in enumerate(results, 1):
        score_str = f"（匹配度 {res['score']*100:.1f}%）"
        report_lines += [
            f"## 匹配 {i}：{res['title']} {score_str}",
            f"**来源文件**：`{res['file']}`",
            f"**命中锚点**：{', '.join(res['matched_terms'][:8]) if res.get('matched_terms') else '语义相似'}",
            "",
            "> " + res["text"].replace("\n", " "),
            "",
            "### 🎯 最相似窗口",
            "",
        ]

        # Step 4: LLM 分析
        print(f"🤖 LLM分析 {i}...")
        window_text, _, _ = _extract_best_window(
            res["source_text"],
            retrieval_text,
            query_features,
            sentences=res.get("sentences"),
        )
        report_lines += [
            _html_similarity_window(res["source_text"], window_text),
            "",
            f"**窗口长度**：约 {len(_normalize_text(window_text))} 字（自适应窗口）",
            "",
        ]
        llm_system_prompt, llm_prompt = _build_analysis_prompt(
            res["title"], window_text, retrieval_text
        )
        analysis = call_llm(llm_prompt, system_prompt=llm_system_prompt)
        report_lines += [
            "### 🧠 窗口对比分析",
            analysis,
            "",
            "---",
            "",
        ]

    report_lines += [
        "## 💡 使用说明",
        "本报告由本地 AI 自动生成。",
        f"- Embedding：`shibing624/text2vec-base-chinese`（中文专用，768维向量）",
        f"- LLM：`{LLM_MODEL}`（本地运行）",
        f"- LLM 参数：temperature={LLM_TEMPERATURE}, num_ctx={LLM_NUM_CTX}, num_predict={LLM_NUM_PREDICT}",
        f"- 史料库：{_collection_count()} 条记录",
        "",
        "**下一步优化**：",
        "- 补充完整《左传》《国语》《通鉴考异》放入 sources/ 目录",
        "- 用环境变量切换更强的中文模型，例如 `ZZTJ_LLM_MODEL=qwen2.5:7b` 或更高规格",
    ]

    return "\n".join(report_lines)


# ─── 命令行交互入口 ───────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  《资治通鉴·周纪》智能比对 Agent")
    print("=" * 60)
    print(f"  Embedding: {EMBED_MODEL}")
    print(f"  LLM: {LLM_MODEL}")
    print(f"  史料目录: {SOURCES_DIR}")
    print(f"  索引目录: {INDEX_DIR}")
    print("=" * 60)

    build_index()

    print("\n📖 输入《资治通鉴·周纪》文本进行分析（按 Ctrl+C 退出）：\n")
    while True:
        try:
            text = input("输入文本 → ").strip()
            if not text:
                continue
            if text.lower() in ("q", "quit", "exit"):
                break
            report = analyze_zztj_text(text)
            print("\n" + "=" * 60)
            print(report)
            print("=" * 60)
        except (KeyboardInterrupt, EOFError):
            print("\n👋 再见！")
            break

if __name__ == "__main__":
    main()
