# 项目关键技术代码片段提炼

下面按“关键技术 -> 文件名 -> 核心代码片段 -> 作用”整理本项目最值得讲的实现。

---

## 1. 数据抓取与 HTML 正文提取

文件：`download_classics.py`

```python
def fetch(url, timeout=20):
    cmd = [
        'curl', '-sL', '--max-time', str(timeout),
        '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        '--tlsv1.2', url
    ]
    r = subprocess.run(cmd, capture_output=True)
    raw = r.stdout
    return raw.decode('utf-8', errors='replace') if raw else None

def extract_text(html):
    if not html:
        return None
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<table[^>]*>.*?</table>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<sup[^>]*>.*?</sup>', '', html, flags=re.DOTALL | re.IGNORECASE)

    text = None
    for pat in [
        r'<div[^>]*id="mw-content-text"[^>]*>(.*?)<div[^>]*class="printfooter"',
        r'<div[^>]*class="[^"]*mw-parser-output[^"]*"[^>]*>(.*?)</div>\s*</div>',
        r'<div[^>]*id="bodyContent"[^>]*>(.*?)<div[^>]*id="footer"',
    ]:
        m = re.search(pat, html, re.DOTALL)
        if m and len(m.group(1)) > 500:
            text = m.group(1)
            break

    text = re.sub(r'<[^>]+>', ' ', text or html)
    text = clean_extracted_text(text)
    return text if len(text) > 200 else None
```

作用：用 `curl` 稳定抓取 Wikisource 页面，再通过正则抽取正文区域，得到可进入后续处理的纯文本。

