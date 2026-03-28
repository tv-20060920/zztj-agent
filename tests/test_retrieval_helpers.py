from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from download_classics import clean_extracted_text, extract_volume_id
from zztj_agent import (
    _candidate_sentence_starts,
    _file_preference_key,
    _collection_count,
    _extract_best_snippet,
    _extract_query_features,
    _limit_rerank_candidates,
    _prepare_retrieval_text,
    retrieve,
    _select_keyword_candidates,
    _score_keyword_candidate,
    _score_text_window,
    _select_best_candidates_per_file,
)
from chromadb.errors import NotFoundError


def test_prepare_retrieval_text_strips_commentary():
    raw = "威烈王二十三年，初命晉大夫魏斯、趙籍、韓虔為諸侯。臣光曰：臣聞天子之職莫大於禮。"
    retrieval_text, commentary = _prepare_retrieval_text(raw)

    assert retrieval_text == "威烈王二十三年，初命晉大夫魏斯、趙籍、韓虔為諸侯"
    assert commentary.startswith("臣光曰")


def test_extract_query_features_supports_traditional_entities():
    features = _extract_query_features("顯王三十三年，秦興師臨周而求九鼎，周君患之，以告顏率。")

    assert "顯王" in features["rulers"]
    assert "三十三年" in features["years"]
    assert "顏率" in features["names"]
    assert "九鼎" in features["events"]


def test_keyword_scoring_prefers_exact_entities_over_generic_terms():
    query_features = _extract_query_features("顯王三十三年，秦興師臨周而求九鼎，周君患之，以告顏率。")
    exact_score, exact_terms = _score_keyword_candidate(
        "秦興師臨周而求九鼎，周君患之，以告顏率。顏率曰：「大王勿憂。」",
        "戰國策_東周",
        query_features,
    )
    generic_score, generic_terms = _score_keyword_candidate(
        "魏文侯以兵誅晉亂，周威烈王賜趙、韓、魏皆命為諸侯。",
        "史記_卷039",
        query_features,
    )

    assert exact_score > generic_score
    assert "顏率" in exact_terms
    assert "九鼎" in exact_terms
    assert "顏率" not in generic_terms


def test_keyword_scoring_handles_simplified_query_against_traditional_text():
    query_features = _extract_query_features("显王三十三年，秦兴师临周而求九鼎，周君患之，以告颜率。")
    score, matched_terms = _score_keyword_candidate(
        "秦興師臨周而求九鼎，周君患之，以告顏率。顏率曰：「大王勿憂。」",
        "戰國策_東周",
        query_features,
    )

    assert score > 0.45
    assert "颜率" in matched_terms or "顏率" in matched_terms
    assert "九鼎" in matched_terms


def test_candidate_sentence_starts_focus_on_hit_sentences():
    sentences = [
        "周赧王時，秦與韓魏相攻，諸侯皆懼。",
        "又使使者往來於周。",
        "秦興師臨周而求九鼎，周君患之，以告顏率。",
        "顏率曰：「大王勿憂，臣請東借救於齊。」",
        "齊王大悅，發師五萬人，使陳臣思將以救周。",
    ]

    starts = _candidate_sentence_starts(sentences, ["九鼎", "顏率"])

    assert starts
    assert 1 in starts
    assert 2 in starts
    assert 0 not in starts


def test_extract_best_snippet_returns_compact_relevant_window():
    query = "顯王三十三年，秦興師臨周而求九鼎，周君患之，以告顏率。"
    query_features = _extract_query_features(query)
    doc = (
        "周赧王時，秦與韓魏相攻，諸侯皆懼。"
        "又使使者往來於周。"
        "秦興師臨周而求九鼎，周君患之，以告顏率。"
        "顏率曰：「大王勿憂，臣請東借救於齊。」"
        "齊王大悅，發師五萬人，使陳臣思將以救周。"
        "其後諸侯復相與謀秦。"
    )

    snippet, matched_terms, penalty = _extract_best_snippet(doc, query, query_features)

    assert len(snippet) < len(doc)
    assert "顏率" in snippet
    assert "九鼎" in snippet
    assert penalty >= 0.0
    assert "顏率" in matched_terms


