from __future__ import annotations

import json

from deep_research.adversarial.verdict import RedVerdict
from deep_research.llm import LLMClient, extract_json_object
from deep_research.prompts import BLUE_AGENT_SYSTEM_PROMPT
from deep_research.schemas import ResearchReport
from deep_research.task_modes import TaskMode, stage_prompt


class BlueAgent:
    def __init__(self, llm: LLMClient, *, task_mode: TaskMode | str = TaskMode.MULTI_HOP) -> None:
        self.llm = llm
        self.task_mode = TaskMode(task_mode)

    async def defend(self, report: ResearchReport, verdict: RedVerdict) -> tuple[ResearchReport, dict]:
        if not verdict.issues:
            return report, {"changes": [], "remaining_risks": []}
        prompt = f"""请修复以下报告。

报告：
{report.content[:8000]}

评审意见：
{json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2)}

可用 evidence：
{json.dumps(report.sources[:30], ensure_ascii=False, indent=2)}"""
        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": stage_prompt("review", self.task_mode) + "\n以上格式用于 fixed_report 字符串中的报告；外层仍使用下述修订 JSON。\n" + BLUE_AGENT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            data = extract_json_object(raw) or {}
            fixed = data.get("fixed_report")
            if isinstance(fixed, str) and fixed.strip():
                report.content = fixed
            return report, {
                "changes": data.get("changes", []),
                "remaining_risks": data.get("remaining_risks", []),
            }
        except Exception as exc:
            return report, {"changes": [], "remaining_risks": [str(exc)]}
