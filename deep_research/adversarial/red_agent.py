from __future__ import annotations

import json
from typing import Any

from deep_research.adversarial.verdict import RedVerdict
from deep_research.llm import LLMClient, extract_json_object
from deep_research.prompts import RED_AGENT_SYSTEM_PROMPT
from deep_research.schemas import ResearchReport
from deep_research.task_modes import TaskMode, stage_prompt


class RedAgent:
    def __init__(self, llm: LLMClient, *, task_mode: TaskMode | str = TaskMode.MULTI_HOP) -> None:
        self.llm = llm
        self.task_mode = TaskMode(task_mode)

    async def attack(self, report: ResearchReport) -> RedVerdict:
        prompt = f"""原始问题：{report.query}

报告：
{report.content[:8000]}

来源：
{json.dumps(report.sources[:30], ensure_ascii=False, indent=2)}

覆盖状态：
{json.dumps(report.coverage, ensure_ascii=False, indent=2)}"""
        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": stage_prompt("review", self.task_mode) + "\n以上格式用于被审阅的报告；你自己的输出必须遵循下面的评审 JSON。\n" + RED_AGENT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
            data = extract_json_object(raw)
            if data:
                return RedVerdict.from_dict(data, raw_feedback=raw)
            return RedVerdict(overall_score=5.0, raw_feedback=raw)
        except Exception as exc:
            return RedVerdict(overall_score=5.0, raw_feedback=f"red_agent_error: {exc}")