def test_extract_best_snippet_handles_simplified_query_against_traditional_doc():
    query = "显王三十三年，秦兴师临周而求九鼎，周君患之，以告颜率。"
    query_features = _extract_query_features(query)
    doc = (
        "周赧王時，秦與韓魏相攻，諸侯皆懼。"
        "又使使者往來於周。"
        "秦興師臨周而求九鼎，周君患之，以告顏率。"
        "顏率曰：「大王勿憂，臣請東借救於齊。」"
        "齊王大悅，發師五萬人，使陳臣思將以救周。"
    )

    snippet, matched_terms, penalty = _extract_best_snippet(doc, query, query_features)

    assert "顏率" in snippet
    assert "九鼎" in snippet
    assert matched_terms
    assert penalty >= 0.0


def test_extract_query_features_recognizes_alias_person_and_skips_place_name():
    features = _extract_query_features("知伯帥韓、魏而攻趙，決晉水以灌晉陽，城不浸者三版。")

    assert "知伯" in features["names"]
    assert "晉陽" not in features["names"]


def test_extract_query_features_handles_qin_shihuang_fengshan_case():
    features = _extract_query_features(
        "二十八年始皇东行郡、县，上邹峄山，立石颂功业。于是召集鲁儒生七十人，至泰山下，议封禅。"
    )

    assert "始皇" in features["rulers"] or "秦始皇" in features["rulers"]
    assert "封禅" in features["events"] or "封禪" in features["events"]
    assert "鲁儒生" not in features["names"]
    assert "集鲁儒" not in features["names"]


def test_score_text_window_handles_aliases_and_rephrased_classical_wording():
    query = "知伯帥韓、魏而攻趙，決晉水以灌晉陽，城不浸者三版。"
    query_features = _extract_query_features(query)
    doc = "當晉六卿之時，知氏最彊，滅范、中行，又率韓、魏之兵以圍趙襄子於晉陽，決晉水以灌晉陽之城，不湛者三版。"

    score, matched_terms, penalty = _score_text_window(doc, query_features)

    assert score > 0.55
    assert "知伯" in matched_terms
    assert "決晉水以灌晉陽" in matched_terms
    assert penalty == 0.0


def test_select_keyword_candidates_prefers_anchor_matches():
    query_features = _extract_query_features("顯王三十三年，秦興師臨周而求九鼎，周君患之，以告顏率。")
    corpus_entries = [
        {
            "id": "exact",
            "text": "秦興師臨周而求九鼎，周君患之，以告顏率。",
            "title": "戰國策_東周",
            "file": "戰國策_東周.txt",
            "sentences": [],
        },
        {
            "id": "generic",
            "text": "周王命諸侯守鼎，亦有求鼎之議。",
            "title": "雜記",
            "file": "雜記.txt",
            "sentences": [],
        },
        {
            "id": "name_only",
            "text": "顏率曰：「大王勿憂。」",
            "title": "戰國策_東周",
            "file": "戰國策_東周_二.txt",
            "sentences": [],
        },
    ]

    selected = _select_keyword_candidates(corpus_entries, query_features, limit=5)

    assert selected[0]["id"] == "exact"
    assert all(item["anchor_hits"] > 0 for item in selected)


def test_limit_rerank_candidates_keeps_strongest_seed_scores():
    limited = _limit_rerank_candidates(
        [
            {"id": "weak", "anchor_hits": 0, "keyword_score": 0.3, "semantic_score": 0.8},
            {"id": "anchor", "anchor_hits": 2, "keyword_score": 0.2, "semantic_score": 0.1},
            {"id": "balanced", "anchor_hits": 1, "keyword_score": 0.5, "semantic_score": 0.4},
        ],
        limit=2,
    )

    assert [item["id"] for item in limited] == ["anchor", "balanced"]


