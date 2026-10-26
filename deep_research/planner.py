from __future__ import annotations

import json
import re
from typing import Any

from deep_research.llm import LLMClient, extract_json_object
from deep_research.prompts import PLANNER_USER_TEMPLATE
from deep_research.schemas import ResearchPlan, ResearchState, ResearchStateType
from deep_research.task_modes import TaskMode, MODE_COVERAGE, MODE_GOALS, stage_prompt


class DeepResearchPlanner:
    def __init__(self, llm: LLMClient, *, task_mode: TaskMode | str = TaskMode.MULTI_HOP) -> None:
        self.llm = llm
        self.task_mode = TaskMode(task_mode)

    async def create_plan(self, query: str, memory_context: str = "") -> ResearchPlan:
        prompt = PLANNER_USER_TEMPLATE.format(
            query=query,
            memory_context=memory_context or "无",
        )
        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": stage_prompt("planner", self.task_mode)},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            data = extract_json_object(raw)
            if data:
                return self._parse_plan(query, data)
        except Exception:
            pass
        return self._fallback_plan(query)

    async def replan(
        self,
        *,
        query: str,
        plan: ResearchPlan,
        successful_results: list[dict[str, Any]],
        failed_states: list[dict[str, Any]],
        coverage_gaps: list[str],
        conflicts: list[str],
        memory_context: str,
    ) -> list[ResearchState]:
        payload = {
            "original_query": query,
            "coverage_checklist": plan.coverage_checklist,
            "successful_results": successful_results,
            "failed_states": failed_states,
            "coverage_gaps": coverage_gaps,
            "conflicts": conflicts,
            "memory_context": memory_context,
        }
        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": stage_prompt("replanner", self.task_mode)},
                    {
                        "role": "user",
                        "content": "请基于以下状态输出新增或替换 states：\n"
                        + json.dumps(payload, ensure_ascii=False, indent=2),
                    },
                ],
                temperature=0.1,
            )
            data = extract_json_object(raw)
            if data:
                states_raw = data.get("states") or data.get("new_states") or data.get("replacements") or []
                states = self._parse_states(states_raw, prefix="replan")
                if states:
                    return states
        except Exception:
            pass
        return self._fallback_replan_states(
            query,
            coverage_gaps,
            conflicts,
            failed_states,
            successful_results=successful_results,
        )

    def _parse_plan(self, query: str, data: dict[str, Any]) -> ResearchPlan:
        states = self._parse_states(data.get("states", []), prefix="state")
        if self.task_mode == TaskMode.MULTI_HOP and not any(state.state_type == ResearchStateType.VERIFY for state in states):
            states.append(
                ResearchState(
                    state_id="state_verify",
                    state_type=ResearchStateType.VERIFY,
                    description="确认最终候选答案的名称和答案格式",
                    dependencies=[states[0].state_id] if states else [],
                    search_queries=["final answer candidate official name"],
                    expected_output="facts",
                    coverage_tags=["verification"],
                    priority=99,
                )
            )
        if self.task_mode != TaskMode.MULTI_HOP:
            required = self._infer_coverage(query)
            if not states:
                return self._fallback_plan(query)
            present = {tag for state in states for tag in state.coverage_tags}
            used_ids = {state.state_id for state in states}
            for index, tag in enumerate(required):
                if tag not in present:
                    state_id = f"mode_{tag}_{index}"
                    while state_id in used_ids:
                        state_id += "_extra"
                    used_ids.add(state_id)
                    states.append(self._mode_state(query, tag, state_id))
            data = dict(data)
            data["coverage_checklist"] = list(dict.fromkeys([*data.get("coverage_checklist", []), *required]))
        return ResearchPlan(
            query=query,
            research_intent=str(data.get("research_intent", query)),
            scope=dict(data.get("scope", {})),
            success_criteria=list(data.get("success_criteria", [])),
            coverage_checklist=self._normalize_checklist(data.get("coverage_checklist", []), states) or self._infer_coverage(query),
            risk_flags=list(data.get("risk_flags", [])),
            states=states,
            raw_plan=data,
        )

    @staticmethod
    def _normalize_checklist(items: Any, states: list[ResearchState]) -> list[str]:
        """Reduce "tag: description" checklist entries to the tag the states actually carry.

        Models like to write the checklist as "answer: give the county name" while tagging
        states with plain "answer"; the two never matched, so coverage stayed incomplete
        until a replan created states tagged with the full sentence."""
        known = {tag for state in states for tag in state.coverage_tags}
        normalized = []
        for item in items if isinstance(items, list) else []:
            text = str(item).strip()
            if not text:
                continue
            head = re.split(r"[:：]", text, maxsplit=1)[0].strip()
            normalized.append(head if head and head != text and head in known else text)
        return list(dict.fromkeys(normalized))

    def _parse_states(self, states_raw: Any, prefix: str) -> list[ResearchState]:
        if not isinstance(states_raw, list):
            return []
        states = []
        seen: set[str] = set()
        for idx, item in enumerate(states_raw, 1):
            if not isinstance(item, dict):
                continue
            item = dict(item)
            item.setdefault("state_id", f"{prefix}_{idx}")
            if item["state_id"] in seen:
                item["state_id"] = f"{item['state_id']}_{idx}"
            seen.add(item["state_id"])
            try:
                state = ResearchState.from_dict(item)
            except Exception:
                continue
            if not state.description:
                continue
            if not state.search_queries:
                state.search_queries = [state.description]
            states.append(state)
        return states

    def _fallback_plan(self, query: str) -> ResearchPlan:
        if self.task_mode != TaskMode.MULTI_HOP:
            coverage = self._infer_coverage(query)
            return ResearchPlan(
                query=query, research_intent=MODE_GOALS[self.task_mode],
                coverage_checklist=coverage,
                states=[self._mode_state(query, tag, f"state_{i}") for i, tag in enumerate(coverage, 1)],
                risk_flags=["planner_fallback"], raw_plan={"fallback": True},
            )
        states = [
            ResearchState(
                state_id="state_1",
                state_type=ResearchStateType.SEARCH,
                description=f"检索与研究问题直接相关的权威来源：{query}",
                dependencies=[],
                search_queries=[query],
                expected_output="facts",
                coverage_tags=["official", "background"],
                priority=1,
            ),
            ResearchState(
                state_id="state_2",
                state_type=ResearchStateType.VERIFY,
                description="确认最终候选答案的名称和答案格式",
                dependencies=["state_1"],
                search_queries=["final answer candidate official name"],
                expected_output="facts",
                coverage_tags=["verification"],
                priority=2,
            ),
        ]
        return ResearchPlan(
            query=query,
            research_intent=query,
            scope={"time_range": "未指定", "region": "未指定", "entities": [], "decision_context": "业务研究"},
            success_criteria=["直接回答研究问题", "关键事实有来源"],
            coverage_checklist=self._infer_coverage(query),
            risk_flags=["planner_fallback"],
            states=states,
            raw_plan={"fallback": True},
        )

    def _fallback_replan_states(
        self,
        query: str,
        coverage_gaps: list[str],
        conflicts: list[str],
        failed_states: list[dict[str, Any]],
        successful_results: list[dict[str, Any]] | None = None,
    ) -> list[ResearchState]:
        states: list[ResearchState] = []
        for idx, gap in enumerate(coverage_gaps, 1):
            states.append(
                ResearchState(
                    state_id=f"replan_gap_{idx}",
                    state_type=ResearchStateType.SEARCH,
                    description=f"补充覆盖缺口：{gap}",
                    dependencies=[],
                    search_queries=[f"{gap} official evidence"],
                    coverage_tags=[gap],
                    priority=1,
                    parent_state_id=None,
                    backtrack_reason=f"coverage gap: {gap}",
                )
            )
        # Every unresolved conflict needs verification; a candidate name is not evidence.
        for idx, conflict in enumerate(conflicts, 1):
            states.append(
                ResearchState(
                    state_id=f"verify_conflict_{idx}",
                    state_type=ResearchStateType.VERIFY,
                    description=f"核查矛盾事实：{conflict}",
                    dependencies=[],
                    search_queries=[conflict[:200]],
                    coverage_tags=["verification"],
                    priority=0,
                    backtrack_reason=f"conflict: {conflict}",
                )
            )
        for idx, failed in enumerate(failed_states, 1):
            states.append(
                ResearchState(
                    state_id=f"retry_failed_{idx}",
                    state_type=ResearchStateType.SEARCH,
                    description=f"重试失败任务：{failed.get('description', query)}",
                    dependencies=[],
                    search_queries=[str(failed.get("description") or query)],
                    coverage_tags=list(failed.get("coverage_tags", [])),
                    priority=2,
                    backtrack_reason=str(failed.get("failure_reason", "failed state")),
                )
            )
        return states

    def _mode_state(self, query: str, tag: str, state_id: str) -> ResearchState:
        descriptions = {
            "supporting_evidence": "寻找支持待核查声明的原始证据",
            "counter_evidence": "主动寻找反驳声明的证据与反例",
            "verdict": "核验声明的适用范围、日期和语境，为逐项判定提供证据",
            "scope": "查明研究对象、时间范围与关键背景，标注范围假设",
            "findings": "检索各主题或比较对象的具体事实与数据",
            "synthesis": "检索支撑跨主题比较与综合结论的证据",
            "limitations": "检索风险、反例与证据局限",
            "recency": "核验来源日期与问题要求的数据周期",
        }
        description = f"{descriptions.get(tag, tag)}：{query}"
        return ResearchState(state_id=state_id, state_type="search", description=description,
                             search_queries=[description], coverage_tags=[tag])

    def _infer_coverage(self, query: str) -> list[str]:
        lowered = query.lower()
        if self.task_mode != TaskMode.MULTI_HOP:
            checklist = list(MODE_COVERAGE[self.task_mode])
            if any(word in lowered for word in ["最新", "当前", "近一年", "today", "current", "latest"]):
                checklist.append("recency")
            return checklist
        checklist = ["official", "verification"]
        if any(word in query for word in ["风险", "合规", "监管"]) or "risk" in lowered:
            checklist.append("risk")
        if any(word in query for word in ["竞品", "竞争", "对比"]) or "competitor" in lowered:
            checklist.append("competitor")
        if any(word in query for word in ["最新", "当前", "近一年", "today", "current"]):
            checklist.append("recency")
        return checklist
