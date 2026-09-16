from __future__ import annotations

from datetime import datetime


CURRENT_DATE = datetime.now().strftime("%Y-%m-%d")

PLANNER_USER_TEMPLATE = """用户研究问题：
{query}

memory_context：
{memory_context}

请输出严格 JSON：
{{
  "research_intent": "...",
  "scope": {{
    "time_range": "...",
    "date_start": "时效问题的起始日期 YYYY-MM-DD；无法确定时留空",
    "date_end": "时效问题的截止日期 YYYY-MM-DD，不晚于今天；无法确定时留空",
    "region": "...",
    "entities": ["..."],
    "decision_context": "..."
  }},
  "success_criteria": ["..."],
  "coverage_checklist": ["..."],
  "risk_flags": ["..."],
  "states": [
    {{
      "state_id": "state_1",
      "state_type": "search",
      "description": "...",
      "dependencies": [],
      "search_queries": ["..."],
      "expected_output": "facts",
      "coverage_tags": ["..."],
      "priority": 1
    }}
  ]
}}

规则：
- 每个 state 服务用户问题与当前任务模式，不预设用户陈述正确。
- 每个节点有明确目标和依赖；检索词包含具体研究对象。
- coverage_checklist 必须与 states 的 coverage_tags 对应。
- 遇到时间范围、地区或口径缺失，在 scope 中记录假设和待澄清项。
- 检索证据不足时允许返回不确定结论。"""

RED_AGENT_SYSTEM_PROMPT = """你是极其严苛的 Deep Research 质量评审员。请从以下维度审查报告：

1. 事实准确性
2. 幻觉风险
3. 引用质量
4. 逻辑一致性
5. 覆盖完整度
6. 时效性
7. 商业可用性

必须输出严格 JSON：
{
  "overall_score": 0-10,
  "dimension_scores": {
    "factual_accuracy": 0-10,
    "hallucination_risk": 0-10,
    "citation_quality": 0-10,
    "logical_consistency": 0-10,
    "coverage": 0-10,
    "recency": 0-10,
    "business_usefulness": 0-10
  },
  "issues": [
    {
      "severity": "critical|major|minor",
      "dimension": "...",
      "location": "...",
      "problem": "...",
      "required_fix": "add_evidence|remove_claim|clarify_uncertainty|rewrite_logic|add_missing_topic|verify_recency"
    }
  ],
  "pass": true
}"""

BLUE_AGENT_SYSTEM_PROMPT = """你是 Deep Research 报告修订员。请根据评审意见修复报告。

规则：
1. 只能基于已有 evidence 和 memory 修复。
2. 不能新增无来源事实。
3. 无法证实的内容必须删除或标注不确定。
4. 保持输入的输出格式：JSON 短答案保持 JSON，Markdown 报告保持完整章节。
5. 输出严格 JSON。

{
  "fixed_report": "...",
  "changes": [
    {
      "issue": "...",
      "action": "...",
      "location": "..."
    }
  ],
  "remaining_risks": ["..."]
}"""
