"""Citation provenance and support checks.

The worker asks the model for evidence items with URLs, but nothing stops the model
from inventing a well-formed link. These checks tie every citation back to what the
tools actually returned in this state:

- provenance: ``tool`` when the URL appeared in a tool result, ``prior_state`` when it
  was verified by an earlier state's tools, ``unverified`` otherwise.
- support: lexical overlap between the claim and the text observed at that URL
  (``supported`` / ``weak`` / ``unsupported``). An optional LLM pass re-judges items the
  lexical check did not clear, which is what catches paraphrase and translation.

Unverified items are kept for transparency but downgraded so coverage gates ignore them.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from deep_research.llm import extract_json_object

PROVENANCE_TOOL = "tool"
PROVENANCE_PRIOR = "prior_state"
PROVENANCE_UNVERIFIED = "unverified"
VERIFIED_PROVENANCE = {PROVENANCE_TOOL, PROVENANCE_PRIOR}
SUPPORTED = "supported"
WEAK = "weak"
UNSUPPORTED = "unsupported"

SUPPORT_THRESHOLD = 0.5
WEAK_THRESHOLD = 0.2
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
_LATIN_RE = re.compile(r"[a-z0-9]+(?:[.\-'][a-z0-9]+)*")
_CJK_RE = re.compile(r"[一-鿿]+")
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "are", "was", "were",
    "by", "with", "as", "from", "that", "this", "it", "its", "be", "has", "have", "had",
    "的", "了", "在", "是", "和", "与", "及", "或", "等", "为", "于", "有", "被", "将", "对", "中",
}


def normalize_url(url: object) -> str:
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def observed_sources(
    tool_calls: list[dict[str, Any]],
    prior_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    """Map every URL the tools returned to the text fragments seen at that URL.

    Evidence already verified in earlier states counts as observed too, so a worker may
    re-cite it; its fragments are the verified claims, not the page text."""
    observed: dict[str, list[str]] = {}

    def add(url: object, *texts: object) -> None:
        key = normalize_url(url)
        if not key:
            return
        fragments = observed.setdefault(key, [])
        for text in texts:
            text = str(text or "").strip()
            if text and text not in fragments:
                fragments.append(text)

    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        raw = str(call.get("result") or "")
        page_url = call.get("url") or call.get("arguments", {}).get("url")
        if page_url:
            add(page_url, raw)
        for item in call.get("results") or []:
            if not isinstance(item, dict):
                continue
            add(item.get("url") or page_url, item.get("title"), item.get("snippet"))
        # Wikipedia and scraped pages report their canonical URL inside the text body.
        for found in _URL_RE.findall(raw):
            add(found, raw)
    for item in prior_evidence or []:
        if (isinstance(item, dict) and item.get("provenance") in VERIFIED_PROVENANCE
                and item.get("support") != UNSUPPORTED):
            add(item.get("url"), item.get("claim"))
    return observed


def _latin_tokens(text: str) -> set[str]:
    return {w for w in _LATIN_RE.findall(text) if len(w) > 1 and w not in _STOPWORDS}


def _cjk_tokens(text: str) -> set[str]:
    words: set[str] = set()
    for run in _CJK_RE.findall(text):
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def _tokens(text: str) -> set[str]:
    text = str(text or "").lower()
    return _latin_tokens(text) | _cjk_tokens(text)


def support_score(claim: str, fragments: list[str]) -> float:
    """Overlap ratio of claim tokens found in the source text.

    A claim written in Chinese about an English page shares no CJK bigrams with it, so a
    mixed-script claim is also scored on its Latin tokens alone (names, numbers, places)
    when it has at least three of them; the better ratio wins. That still cannot prove
    entailment across languages, which is why weak and unsupported tool-backed items go
    to the model for a second opinion."""
    claim = str(claim or "").lower()
    all_tokens = _tokens(claim)
    if not all_tokens:
        return 0.0
    source_tokens: set[str] = set()
    for fragment in fragments:
        source_tokens |= _tokens(fragment)
    score = len(all_tokens & source_tokens) / len(all_tokens)
    latin = _latin_tokens(claim)
    if len(latin) >= 3 and len(latin) < len(all_tokens):
        score = max(score, len(latin & source_tokens) / len(latin))
    return score


def text_overlap(text: str, other: str) -> float:
    """Share of `text` tokens that also occur in `other`; used to match questions to planned states."""
    tokens = _tokens(text)
    return len(tokens & _tokens(other)) / len(tokens) if tokens else 0.0


def support_label(score: float) -> str:
    if score >= SUPPORT_THRESHOLD:
        return SUPPORTED
    if score >= WEAK_THRESHOLD:
        return WEAK
    return UNSUPPORTED


def annotate_evidence(
    evidence: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    prior_evidence: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach provenance/support fields in place and downgrade citations the tools never returned."""
    tool_only = observed_sources(tool_calls)
    observed = observed_sources(tool_calls, prior_evidence)
    for item in evidence:
        if not isinstance(item, dict):
            continue
        key = normalize_url(item.get("url"))
        fragments = observed.get(key)
        if not key or fragments is None:
            item["provenance"] = PROVENANCE_UNVERIFIED
            item["support"] = UNSUPPORTED
            item["support_score"] = 0.0
            item["evidence_type"] = "inference"
            item["confidence"] = "low"
            continue
        score = support_score(str(item.get("claim") or ""), fragments)
        item["provenance"] = PROVENANCE_TOOL if key in tool_only else PROVENANCE_PRIOR
        item["support"] = support_label(score)
        item["support_score"] = round(score, 3)
    return evidence


