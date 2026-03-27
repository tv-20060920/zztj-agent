# 项目关键技术代码片段：新生友好版

这份版本专门给刚学编程和 AI 的同学看。

写法原则只有两个：
- 每段只保留最值得讲的核心代码。
- 每段后面都用白话解释“它到底在干嘛”。

---

## 1. 抓网页源码

文件名：`download_classics.py`

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
```

白话解释：

这段代码的任务很简单：去网上把某个古籍页面的 HTML 下载下来。

你可以把它理解成“让程序自己打开网页，并把网页源代码抄回来”。

这里为什么不用浏览器，而用 `curl`？
- 因为 `curl` 更稳定。
- 对批量下载更方便。
- 遇到一些网络兼容问题时，比直接写 Python 网络请求更省事。

这里拿到的还不是正文，只是一大坨网页源码。真正的正文提取，要靠下一段。

源码位置：
[download_classics.py:20](/Users/tv/Desktop/zztj-agent/download_classics.py#L20)

---

## 2. 从 HTML 里挖出正文

文件名：`download_classics.py`

```python
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

白话解释：

网页源码里不只有正文，还有很多乱七八糟的东西，比如：
- JavaScript
- CSS
- 表格
- 上标注释
- 页面边栏

这段代码做的事就是：
1. 先删掉明显没用的部分。
2. 再去网页里找“最像正文的区域”。
3. 最后把 HTML 标签全剥掉，只留下文字。

可以把它理解成：

“先把网页壳子拆掉，再把里面真正的古文抠出来。”