def test_retrieve_normalizes_whitespace_before_embedding():
    import zztj_agent as agent

    class FakeEmbedder:
        def __init__(self):
            self.queries = []

        def encode(self, items, convert_to_numpy=True):
            import numpy as np

            self.queries.extend(items)
            return np.array([[0.0, 0.0, 0.0]])

    fake_embedder = FakeEmbedder()
    original_get_embedder = agent.get_embedder
    original_get_collection = agent.get_chroma_collection
    original_get_corpus_entries = agent._get_corpus_entries
    original_call_collection = agent._call_collection

    try:
        agent.get_embedder = lambda: fake_embedder
        agent.get_chroma_collection = lambda: object()
        agent._get_corpus_entries = lambda: []
        agent._call_collection = lambda method_name, *args, **kwargs: {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
            "ids": [[]],
        }

        results = retrieve("二十八年\n始皇东行郡、县，  上邹峄山。", top_k=1)

        assert results == []
        assert fake_embedder.queries == ["二十八年始皇东行郡、县，上邹峄山。"]
    finally:
        agent.get_embedder = original_get_embedder
        agent.get_chroma_collection = original_get_collection
        agent._get_corpus_entries = original_get_corpus_entries
        agent._call_collection = original_call_collection


def test_select_best_candidates_per_file_prefers_rule_stronger_chunk():
    candidates = [
        {
            "file": "史記_卷044.txt",
            "score": 0.51,
            "window_score": 0.30,
            "keyword_score": 0.49,
            "semantic_score": 0.88,
            "penalty": 0.15,
            "anchor_hits": 1,
        },
        {
            "file": "史記_卷044.txt",
            "score": 0.46,
            "window_score": 0.64,
            "keyword_score": 0.62,
            "semantic_score": 0.00,
            "penalty": 0.0,
            "anchor_hits": 1,
        },
        {
            "file": "史記_卷043.txt",
            "score": 0.40,
            "window_score": 0.35,
            "keyword_score": 0.40,
            "semantic_score": 0.20,
            "penalty": 0.0,
            "anchor_hits": 0,
        },
    ]

    chosen = _select_best_candidates_per_file(candidates)
    chosen_map = {item["file"]: item for item in chosen}

    assert len(chosen) == 2
    assert _file_preference_key(chosen_map["史記_卷044.txt"]) > _file_preference_key(candidates[0])
    assert chosen_map["史記_卷044.txt"]["window_score"] == 0.64


def test_clean_extracted_text_removes_wikisource_noise():
    raw = """
    ← 回主目錄 戰國策卷一 東周 作者： 劉向 西漢 →
    姊妹计划 : 数据项
    秦興師臨周而求九鼎。[ 编辑 ]
    顏率曰：「大王勿憂。」
    """
    cleaned = clean_extracted_text(raw)

    assert "姊妹计划" not in cleaned
    assert "编辑" not in cleaned
    assert "秦興師臨周而求九鼎" in cleaned
    assert "顏率曰" in cleaned


def test_extract_volume_id():
    assert extract_volume_id("史記_卷039_宋微子世家") == "039"
    assert extract_volume_id("史記_卷126") == "126"


def test_collection_count_recovers_after_not_found(monkeypatch):
    class DeadCollection:
        def count(self):
            raise NotFoundError("missing")

    class LiveCollection:
        def count(self):
            return 7

    collections = [DeadCollection(), LiveCollection()]

    def fake_get_collection():
        return collections.pop(0)

    monkeypatch.setattr("zztj_agent.get_chroma_collection", fake_get_collection)
    monkeypatch.setattr("zztj_agent._invalidate_collection", lambda: None)

    assert _collection_count() == 7