def is_verified(item: object) -> bool:
    """True unless a check explicitly rejected the item; unannotated items are left to usable_evidence."""
    if not isinstance(item, dict):
        return False
    return item.get("provenance") != PROVENANCE_UNVERIFIED and item.get("support") != UNSUPPORTED


def citation_stats(evidence: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"total": 0, "tool": 0, "unverified": 0, "supported": 0, "weak": 0, "unsupported": 0, "unchecked": 0}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        stats["total"] += 1
        provenance = item.get("provenance")
        if provenance in VERIFIED_PROVENANCE:
            stats["tool"] += 1
        elif provenance == PROVENANCE_UNVERIFIED:
            stats["unverified"] += 1
        else:
            stats["unchecked"] += 1
        support = item.get("support")
        if support in {SUPPORTED, WEAK, UNSUPPORTED}:
            stats[support] += 1
    return stats


ENTAILMENT_SYSTEM = (
    "你是引用核验员。给定来源原文片段和一条引用陈述，判断原文是否支持该陈述。"
    "陈述和原文可能是不同语言，按语义判断。只依据片段内容，不使用外部知识；片段不足以判断时回答 unclear。"
    '输出严格 JSON：{"verdicts":[{"index":0,"verdict":"supported|unsupported|unclear","reason":"..."}]}'
)


async def llm_entailment(
    llm: Any,
    evidence: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    prior_evidence: list[dict[str, Any]] | None = None,
    *,
    max_items: int = 8,
    max_chars: int = 1500,
) -> list[dict[str, Any]]:
    """Re-judge tool-backed citations the lexical check did not clear. Failures leave items unchanged.

    Lexical overlap misses paraphrase and translation, so both weak and unsupported items are
    sent; unverified (fabricated URL) items are never rescued this way. An "unclear" verdict
    on an unsupported item lifts it to weak: the model could not confirm it, but the lexical
    rejection is no longer trusted either."""
    observed = observed_sources(tool_calls, prior_evidence)
    pending = [item for item in evidence if isinstance(item, dict)
               and item.get("provenance") in VERIFIED_PROVENANCE
               and item.get("support") in {WEAK, UNSUPPORTED}][:max_items]
    if not pending or llm is None:
        return evidence
    blocks = []
    for idx, item in enumerate(pending):
        fragments = observed.get(normalize_url(item.get("url")), [])
        source_text = "\n".join(fragments)[:max_chars]
        blocks.append(f"[{idx}] 陈述：{item.get('claim')}\n来源片段：{source_text}")
    try:
        raw = await llm.complete(
            [{"role": "system", "content": ENTAILMENT_SYSTEM},
             {"role": "user", "content": "\n\n".join(blocks)}],
            temperature=0.0,
        )
        data = extract_json_object(raw) or {}
        for verdict in data.get("verdicts", []) or []:
            if not isinstance(verdict, dict):
                continue
            try:
                item = pending[int(verdict.get("index"))]
            except (TypeError, ValueError, IndexError):
                continue
            label = str(verdict.get("verdict") or "").lower()
            if label in {SUPPORTED, UNSUPPORTED}:
                item["support"] = label
            elif item["support"] == UNSUPPORTED:
                item["support"] = WEAK
            else:
                continue
            item["support_judge"] = "llm"
            item["support_reason"] = str(verdict.get("reason") or "")[:300]
    except Exception:
        pass
    return evidence