源码位置：
[download_classics.py:62](/Users/tv/Desktop/zztj-agent/download_classics.py#L62)

---

## 3. 清理网页噪音

文件名：`download_classics.py`

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

白话解释：

网页正文就算提取出来了，也还是不干净。

比如它可能混着：
- “回主目录”
- “姊妹计划”
- “编辑”
- 作者说明
- 奇怪空格

这些东西对人来说还能忍，对机器来说很危险。因为机器会把这些垃圾也当成正文去学。

所以这段代码的任务就是：

“把不属于古文正文的东西尽量删掉。”

为什么这一步非常重要？

因为后面要做向量检索。如果前面的文本很脏，后面的检索就会越来越歪。

源码位置：
[download_classics.py:35](/Users/tv/Desktop/zztj-agent/download_classics.py#L35)

---

## 4. 繁简和异写归一化

文件名：`zztj_agent.py`

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

白话解释：

古籍文本里，一个人名、一个词，经常有多种写法。

比如：
- `知伯` 和 `智伯`
- `趙毋恤` 和 `趙襄子`
- `帥` 和 `率`
- `湛` 和 `浸`

如果程序把这些都当成完全不同的词，就会漏检。

所以这段代码做的事是：

“把长得不一样、但意思差不多的写法，尽量归到一起。”

这一步很像你整理同学名单时，把“张三”“張三”“三哥”先认成同一个人。

源码位置：
[zztj_agent.py:186](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L186)

---

## 5. 从文本里抓历史关键信息

文件名：`zztj_agent.py`

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
```

白话解释：

这一段是在做“历史信息抽取”。

程序想从一句古文里抓出几类最重要的东西：
- 谁在位
- 哪一年
- 谁出场
- 哪个国家

为什么要抓这些？

因为历史文本检索不能只看“句子像不像”，还要看“是不是同一年、同一个人、同一件事”。

例如，两段文字都讲“出兵”，但如果年份和人物不对，那就不是同一条史料。

所以这一步是在给程序装上“历史学方向感”。

源码位置：
[zztj_agent.py:443](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L443)

---

## 6. 把输入句子变成“检索锚点”

文件名：`zztj_agent.py`

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

白话解释：

程序收到用户输入后，不会直接傻乎乎拿整句话去搜。

它会先问自己：
- 这句话里最重要的词有哪些？
- 哪些词最能代表这件事？

于是它把输入压缩成一组“锚点”，比如：
- `顯王`
- `三十三年`
- `顏率`
- `九鼎`

这就像你查资料时，不会把整段作文都塞进搜索框，而是先提炼关键词。

源码位置：
[zztj_agent.py:515](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L515)

---

## 7. 先把“臣光曰”这种按语切掉

文件名：`zztj_agent.py`

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

白话解释：

《资治通鉴》里有时会混入司马光自己的评论，比如：
- `臣光曰`
- `臣光按`

这些内容不一定属于原始史料本身。

如果你拿着“正文 + 评论”一起去检索，程序很容易被评论带偏。

所以这段代码的逻辑是：

“如果看见编者按，就先把它切掉，只拿正文去找史料。”

源码位置：
[zztj_agent.py:735](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L735)

---

## 8. 为什么要分块

文件名：`zztj_agent.py`

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
```

白话解释：

整本《史记》不能直接塞进向量库里，不然每一条都太长了。

所以要先切成很多小块。

这里的切法不是乱切，而是尽量按古文结构切：
- 先按段落
- 再按句子
- 太长的句子再切
- 邻近块之间还保留一点重叠

为什么要重叠？

因为一个事件可能刚好卡在块和块的边界上。没有重叠，就可能被切断，结果两边都不像完整证据。

源码位置：
[zztj_agent.py:861](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L861)

---

## 9. 向量模型懒加载

文件名：`zztj_agent.py`

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

白话解释：

向量模型比较占内存，所以程序没有一启动就加载它。

而是等到真正要做向量化时，才加载。

这叫“懒加载”。

你可以把它理解成：

“平时先不开大机器，等真要干活时再开。”

这对普通电脑非常重要，否则程序可能一开始就很卡。

源码位置：
[zztj_agent.py:801](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L801)

---

## 10. 本地向量数据库

文件名：`zztj_agent.py`

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
```

白话解释：

切好的文本块要存起来，还要支持“按相似度查找”，这就是向量数据库的工作。

这里项目用的是 ChromaDB。

它的好处是：
- 可以本地运行
- 不需要单独服务器
- 适合课程项目和个人电脑

你可以把它理解成：

“一个专门拿来存文本向量、并且能按相似度搜索的数据库。”

源码位置：
[zztj_agent.py:822](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L822)

---

## 11. 建立向量索引

文件名：`zztj_agent.py`

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
```

白话解释：

这段代码做的就是：

“把所有史料切块，再把每个块变成向量，最后存进数据库。”

这一步做完后，程序才算真正拥有了一个“可搜索的史料库”。

注意这里不只存文本，还存了：
- `title`
- `file`

这样后面找到结果时，程序才能告诉你“这是哪本书、哪个文件里的内容”。

源码位置：
[zztj_agent.py:923](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L923)

---

## 12. 关键词打分不是简单数次数

文件名：`zztj_agent.py`

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

白话解释：

这段代码很关键，因为它说明程序不是“看见词就一视同仁”。

它认为：
- 君主信息很重要
- 年份很重要
- 人名很重要
- 普通事件词次一级

为什么？

因为历史文本里，“是不是同一个人、同一年”比“是不是都写了战争”更重要。

所以这里用了“加权打分”。

简单说就是：

“重要词命中，加更多分；普通词命中，加少一点分。”

源码位置：
[zztj_agent.py:749](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L749)

---

## 13. 对候选窗口做更细的评分

文件名：`zztj_agent.py`

```python
def _score_text_window(text: str, query_features: dict):
    text_variants = _comparison_variants(text)
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

白话解释：

这一步是在问：

“这段候选文本到底有多像用户输入？”

它不是只看一种指标，而是混着看：
- 重要实体命中了多少
- 字串重合多不多
- 整体顺序像不像
- 有没有明显不对的人物或年份

也就是说，它不是只看“像”，还看“会不会像错了”。

这对历史任务特别重要，因为有些句子表面很像，实际上人物年份全错。

源码位置：
[zztj_agent.py:558](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L558)

---

## 14. 从长文本里截出最像的一小段

文件名：`zztj_agent.py`

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

白话解释：

一整条候选史料块可能还是太长，所以程序继续做一件事：

“在这条候选里，再找到最像输入文本的那一小段。”

这个过程像什么？

像你在一本书里找到一个大概相关的章节后，还要继续用眼睛扫，找出真正对应的那几句话。

这样做的好处是：
- 结果更紧凑
- 更适合展示
- 更适合拿去和输入文本做对比

源码位置：
[zztj_agent.py:650](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L650)

---

## 15. 混合检索主流程

文件名：`zztj_agent.py`

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
    results = _call_collection(
        'query',
        query_embeddings=[query_emb],
        n_results=max(top_k * 4, SEMANTIC_CANDIDATES),
        include=["documents", "metadatas", "distances"]
    )

    for candidate in candidate_map.values():
        snippet, snippet_terms, penalty = _extract_best_snippet(candidate["source_text"], query, query_features)
        window_score, matched_terms, _ = _score_text_window(snippet, query_features)
        final_score = (
            candidate["semantic_score"] * 0.15
            + candidate["keyword_score"] * 0.30
            + window_score * 0.55
        )
```

白话解释：

这是整个项目最核心的代码之一。

它不是只做一种搜索，而是三步一起上：

1. 先做关键词召回
2. 再做向量相似召回
3. 最后做窗口级重排

为什么这么麻烦？

因为单靠一种方法都不够稳：
- 只靠关键词，容易漏掉改写过的句子
- 只靠向量，容易找到“意思有点像但不是同一件事”的文本

所以项目选择“两条腿走路”：

“规则帮你守住历史实体，向量帮你补足语义相似。”

源码位置：
[zztj_agent.py:995](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L995)

---

## 16. 给大模型的提示词不是随便写的

文件名：`zztj_agent.py`

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
"""
    return system_prompt, user_prompt
```

白话解释：

大模型不是你随便说一句“帮我分析一下”就一定会答得好。

这段代码做的事是：

“先把问题摆清楚，再把证据喂给模型，再规定输出格式。”

也就是说，它不是让模型自由发挥，而是尽量让模型：
- 只看这两段文本
- 只根据眼前证据说话
- 按固定结构输出

这会大大减少模型胡说八道的概率。

源码位置：
[zztj_agent.py:336](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L336)

---

## 17. 调用本地大模型

文件名：`zztj_agent.py`

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

白话解释：

这段代码是在和本地 Ollama 通信。

你可以把 Ollama 理解成：

“在自己电脑上运行大模型的一个本地服务。”

程序把提示词整理好后，用 HTTP 请求发给 Ollama，然后拿回模型输出。

它的意义是：
- 不需要云 API
- 数据不出本机
- 更适合本地实验和课堂演示

源码位置：
[zztj_agent.py:1184](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L1184)

---

## 18. 整个项目的总入口

文件名：`zztj_agent.py`

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

白话解释：

这一段就是整个系统的总指挥。

它把前面所有模块串起来：
- 先清理输入
- 再建索引
- 再检索
- 再截窗口
- 再交给大模型分析

如果把整个项目比作流水线，这个函数就是总开关。

所以读懂它，你就基本读懂了整个项目。

源码位置：
[zztj_agent.py:1223](/Users/tv/Desktop/zztj-agent/zztj_agent.py#L1223)

---

## 19. Web 界面怎么接上算法

文件名：`app.py`

```python
def main():
    with gr.Blocks(css=CSS, title="资治通鉴周纪智能比对Agent") as demo:
        input_box = gr.Textbox(...)
        submit_btn = gr.Button("🔍 开始分析", variant="primary", size="lg")
        output_md = gr.Markdown(...)

        submit_btn.click(fn=analyze_zztj_text, inputs=input_box, outputs=output_md)
        input_box.submit(fn=analyze_zztj_text, inputs=input_box, outputs=output_md)

    demo.launch(server_name="127.0.0.1", server_port=7860, share=False, inbrowser=False)
```

白话解释：

前面的代码都是后端逻辑，这一段是在做界面。

它做了三件事：
- 建输入框
- 建按钮
- 把按钮点击事件绑定到 `analyze_zztj_text`

也就是说，用户点击一下“开始分析”，底层那一大套检索和 LLM 流程就跑起来了。

这就是“把算法变成产品”的第一步。

源码位置：
[app.py:60](/Users/tv/Desktop/zztj-agent/app.py#L60)

---

## 20. 关键测试

文件名：`tests/test_retrieval_helpers.py`

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

白话解释：

测试的作用不是“让代码看起来高级”，而是防止关键功能以后被改坏。

比如这里就在检查：
- “臣光曰”会不会被正确切掉
- 文字改写后，程序还能不能认出是同一件事

对新生来说，可以把测试理解成：

“给程序留下一套自动判卷题。以后改代码时，系统自己先检查有没有把老功能弄坏。”

源码位置：
[test_retrieval_helpers.py:20](/Users/tv/Desktop/zztj-agent/tests/test_retrieval_helpers.py#L20)
[test_retrieval_helpers.py:116](/Users/tv/Desktop/zztj-agent/tests/test_retrieval_helpers.py#L116)

---

## 最后一句总结

如果你只记住一句话，就记这个：

这个项目不是“直接问大模型”，而是：

“先把古籍数据洗干净，再把文本切块和向量化，再用混合检索找到最可能的史料窗口，最后才让大模型基于证据做解释。”
