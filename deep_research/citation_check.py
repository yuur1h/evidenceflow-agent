"""Citation provenance and support checks.

The worker asks the model for evidence items with URLs, but nothing stops the model
from inventing a well-formed link. These checks tie every citation back to what the
tools actually returned in this state:

- provenance: ``tool`` when the URL appeared in a tool result, ``unverified`` otherwise.
- support: lexical overlap between the claim and the text observed at that URL
  (``supported`` / ``weak`` / ``unsupported``). An optional LLM pass upgrades or
  downgrades ``weak`` items.

Unverified items are kept for transparency but downgraded so coverage gates ignore them.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from deep_research.llm import extract_json_object

PROVENANCE_TOOL = "tool"
PROVENANCE_UNVERIFIED = "unverified"
SUPPORTED = "supported"
WEAK = "weak"
UNSUPPORTED = "unsupported"

SUPPORT_THRESHOLD = 0.5
WEAK_THRESHOLD = 0.2
_URL_RE = re.compile(r"https?://[^\s)>\]\"']+")
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


def observed_sources(tool_calls: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Map every URL the tools returned to the text fragments seen at that URL."""
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
    return observed


def _tokens(text: str) -> set[str]:
    text = str(text or "").lower()
    words = {w for w in re.findall(r"[a-z0-9]+(?:[.\-'][a-z0-9]+)*", text) if len(w) > 1 and w not in _STOPWORDS}
    cjk = re.sub(r"[^一-鿿]", " ", text)
    for run in cjk.split():
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def support_score(claim: str, fragments: list[str]) -> float:
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return 0.0
    source_tokens = set()
    for fragment in fragments:
        source_tokens |= _tokens(fragment)
    return len(claim_tokens & source_tokens) / len(claim_tokens)


def support_label(score: float) -> str:
    if score >= SUPPORT_THRESHOLD:
        return SUPPORTED
    if score >= WEAK_THRESHOLD:
        return WEAK
    return UNSUPPORTED


def annotate_evidence(evidence: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach provenance/support fields in place and downgrade citations the tools never returned."""
    observed = observed_sources(tool_calls)
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
        item["provenance"] = PROVENANCE_TOOL
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
        if provenance == PROVENANCE_TOOL:
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
    "只依据片段内容，不使用外部知识；片段不足以判断时回答 unclear。"
    '输出严格 JSON：{"verdicts":[{"index":0,"verdict":"supported|unsupported|unclear","reason":"..."}]}'
)


async def llm_entailment(
    llm: Any,
    evidence: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    *,
    max_items: int = 8,
    max_chars: int = 1500,
) -> list[dict[str, Any]]:
    """Re-judge lexically weak but tool-backed citations with the model. Failures leave items unchanged."""
    observed = observed_sources(tool_calls)
    pending = [item for item in evidence if isinstance(item, dict)
               and item.get("provenance") == PROVENANCE_TOOL and item.get("support") == WEAK][:max_items]
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
                item["support_judge"] = "llm"
                item["support_reason"] = str(verdict.get("reason") or "")[:300]
    except Exception:
        pass
    return evidence
