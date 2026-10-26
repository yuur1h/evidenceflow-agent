from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from typing import Any, Callable

from deep_research.adversarial.blue_agent import BlueAgent
from deep_research.adversarial.loop import AdversarialLoop
from deep_research.adversarial.red_agent import RedAgent
from deep_research.compressor.compressor import ContextCompressor
from deep_research.llm import create_llm, LLMClient
from deep_research.task_modes import TaskMode
from deep_research.citation_check import citation_stats, text_overlap
from deep_research.evidence_quality import usable_evidence, in_time_scope
from deep_research.memory.long_term import MemoryEntry
from deep_research.memory.memory_store import SharedMemoryStore
from deep_research.planner import DeepResearchPlanner
from deep_research.schemas import (
    CoverageReport,
    DeepResearchResult,
    FSMState,
    ResearchPlan,
    ResearchReport,
    ResearchState,
    StateHistoryEvent,
    WorkerResult,
    confidence_to_float,
)
from deep_research.state_graph import ResearchStateGraph
from deep_research.tool_adapter import FunctionToolExecutor
from deep_research.worker import DeepResearchWorker
from deep_research.synthesizer import DeepResearchSynthesizer, extract_final_answer

EARLY_STOP_REASON = "final answer already resolved"


class DeepResearchRunner:
    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        task_mode: TaskMode | str = TaskMode.MULTI_HOP,
        tool_executor: Any | None = None,
        embedder: Any | None = None,
        memory_db_path: str | None = None,
        session_id: str | None = None,
        max_concurrent: int = 3,
        max_replans: int = 2,
        max_adversarial_rounds: int = 2,
        state_timeout_seconds: int = 120,
        event_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.task_mode = TaskMode(task_mode)
        self._owns_llm = llm is None
        self.llm = llm or create_llm()
        self.session_id = session_id or str(uuid.uuid4())
        self.max_concurrent = max(1, max_concurrent)
        self.max_replans = max(0, max_replans)
        self.max_adversarial_rounds = max(0, max_adversarial_rounds)
        self.state_timeout_seconds = state_timeout_seconds
        self.memory_store = SharedMemoryStore(
            memory_db_path or os.getenv("DEEP_RESEARCH_MEMORY_DB", "data/deep_research_memory.db"),
            session_id=self.session_id,
            embedder=embedder,
        )
        self.compressor = ContextCompressor(llm=self.llm)
        self.planner = DeepResearchPlanner(self.llm, task_mode=self.task_mode)
        self.tool_executor = tool_executor or FunctionToolExecutor()
        self.worker = DeepResearchWorker(
            self.llm,
            task_mode=self.task_mode,
            tool_executor=self.tool_executor,
            compressor=self.compressor,
        )
        self.synthesizer = DeepResearchSynthesizer(self.llm, compressor=self.compressor, task_mode=self.task_mode)
        self.adversarial = AdversarialLoop(
            red_agent=RedAgent(self.llm, task_mode=self.task_mode),
            blue_agent=BlueAgent(self.llm, task_mode=self.task_mode),
            max_rounds=max_adversarial_rounds,
        )
        self.state_history: list[dict[str, Any]] = []
        self.num_replans = 0
        self.num_searches = 0
        self.event_sink = event_sink

    async def run(self, query: str) -> DeepResearchResult:
        start = time.monotonic()
        graph = ResearchStateGraph()
        results: list[WorkerResult] = []
        plan: ResearchPlan | None = None
        coverage_report = CoverageReport(complete=False)
        final_report: ResearchReport | None = None

        try:
            self._transition(FSMState.PLANNING)
            memory_context = self.memory_store.get_context_for_query(query, max_tokens=2500)
            plan = await self.planner.create_plan(query, memory_context)
            for state in plan.states:
                if not state.timeout_seconds:
                    state.timeout_seconds = self.state_timeout_seconds
                graph.add_state(state)
            self._emit(
                {
                    "type": "plan",
                    "plan": plan.to_dict(),
                    "states": [state.to_dict() for state in graph.states()],
                    "coverage_checklist": plan.coverage_checklist,
                }
            )

            while True:
                self._transition(FSMState.DISPATCHING)
                ready = graph.ready_states(limit=self.max_concurrent)
                if ready:
                    self._emit(
                        {
                            "type": "dispatch",
                            "ready_states": [state.to_dict() for state in ready],
                            "max_concurrent": self.max_concurrent,
                        }
                    )
                    batch = await self._run_ready_states(query, graph, ready, results)
                    results.extend(batch)

                self._transition(FSMState.COLLECTING)
                self._collect_to_memory(query, batch if ready else [])

                self._transition(FSMState.COVERAGE_CHECK)
                coverage_report = self._check_coverage(plan, graph, results)
                self._emit({"type": "coverage", "coverage": coverage_report.to_dict()})
                if coverage_report.complete and graph.all_terminal():
                    break
                if self._has_sufficient_final_answer(plan, graph, results, coverage_report):
                    for state in graph.states():
                        if state.status.value == "pending":
                            graph.mark_skipped(state.state_id, EARLY_STOP_REASON)
                    coverage_report = self._check_coverage(plan, graph, results)
                    self._emit(
                        {
                            "type": "early_stop",
                            "reason": "final_answer_resolved",
                            "coverage": coverage_report.to_dict(),
                        }
                    )
                    break

                if graph.ready_states():
                    continue

                should_replan = (
                    (graph.should_replan() or not coverage_report.complete)
                    and self.num_replans < self.max_replans
                )
                if not should_replan:
                    for state in graph.states():
                        if state.status.value == "pending":
                            graph.mark_skipped(state.state_id, "dependencies unavailable; research budget exhausted")
                    if not graph.ready_states() and graph.all_terminal():
                        break
                    if self.num_replans >= self.max_replans:
                        break
                    if not graph.ready_states():
                        break

                self._transition(FSMState.REPLANNING)
                self.num_replans += 1
                self._emit(
                    {
                        "type": "replan_start",
                        "round": self.num_replans,
                        "coverage": coverage_report.to_dict(),
                    }
                )
                await self._replan(query, plan, graph, results, coverage_report)
                self._emit(
                    {
                        "type": "replan_result",
                        "round": self.num_replans,
                        "states": [state.to_dict() for state in graph.states()],
                    }
                )
                if not graph.ready_states() and not graph.has_pending():
                    break

            self._transition(FSMState.SYNTHESIZING)
            final_report = await self.synthesizer.synthesize(
                query=query,
                plan=plan,
                results=list({result.state_id: result for result in results}.values()),
                state_history=self.state_history,
                states=[state.to_dict() for state in graph.states()],
                coverage=coverage_report.to_dict(),
                critique_history=[],
            )

            self._transition(FSMState.ADVERSARIAL_REVIEW)
            if self.max_adversarial_rounds > 0:
                final_report, critique = await self.adversarial.run(final_report)
                final_report.critique_history = critique

            self._transition(FSMState.FINALIZING)
            elapsed = time.monotonic() - start
            self._transition(FSMState.DONE)
            final_report.state_history = self.state_history
            final_report.states = [state.to_dict() for state in graph.states()]
            metadata = self._metadata(
                elapsed_seconds=elapsed,
                num_states=len(graph.states()),
                adversarial_rounds=len(final_report.critique_history),
            )
            metadata["coverage_complete"] = coverage_report.complete
            metadata["citations"] = citation_stats(
                [e for r in {r.state_id: r for r in results}.values() for e in r.evidence])
            metadata["research_status"] = "complete" if coverage_report.complete else "partial"
            if not coverage_report.complete:
                final_report.content = self._label_incomplete(final_report.content, coverage_report)
                final_report.confidence = min(final_report.confidence, 0.35)
            answer = extract_final_answer(final_report.content) if self.task_mode == TaskMode.MULTI_HOP else final_report.content
            self._emit(
                {
                    "type": "final",
                    "answer": answer,
                    "metadata": metadata,
                    "confidence": final_report.confidence,
                }
            )
            return DeepResearchResult(answer=answer, report=final_report, metadata=metadata)
        except Exception as exc:
            self._transition(FSMState.FAILED, {"error": str(exc)})
            elapsed = time.monotonic() - start
            content = self._failed_report(query, exc, results)
            final_report = ResearchReport(
                query=query,
                content=content,
                sources=[],
                confidence=0.0,
                state_history=self.state_history,
                states=[state.to_dict() for state in graph.states()],
                coverage=coverage_report.to_dict(),
                critique_history=[],
            )
            return DeepResearchResult(
                answer=content,
                report=final_report,
                metadata=self._metadata(
                    elapsed_seconds=elapsed,
                    num_states=len(graph.states()),
                    adversarial_rounds=0,
                    failed=True,
                ),
            )

        finally:
            if getattr(self, "_owns_llm", False):
                await self.llm.close()

    async def _run_ready_states(
        self,
        query: str,
        graph: ResearchStateGraph,
        ready: list[ResearchState],
        previous_results: list[WorkerResult],
    ) -> list[WorkerResult]:
        for state in ready:
            graph.mark_running(state.state_id)
            self._emit({"type": "state_start", "state": state.to_dict()})

        async def run_one(state: ResearchState) -> WorkerResult:
            try:
                memory_context = self.memory_store.get_context_for_query(state.description, max_tokens=1500)
                result = await asyncio.wait_for(
                    self.worker.run(
                        query=query,
                        state=state,
                        memory_context=memory_context,
                        prior_results=[item.to_dict() for item in previous_results],
                    ),
                    timeout=state.timeout_seconds or self.state_timeout_seconds,
                )
                self.num_searches += len(result.tool_calls)
                result.state_id = state.state_id
                self._defer_planned_questions(result, state, graph)
                if result.normalized_status() == "failed":
                    graph.mark_failed(state.state_id, result.error or result.summary)
                elif result.normalized_status() != "resolved" or not any(usable_evidence(e) for e in result.evidence) or result.open_questions or result.conflicts:
                    result.status = "partial"
                    graph.mark_partial(state.state_id, "Unresolved questions, conflicts, or missing source-linked evidence")
                else:
                    graph.mark_success(state.state_id)
                    self._close_verified_memory_conflicts(state)
                self._emit(
                    {
                        "type": "state_result",
                        "state_id": state.state_id,
                        "status": graph.get_state(state.state_id).status.value,
                        "result": result.to_dict(),
                    }
                )
                return result
            except asyncio.TimeoutError:
                graph.mark_failed(state.state_id, "state timeout")
                result = WorkerResult(
                    status="failed",
                    state_id=state.state_id,
                    summary="State execution timed out.",
                    error="timeout",
                    open_questions=["State timed out."],
                )
                self._emit(
                    {
                        "type": "state_result",
                        "state_id": state.state_id,
                        "status": "failed",
                        "result": result.to_dict(),
                    }
                )
                return result
            except Exception as exc:
                graph.mark_failed(state.state_id, str(exc))
                result = WorkerResult(
                    status="failed",
                    state_id=state.state_id,
                    summary="State execution failed.",
                    error=str(exc),
                    open_questions=[str(exc)],
                )
                self._emit(
                    {
                        "type": "state_result",
                        "state_id": state.state_id,
                        "status": "failed",
                        "result": result.to_dict(),
                    }
                )
                return result

        return await asyncio.gather(*(run_one(state) for state in ready))

    PLANNED_QUESTION_OVERLAP = 0.35

    def _close_verified_memory_conflicts(self, state: ResearchState) -> None:
        """A verify state that resolved cleanly settles the memory conflict it was created for.

        Memory conflicts are detected heuristically at insert time and nothing else closes
        them, so without this an entry pair flagged once would block coverage forever."""
        if state.state_type.value != "verify" or not state.backtrack_reason:
            return
        dismiss = getattr(self.memory_store, "dismiss_conflicts_matching", None)
        if callable(dismiss):
            dismiss(f"{state.backtrack_reason}\n{state.description}")

    @classmethod
    def _defer_planned_questions(cls, result: WorkerResult, state: ResearchState, graph: ResearchStateGraph) -> None:
        """Move open questions that restate another planned state's job into recommended_followups.

        Workers tend to list the rest of the overall query as their own open question, which
        would mark a finished node partial and block every dependent. A question that is
        already another node's description is that node's problem, not this one's."""
        others = [s.description for s in graph.states() if s.state_id != state.state_id and s.description]
        kept, deferred = [], []
        for question in result.open_questions:
            if any(text_overlap(question, desc) >= cls.PLANNED_QUESTION_OVERLAP for desc in others):
                deferred.append(question)
            else:
                kept.append(question)
        if deferred:
            result.open_questions = kept
            result.recommended_followups = list(dict.fromkeys([*result.recommended_followups, *deferred]))

    def _collect_to_memory(self, query: str, results: list[WorkerResult]) -> None:
        for result in results:
            for idx, evidence in enumerate(result.evidence):
                claim = str(evidence.get("claim", "")).strip()
                if not claim:
                    continue
                entry = MemoryEntry(
                    # Built-in hash() is salted per process, so IDs must use a stable digest.
                    entry_id=f"{result.state_id}:{idx}:{hashlib.sha1(claim.encode('utf-8')).hexdigest()[:16]}",
                    session_id=self.session_id,
                    claim=claim,
                    source=str(evidence.get("source", "")),
                    url=str(evidence.get("url", "")),
                    confidence=confidence_to_float(evidence.get("confidence")),
                    agent_id=result.state_id,
                    timestamp=time.time(),
                    evidence_type=str(evidence.get("evidence_type", "secondary")),
                    topic=query[:80],
                    embedding=[],
                    metadata={"date": evidence.get("date", ""), "state_id": result.state_id},
                )
                self.memory_store.put(entry)

    def _check_coverage(
        self,
        plan: ResearchPlan,
        graph: ResearchStateGraph,
        results: list[WorkerResult],
    ) -> CoverageReport:
        # Retries replace the current assessment, while full history stays in results.
        latest = {result.state_id: result for result in results}
        qualified = []
        evidence_gaps = []
        for state in graph.states():
            result = latest.get(state.state_id)
            if not result:
                continue
            evidence = [e for e in result.evidence if usable_evidence(e)]
            if not evidence:
                evidence_gaps.append(state.state_id)
            if (state.status.value == "success" and result.normalized_status() == "resolved"
                    and evidence and not result.open_questions and not result.conflicts):
                qualified.append((state, evidence))
        expected = list(dict.fromkeys(plan.coverage_checklist))
        covered_tags = set()
        for state, evidence in qualified:
            for tag in state.coverage_tags:
                if tag == "official" and not any(e.get("evidence_type") == "primary" for e in evidence):
                    continue
                if tag == "recency" and not any(in_time_scope(e, plan.scope) for e in evidence):
                    continue
                covered_tags.add(tag)
        covered = [tag for tag in expected if tag in covered_tags]
        missing = [tag for tag in expected if tag not in covered_tags]
        failed = [s.state_id for s in graph.states() if s.status.value == "failed"]
        partial = [s.state_id for s in graph.states() if s.status.value == "partial"]
        blocked = [s.state_id for s in graph.states() if s.status.value in {"pending", "running", "skipped"}
                   and s.metadata.get("skip_reason") != EARLY_STOP_REASON]
        questions = list(dict.fromkeys(q for r in latest.values() for q in r.open_questions if q))
        conflicts = list(dict.fromkeys(c for r in latest.values() for c in r.conflicts if c))
        conflicts.extend(record.claim_1 + " <-> " + record.claim_2 for record in self.memory_store.get_conflicts("open"))
        has_primary = any(e.get("evidence_type") == "primary" for _, evidence in qualified for e in evidence)
        needs_recency = "recency" in expected or any("最新" in flag or "时效" in flag for flag in plan.risk_flags)
        missing_recency = needs_recency and not any(
            in_time_scope(e, plan.scope) for _, evidence in qualified for e in evidence)
        if missing_recency and "recency" not in missing:
            missing.append("recency")
        notes = []
        stats = citation_stats([e for r in latest.values() for e in r.evidence])
        if stats["unverified"] or stats["unsupported"]:
            notes.append(f"引用核验：{stats['unverified']} 条引用的 URL 未出现在工具结果中，"
                         f"{stats['unsupported']} 条陈述未被来源原文支持；这些证据不计入覆盖。")
        if not has_primary:
            notes.append("缺少标记为一手来源的有效证据；来源真实性仍需引用核验。")
        if missing_recency:
            notes.append("时效证据缺失、日期无效或不在范围内；需 scope.date_start/date_end 和证据 date（YYYY-MM-DD）。")
        return CoverageReport(
            complete=bool(qualified) and not any([missing, failed, partial, blocked, evidence_gaps, questions, conflicts]),
            covered=covered, missing=missing, failed_states=failed,
            partial_states=partial, blocked_states=blocked, evidence_gaps=evidence_gaps,
            open_questions=questions, conflicts=conflicts,
            missing_authoritative_sources=not has_primary,
            missing_recency=missing_recency, notes=notes,
        )

    def _has_sufficient_final_answer(
        self,
        plan: ResearchPlan,
        graph: ResearchStateGraph,
        results: list[WorkerResult],
        coverage: CoverageReport,
    ) -> bool:
        """Multi-hop only: skip remaining pending states once every required tag is
        covered by clean, source-linked evidence and an answer state has resolved."""
        if self.task_mode != TaskMode.MULTI_HOP or not graph.has_pending():
            return False
        if any([coverage.missing, coverage.failed_states, coverage.partial_states,
                coverage.evidence_gaps, coverage.open_questions, coverage.conflicts]):
            return False
        if any(state.status.value == "running" for state in graph.states()):
            return False
        states_by_id = {state.state_id: state for state in graph.states()}
        latest = {result.state_id: result for result in results}
        for result in latest.values():
            state = states_by_id.get(result.state_id)
            if not state or state.status.value != "success" or result.conflicts:
                continue
            candidate = (result.candidate_answer or "").strip()
            if not candidate or candidate.lower() in {"unknown", "n/a", "none", "null"}:
                continue
            expected_output = state.expected_output.lower()
            answer_state = (
                "final_answer" in state.coverage_tags
                or "answer" in state.coverage_tags
                or "answer_format" in state.coverage_tags
                or "candidate_answer" in expected_output
                or "final" in expected_output
                or bool(result.answer_form_hint)
            )
            if answer_state and self._max_evidence_confidence(result) >= 0.75:
                return True
        return False

    @staticmethod
    def _max_evidence_confidence(result: WorkerResult) -> float:
        scores = [confidence_to_float(e.get("confidence")) for e in result.evidence if usable_evidence(e)]
        return max(scores, default=0.0)

    async def _replan(
        self,
        query: str,
        plan: ResearchPlan,
        graph: ResearchStateGraph,
        results: list[WorkerResult],
        coverage: CoverageReport,
    ) -> None:
        latest = {result.state_id: result for result in results}
        successful = [result.to_dict() for result in latest.values() if result.normalized_status() == "resolved"]
        failed_states = []
        for state in graph.states():
            if state.status.value not in {"failed", "partial", "pending"}:
                continue
            payload = state.to_dict()
            payload["failure_reason"] = state.metadata.get("failure_reason") or state.metadata.get("incomplete_reason") or "dependencies unavailable"
            failed_states.append(payload)
        memory_context = self.memory_store.get_context_for_query(query, max_tokens=2500)
        new_states = await self.planner.replan(
            query=query,
            plan=plan,
            successful_results=successful,
            failed_states=failed_states,
            coverage_gaps=list(dict.fromkeys([*coverage.missing, *coverage.open_questions, *coverage.conflicts])),
            conflicts=coverage.conflicts,
            memory_context=memory_context,
        )
        graph.retry_incomplete()
        existing = {state.state_id for state in graph.states()}
        for state in new_states:
            original_id = state.state_id
            while state.state_id in existing:
                state.state_id += f"_replan_{self.num_replans}"
            if state.state_id != original_id:
                for dependent in new_states:
                    dependent.dependencies = [state.state_id if dep == original_id else dep for dep in dependent.dependencies]
            existing.add(state.state_id)
            graph.add_state(state)

    def _label_incomplete(self, content: str, coverage: CoverageReport) -> str:
        import json
        from deep_research.llm import extract_json_object
        detail = "研究未完成，证据不足以支持确定结论。"
        gaps = list(dict.fromkeys([*coverage.missing, *coverage.evidence_gaps,
                                  *coverage.partial_states, *coverage.failed_states, *coverage.blocked_states]))
        if gaps:
            detail += "待补充：" + "、".join(gaps)
        if self.task_mode == TaskMode.MULTI_HOP:
            data = extract_json_object(content) or {}
            data["candidate_answer"] = data.get("answer", "")
            data["answer"] = "证据不足，暂无法确定答案"
            data["confidence"] = "low"
            data["uncertainty"] = detail
            return json.dumps(data, ensure_ascii=False)
        return "> " + detail + "\n\n" + content

    def _transition(self, state: FSMState, detail: dict[str, Any] | None = None) -> None:
        event = StateHistoryEvent(state=state.name, detail=detail or {}).to_dict()
        self.state_history.append(event)
        self._emit({"type": "state", **event})

    def _emit(self, event: dict[str, Any]) -> None:
        if not self.event_sink:
            return
        try:
            self.event_sink(event)
        except Exception:
            pass

    def _metadata(
        self,
        *,
        elapsed_seconds: float,
        num_states: int,
        adversarial_rounds: int,
        failed: bool = False,
    ) -> dict[str, Any]:
        return {
            "mode": "deep_research",
            "task_mode": self.task_mode.value,
            "session_id": self.session_id,
            "num_states": num_states,
            "num_searches": self.num_searches,
            "num_replans": self.num_replans,
            "adversarial_rounds": adversarial_rounds,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "failed": failed,
            "research_status": "failed" if failed else "partial",
            "coverage_complete": False,
            "llm_usage": self._llm_usage(),
            "compression": self.compressor.get_stats(),
        }

    def _llm_usage(self) -> dict[str, Any]:
        getter = getattr(self.llm, "get_usage", None)
        if not callable(getter):
            return {}
        try:
            return dict(getter())
        except Exception:
            return {}

    @staticmethod
    def _failed_report(query: str, exc: Exception, results: list[WorkerResult]) -> str:
        lines = [
            f"# 研究报告：{query}",
            "",
            "## 执行摘要",
            "研究流程未能完整完成，以下为错误说明和已获得的有限证据。",
            "",
            "## 证据质量与不确定性",
            f"- 错误：{type(exc).__name__}: {exc}",
        ]
        for result in results:
            if result.summary:
                lines.append(f"- {result.state_id}: {result.summary}")
        return "\n".join(lines)
