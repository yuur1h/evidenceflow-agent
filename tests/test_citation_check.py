import pytest

from deep_research.citation_check import (
    annotate_evidence, citation_stats, llm_entailment, normalize_url, observed_sources, support_score,
)
from deep_research.evidence_quality import usable_evidence
from deep_research.schemas import ResearchState
from deep_research.worker import DeepResearchWorker


SEARCH = {"tool": "search", "query": "capital of france", "results": [
    {"title": "Paris - Wikipedia", "url": "https://en.wikipedia.org/wiki/Paris",
     "snippet": "Paris is the capital and most populous city of France."}]}
PAGE = {"tool": "enrich_url", "url": "https://www.example.com/report/",
        "result": "Annual report 2025. Revenue grew 12 percent to 3.4 billion euros.", "results": []}
WIKI = {"tool": "search_wikipedia", "query": "Lyon",
        "result": "Lyon\n\nLyon is a city in France.\n\nURL: https://en.wikipedia.org/wiki/Lyon", "results": []}


def item(url, claim="Paris is the capital of France.", **kw):
    return {"claim": claim, "url": url, "source": "x", "confidence": "high", "evidence_type": "primary", **kw}


@pytest.mark.parametrize('a,b', [
    ("https://www.example.com/report/", "https://example.com/report"),
    ("http://Example.com/a?x=1#top", "https://example.com/a?x=1"),
])
def test_url_normalization_ignores_scheme_www_slash_fragment(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_observed_sources_cover_search_page_and_wiki_body_urls():
    observed = observed_sources([SEARCH, PAGE, WIKI])
    assert normalize_url("https://en.wikipedia.org/wiki/Paris") in observed
    assert normalize_url("https://example.com/report") in observed
    assert normalize_url("https://en.wikipedia.org/wiki/Lyon") in observed


def test_fabricated_url_is_downgraded_and_excluded_from_gates():
    [fake] = annotate_evidence([item("https://en.wikipedia.org/wiki/France")], [SEARCH])
    assert fake["provenance"] == "unverified"
    assert fake["evidence_type"] == "inference" and fake["confidence"] == "low"
    assert not usable_evidence(fake)


def test_tool_backed_claim_is_supported_and_usable():
    [ok] = annotate_evidence([item("https://en.wikipedia.org/wiki/Paris")], [SEARCH])
    assert ok["provenance"] == "tool" and ok["support"] == "supported"
    assert ok["evidence_type"] == "primary"
    assert usable_evidence(ok)


def test_tool_backed_but_unrelated_claim_is_unsupported():
    [bad] = annotate_evidence([item("https://en.wikipedia.org/wiki/Paris", claim="Berlin hosted the 1936 Olympics.")], [SEARCH])
    assert bad["support"] == "unsupported"
    assert not usable_evidence(bad)


def test_no_tool_output_means_every_citation_is_unverified():
    [fake] = annotate_evidence([item("https://en.wikipedia.org/wiki/Paris")], [])
    assert fake["provenance"] == "unverified"


def test_cjk_overlap_uses_character_bigrams():
    assert support_score("巴黎是法国首都", ["巴黎是法国的首都和最大城市"]) >= 0.5
    assert support_score("柏林是德国首都", ["巴黎是法国的首都"]) < 0.5


def test_unannotated_evidence_still_passes_structural_gate():
    assert usable_evidence(item("https://example.com/x"))


def test_stats_count_each_bucket():
    evidence = annotate_evidence([item("https://en.wikipedia.org/wiki/Paris"), item("https://nowhere.test/x")], [SEARCH])
    stats = citation_stats(evidence + [item("https://example.com/unchecked")])
    assert stats == {"total": 3, "tool": 1, "unverified": 1, "supported": 1, "weak": 0, "unsupported": 1, "unchecked": 1}


class VerdictLLM:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        assert "来源片段" in messages[1]["content"]
        return '{"verdicts": [{"index": 0, "verdict": "%s", "reason": "r"}]}' % self.verdict


@pytest.mark.parametrize('verdict,expected', [("supported", "supported"), ("unsupported", "unsupported"), ("unclear", "weak")])
@pytest.mark.asyncio
async def test_llm_rejudges_only_weak_items(verdict, expected):
    weak = item("https://example.com/report", claim="Revenue rose 12 percent year on year, driven by exports.")
    strong = item("https://en.wikipedia.org/wiki/Paris")
    annotate_evidence([weak, strong], [SEARCH, PAGE])
    assert weak["support"] == "weak"
    llm = VerdictLLM(verdict)
    await llm_entailment(llm, [weak, strong], [SEARCH, PAGE])
    assert llm.calls == 1
    assert weak["support"] == expected
    assert strong["support"] == "supported" and "support_judge" not in strong


@pytest.mark.asyncio
async def test_llm_failure_keeps_rule_based_label():
    class Broken:
        async def complete(self, *a, **k):
            raise RuntimeError("offline")
    weak = item("https://example.com/report", claim="Revenue rose 12 percent year on year, driven by exports.")
    annotate_evidence([weak], [PAGE])
    await llm_entailment(Broken(), [weak], [PAGE])
    assert weak["support"] == "weak"


@pytest.mark.asyncio
async def test_worker_output_is_annotated_end_to_end(monkeypatch):
    monkeypatch.setenv("CITATION_LLM_CHECK", "0")

    class Tools:
        async def search(self, query, **kwargs):
            return SEARCH

    class LLM:
        async def complete(self, messages, **kwargs):
            return ('{"status":"resolved","summary":"s","candidate_answer":"Paris","evidence":['
                    '{"claim":"Paris is the capital of France.","url":"https://en.wikipedia.org/wiki/Paris","confidence":"high","evidence_type":"primary"},'
                    '{"claim":"Paris is the capital of France.","url":"https://made.up/source","confidence":"high","evidence_type":"primary"}]}')

    class Identity:
        async def compress(self, texts, **kwargs):
            return texts

    worker = DeepResearchWorker(LLM(), tool_executor=Tools(), compressor=Identity())
    result = await worker.run(query="capital of france", state=ResearchState("s", "search", "x"))
    assert [e["provenance"] for e in result.evidence] == ["tool", "unverified"]
    assert [usable_evidence(e) for e in result.evidence] == [True, False]