源码位置：
[download_classics.py:20](/Users/tv/Desktop/zztj-agent/download_classics.py#L20)
[download_classics.py:62](/Users/tv/Desktop/zztj-agent/download_classics.py#L62)

---

## 2. 史料噪音清洗

文件：`download_classics.py`

```python
def clean_extracted_text(text):
    text = html_lib.unescape(text).replace("\xa0", " ")
    text = re.sub(r'◄[^►\n]+►', ' ', text)
    text = re.sub(r'姊妹计划\s*:\s*数据项', ' ', text)
    text = re.sub(r'回主目錄|回主目录|閲文言|维基大典 文：|維基大典 文：|本维基文库', ' ', text)
    text = re.sub(r'维基百科\s*中的：|维基百科\s*條目：|維基百科\s*中的：|維基百科\s*條目：', ' ', text)
    text = re.sub(r'\[[^\]]*编辑[^\]]*\]', ' ', text, flags=re.IGNORECASE)

    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(marker in line for marker in ("作者：", "姊妹计划", "数据项", "回主目錄", "回主目录")) and len(line) < 120:
            continue
        lines.append(line)

    text = "\n".join(lines)
    sentences = [seg.strip() for seg in re.split(r'(?<=[。！？；])\s*', text) if len(seg.strip()) > 4]
    return "\n".join(sentences)
```

作用：把导航、编辑标记、百科噪音和多余空白剥掉，保证向量化之前的数据足够干净。

源码位置：
[download_classics.py:35](/Users/tv/Desktop/zztj-agent/download_classics.py#L35)

---

## 3. 繁简转换与语义归一

文件：`zztj_agent.py`

```python
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
```

作用：把“知伯/智伯”“趙毋恤/趙襄子”“帥/率”“湛/浸”这类古籍常见变体统一到更稳定的比较形式上。

源码位置：
[zztj_agent.py:186](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L186)

---

## 4. 古文实体特征抽取

文件：`zztj_agent.py`

```python
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
        ]
        for pattern in ruler_patterns:
            rulers.extend(re.findall(pattern, normalized))

        years.extend(re.findall(rf'(?:[{YEAR_CHARS}]|\d)+年', normalized))

        for pattern in NAME_CONTEXT_PATTERNS:
            for term in re.findall(pattern, normalized):
                if term in rulers or term in years:
                    continue
                if _is_place_like(term):
                    continue
                names.append(term)

        for canonical, aliases in PERSON_ALIAS_GROUPS:
            if any(alias in normalized for alias in aliases):
                names.append(canonical)
```

作用：把查询和候选文本里的“君主、年份、人名、国名”提炼出来，给后面的混合检索做硬约束。

源码位置：
[zztj_agent.py:443](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L443)

---

## 5. 检索锚点构造

文件：`zztj_agent.py`

```python
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

    focus_terms = (
        features["rulers"]
        + features["years"]
        + features["names"]
        + _unique_keep_order(phrases)[:4]
        + _unique_keep_order(events)
    )
```

作用：把输入文本压缩成一组“检索锚点”，比如“顯王”“三十三年”“顏率”“九鼎”，后续重排全围绕这些锚点展开。

源码位置：
[zztj_agent.py:515](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L515)

---

## 6. 按语剥离

文件：`zztj_agent.py`

```python
def _prepare_retrieval_text(input_text: str):
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
```

作用：如果输入里夹着“臣光曰”等评论性文字，检索时先剥离，只保留事件正文，避免污染召回。

源码位置：
[zztj_agent.py:735](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L735)

---

## 7. 古文友好的分块

文件：`zztj_agent.py`

```python
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    cleaned = _clean_source_text(text)
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', cleaned) if p.strip()]
    if not paragraphs:
        paragraphs = [cleaned]

    chunks = []
    current = []
    current_len = 0

    def flush():
        nonlocal current, current_len
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
```

作用：不是按固定字数硬切，而是“段落 -> 句子 -> 长句滑窗 -> 重叠保留”地切块，更适合古文事件粒度。

源码位置：
[zztj_agent.py:861](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L861)

---

## 8. Embedding 模型懒加载

文件：`zztj_agent.py`

```python
def get_embedder():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model

def free_embedder():
    global _embed_model
    if _embed_model is not None:
        del _embed_model
        _embed_model = None
        gc.collect()
```

作用：向量模型只在真正需要时加载，减少本地机器启动时的内存压力。

源码位置：
[zztj_agent.py:801](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L801)

---

## 9. 本地向量数据库与容错

文件：`zztj_agent.py`

```python
def get_chroma_collection():
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

def _call_collection(method_name: str, *args, **kwargs):
    for attempt in range(2):
        coll = get_chroma_collection()
        try:
            return getattr(coll, method_name)(*args, **kwargs)
        except NotFoundError:
            if attempt == 0:
                _invalidate_collection()
                continue
            raise
```

作用：使用本地持久化 ChromaDB 保存向量索引，并在 collection 句柄失效时自动重连。

源码位置：
[zztj_agent.py:822](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L822)

---

## 10. 批量建索引

文件：`zztj_agent.py`

```python
def build_index():
    coll = get_chroma_collection()
    existing = _collection_count()
    if existing > 0:
        return coll

    docs = load_source_documents()
    embedder = get_embedder()
    all_chunks = []
    for doc in docs:
        chunks = chunk_text(doc["text"])
        for chunk in chunks:
            all_chunks.append({
                "text": chunk,
                "title": doc["title"],
                "file": doc["file"],
            })

    for i, chunk in enumerate(all_chunks):
        emb = embedder.encode([chunk["text"]], convert_to_numpy=True)[0].tolist()
        ids.append(f"chunk_{i:05d}")
        embeddings.append(emb)
        metadatas.append({"title": chunk["title"], "file": chunk["file"]})
        documents.append(chunk["text"])
        if len(ids) >= BATCH or i == len(all_chunks) - 1:
            _call_collection("add", ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
```

作用：把文本块批量向量化并写入 ChromaDB，形成整个项目的检索底座。

源码位置：
[zztj_agent.py:923](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L923)

---

## 11. 加权关键词打分

文件：`zztj_agent.py`

```python
def _score_keyword_candidate(doc_text: str, title: str, query_features: dict):
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
```

作用：不是简单数关键词个数，而是给“君主、年份、人名”更高权重，保证历史检索更稳。

源码位置：
[zztj_agent.py:749](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L749)

---

## 12. 窗口级重排

文件：`zztj_agent.py`

```python
def _score_text_window(text: str, query_features: dict):
    text_variants = _comparison_variants(text)
    weighted_groups = [
        ("rulers", 3.2),
        ("years", 2.8),
        ("names", 3.0),
        ("phrases", 2.2),
        ("events", 1.6),
        ("states", 0.5),
    ]
    ...
    coverage = weighted_hits / total_weight if total_weight else 0.0
    ngram_overlap = len(query_features["bigrams"] & _extract_bigrams(text)) / max(len(query_features["bigrams"]), 1)
    sequence_ratio = max(
        difflib.SequenceMatcher(None, query_variant, text_variant).ratio()
        for query_variant in query_features.get("comparison_variants", query_features.get("variants", [query_features["normalized"]]))
        for text_variant in text_variants
    )
    score = coverage * 0.58 + ngram_overlap * 0.24 + sequence_ratio * 0.18 + bonus - penalty
```

作用：对候选片段做更细粒度的重排，融合实体命中率、n-gram 重合、序列相似和惩罚项。

源码位置：
[zztj_agent.py:558](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L558)

---

## 13. 最佳证据片段抽取

文件：`zztj_agent.py`

```python
def _extract_best_snippet(doc_text: str, query: str, query_features: dict):
    target_len = max(90, min(220, len(_normalize_text(query)) * 3 + 60))
    sentences = _split_text_sentences(doc_text)
    best_text = _trim_around_focus_terms(doc_text, query_features["focus_terms"], target_len)
    best_score, best_terms, best_penalty = _score_text_window(best_text, query_features)

    for start in range(len(sentences)):
        window_parts = []
        window_len = 0
        for end in range(start, len(sentences)):
            sentence = sentences[end]
            window_parts.append(sentence)
            window_len += len(sentence)
            if window_len < max(60, target_len // 2):
                continue
            window_text = "".join(window_parts).strip()
            score, matched_terms, penalty = _score_text_window(window_text, query_features)
            if score > best_score + 1e-6:
                best_text = window_text
```

作用：候选文档可能很长，这里再在文档内部滑窗，抽出最能证明“就是它”的一小段。

源码位置：
[zztj_agent.py:650](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L650)

---

## 14. 混合检索主链路

文件：`zztj_agent.py`

```python
def retrieve(query: str, top_k: int = TOP_K):
    embedder = get_embedder()
    query_features = _extract_query_features(query)
    candidate_map = {}

    all_docs = _call_collection('get', include=['documents', 'metadatas'])
    for i, doc in enumerate(all_docs['documents']):
        keyword_score, keyword_terms = _score_keyword_candidate(doc, title, query_features)
        if keyword_score > 0:
            upsert_candidate(..., keyword_score=keyword_score, keyword_terms=keyword_terms)

    query_emb = embedder.encode([query], convert_to_numpy=True)[0].tolist()
    results = _call_collection('query', query_embeddings=[query_emb], n_results=max(top_k * 4, SEMANTIC_CANDIDATES), include=["documents", "metadatas", "distances"])
    for i in range(len(results["documents"][0])):
        semantic_score = max(0.0, 1.0 - results["distances"][0][i] / 2.0)
        upsert_candidate(..., semantic_score=semantic_score)

    for candidate in candidate_map.values():
        snippet, snippet_terms, penalty = _extract_best_snippet(candidate["source_text"], query, query_features)
        window_score, matched_terms, _ = _score_text_window(snippet, query_features)
        final_score = (
            candidate["semantic_score"] * 0.15
            + candidate["keyword_score"] * 0.30
            + window_score * 0.55
        )
```

作用：这是项目最核心的一段，真正把“关键词召回 + 向量召回 + 窗口重排”串成一个可用的混合检索系统。

源码位置：
[zztj_agent.py:995](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L995)

---

## 15. Prompt 构造

文件：`zztj_agent.py`

```python
def _build_analysis_prompt(source_title: str, window_text: str, target_text: str):
    evidence = _build_text_evidence(window_text, target_text)
    system_prompt = """你是严谨的古文文本分析助手..."""

    user_prompt = f"""请比较下面两段文本，并分析它们为什么会出现这些差异。

【史料窗口 - {source_title}】
{window_text[:1200]}

【输入文段】
{target_text[:1200]}

【程序提取的差异证据】
### 窗口文本中有、输入文段中弱化或未出现的内容
{_format_evidence_block([...])}
...
"""
    return system_prompt, user_prompt
```

作用：不是把全文直接扔给模型，而是把“史料窗口 + 输入文本 + 程序抽出的证据”一起组织成结构化提示词。

源码位置：
[zztj_agent.py:336](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L336)

---

## 16. 本地 LLM 调用

文件：`zztj_agent.py`

```python
def call_llm(prompt: str, timeout: int = 120, system_prompt: str | None = None) -> str:
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
    conn.request(
        "POST",
        "/api/generate",
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
```

作用：通过 Ollama 的本地 HTTP API 调用本地模型，不依赖云端。

源码位置：
[zztj_agent.py:1184](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L1184)

---

## 17. 总分析入口

文件：`zztj_agent.py`

```python
def analyze_zztj_text(input_text: str) -> str:
    retrieval_text, stripped_commentary = _prepare_retrieval_text(input_text)

    if _collection_count() == 0:
        build_index()

    query_features = _extract_query_features(retrieval_text)
    results = retrieve(retrieval_text, top_k=TOP_K)

    for i, res in enumerate(results, 1):
        window_text, _, _ = _extract_best_window(res["source_text"], retrieval_text, query_features)
        llm_system_prompt, llm_prompt = _build_analysis_prompt(
            res["title"], window_text, retrieval_text
        )
        analysis = call_llm(llm_prompt, system_prompt=llm_system_prompt)
```

作用：把“按语剥离 -> 建索引 -> 检索 -> 最佳窗口 -> Prompt -> LLM -> Markdown 报告”整个串起来。

源码位置：
[zztj_agent.py:1223](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L1223)

---

## 18. Gradio Web UI

文件：`app.py`

```python
def _localhost_safe_httpx_request(method, url, *args, **kwargs):
    if _should_bypass_proxy(url):
        kwargs.setdefault("trust_env", False)
    return _ORIGINAL_HTTPX_REQUEST(method, url, *args, **kwargs)

def main():
    with gr.Blocks(css=CSS, title="资治通鉴周纪智能比对Agent") as demo:
        input_box = gr.Textbox(...)
        submit_btn = gr.Button("🔍 开始分析", variant="primary", size="lg")
        output_md = gr.Markdown(...)

        submit_btn.click(fn=analyze_zztj_text, inputs=input_box, outputs=output_md)
        input_box.submit(fn=analyze_zztj_text, inputs=input_box, outputs=output_md)

    demo.launch(server_name="127.0.0.1", server_port=7860, share=False, inbrowser=False)
```

作用：把底层算法封装成浏览器可用的界面，同时绕过本地代理对 `localhost` 请求的干扰。

源码位置：
[app.py:25](/Users/tv/Desktop/zztj-agent/app.py#L25)
[app.py:60](/Users/tv/Desktop/zztj-agent/app.py#L60)

---

## 19. 关键测试

文件：`tests/test_retrieval_helpers.py`

```python
def test_prepare_retrieval_text_strips_commentary():
    raw = "威烈王二十三年，初命晉大夫魏斯、趙籍、韓虔為諸侯。臣光曰：臣聞天子之職莫大於禮。"
    retrieval_text, commentary = _prepare_retrieval_text(raw)
    assert retrieval_text == "威烈王二十三年，初命晉大夫魏斯、趙籍、韓虔為諸侯"

def test_score_text_window_handles_aliases_and_rephrased_classical_wording():
    query = "知伯帥韓、魏而攻趙，決晉水以灌晉陽，城不浸者三版。"
    doc = "當晉六卿之時，知氏最彊...決晉水以灌晉陽之城，不湛者三版。"
    score, matched_terms, penalty = _score_text_window(doc, query_features)
    assert score > 0.55
    assert penalty == 0.0
```

作用：把“按语剥离正确”“别名识别有效”“语义改写仍能命中”这些关键设计固定成自动化测试。

源码位置：
[test_retrieval_helpers.py:20](/Users/tv/Desktop/zztj-agent/tests/test_retrieval_helpers.py#L20)
[test_retrieval_helpers.py:116](/Users/tv/Desktop/zztj-agent/tests/test_retrieval_helpers.py#L116)
