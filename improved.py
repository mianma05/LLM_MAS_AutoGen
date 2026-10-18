"""改进版：在提高版「调研 + 审阅」流程之后，新增「PLC 代码生成 + 校验」回路。

运行：
    python improved.py --topic "LLM-assisted PLC code generation and industrial automation programming"

与提高版（advanced.py）的差异：
1. 保留原有四 Agent（Planner / Search / Writer / Reviewer）的调研流程，复用 advanced.build_team。
2. 调研通过后进入第二阶段，新增两个 Agent：
   - CodeGeneratorAgent：根据调研结论生成 IEC 61131-3 结构化文本(ST)代码示例；
   - CodeVerifierAgent：调用确定性工具 check_st_code 校验代码，失败时回传修订。
3. 校验结果（工具返回值）驱动「生成 → 校验 → 修订」回路，最多修订一次。

产物（outputs/improved_*/ 下）：
    report.md         调研报告（阶段 1）
    review.md         审阅意见 + 引用复核（阶段 1）
    generated_code.st 最终生成的 ST 代码（阶段 2）
    verification.md   Agent 校验结论 + 确定性复核结果（阶段 2）
    trace.jsonl       两个阶段的完整消息与工具调用轨迹
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Sequence

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.messages import BaseAgentEvent, BaseChatMessage, TextMessage
from autogen_agentchat.teams import SelectorGroupChat

from advanced import build_team as build_research_team
from common import (
    build_model_client,
    event_record,
    last_text,
    load_settings,
    make_run_dir,
    parse_args,
    remove_markers,
    save_json,
)
from plc_tools import check_st_code
from research_tools import audit_citations, get_registry, reset_registry


def build_code_team(model_client: Any) -> tuple[SelectorGroupChat, dict[str, int]]:
    """构造第二阶段「代码生成 + 校验」Team，并返回可观察的修订次数。"""

    generator = AssistantAgent(
        "CodeGeneratorAgent",
        model_client=model_client,
        description="根据调研结论生成 IEC 61131-3 ST 代码示例。",
        system_message=(
            "你是 PLC 代码生成 Agent。根据调研报告的主题，生成一段可演示的 IEC 61131-3 "
            "结构化文本(ST)代码：一个带启动/停止按钮与急停安全联锁的电机控制程序，"
            "包含 IF/ELSE 分支和一个 TON 延时定时器。代码放在 ```st 代码块中。"
            "注意 ST 的赋值运算符是 ':=' 而不是 '='。"
            "若 CodeVerifierAgent 的校验结果为 FAIL，请按指出的问题输出修正后的完整代码；"
            "若为 PASS，则说明无需修订。"
        ),
    )
    verifier = AssistantAgent(
        "CodeVerifierAgent",
        model_client=model_client,
        tools=[check_st_code],
        description="用确定性工具校验生成的 ST 代码。",
        system_message=(
            "你是代码校验 Agent。把 CodeGeneratorAgent 的完整 ```st 代码块内容原样作为 "
            "code 参数调用 check_st_code 工具。只依据工具返回的结果判断，不要凭感觉判断。"
        ),
        # 与 SearchAgent 相同：关闭反思，让工具结果直接作为摘要传给下一位，
        # 避免 deepseek 在工具调用后的二次反思不稳定（可能不返回文本）。
        reflect_on_tool_use=False,
        max_tool_iterations=1,
    )

    state = {"revisions": 0}

    def choose_code_next(
        messages: Sequence[BaseAgentEvent | BaseChatMessage], state: dict[str, int]
    ) -> str | None:
        names = {"CodeGeneratorAgent", "CodeVerifierAgent"}
        for message in reversed(messages):
            source = getattr(message, "source", None)
            if source not in names:
                continue
            content = str(getattr(message, "content", ""))
            if source == "CodeGeneratorAgent":
                return "CodeVerifierAgent"
            if source == "CodeVerifierAgent":
                # 工具返回以 FAIL 开头 → 回退修订（确定性，最多一次）
                if content.strip().startswith("FAIL") and state["revisions"] < 1:
                    state["revisions"] += 1
                    return "CodeGeneratorAgent"
                return None
        return "CodeGeneratorAgent"

    team = SelectorGroupChat(
        [generator, verifier],
        model_client=model_client,
        selector_func=lambda messages: choose_code_next(messages, state),
        allow_repeated_speaker=True,
        # 工具返回 PASS 时以确定性文字终止，避免退回模型选择器产生多余消息
        termination_condition=TextMentionTermination(
            "未发现明显问题", sources=["CodeVerifierAgent"]
        )
        | MaxMessageTermination(12),
        max_turns=6,
    )
    return team, state


async def run_and_trace(team: Any, task: str, trace_file: Any) -> list[Any]:
    """运行一个 Team，并把完整事件追加写入 trace.jsonl。"""

    events: list[Any] = []
    async for event in team.run_stream(task=task):
        events.append(event)
        trace_file.write(json.dumps(event_record(event), ensure_ascii=False) + "\n")
        if isinstance(event, TextMessage):
            text = str(event.content).replace("\n", " ").strip()
            print(f"[{event.source}] {text[:260]}")
    return events


def extract_code(text: str) -> str:
    """从消息中提取 ```st ... ``` 代码块；找不到时返回原文。"""

    match = re.search(r"```(?:st|iecst|iec)?[ \t]*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


def last_code_block(events: Sequence[Any], source: str) -> str:
    """取某个 Agent 最后一次包含代码块的消息，并提取其中的代码。"""

    for event in reversed(list(events)):
        if isinstance(event, TextMessage) and getattr(event, "source", None) == source:
            text = str(event.content)
            if "```" in text:
                return extract_code(text)
    return ""


def extract_check_results(events: Sequence[Any]) -> list[str]:
    """提取 CodeVerifierAgent 每次 check_st_code 工具调用的返回结果。"""

    results: list[str] = []
    for event in events:
        if event.__class__.__name__ != "ToolCallExecutionEvent":
            continue
        if getattr(event, "source", None) != "CodeVerifierAgent":
            continue
        for item in getattr(event, "content", []) or []:
            name = getattr(item, "name", None)
            content = getattr(item, "content", None)
            if name == "check_st_code" and content is not None:
                results.append(str(content))
    return results


async def main() -> None:
    args = parse_args("改进版：调研 + PLC 代码生成与校验回路")
    settings = load_settings()
    reset_registry()
    out_dir = make_run_dir("improved")
    model_client = build_model_client(settings)

    research_team, research_state = build_research_team(model_client)
    code_team, code_state = build_code_team(model_client)

    research_task = (
        f"请围绕以下主题完成一次小型技术调研：{args.topic}\n"
        "要求：只根据检索到的证据写作，面向本科生，保留引用编号。"
    )

    try:
        with (out_dir / "trace.jsonl").open("w", encoding="utf-8") as trace_file:
            print("\n===== 阶段 1：调研（规划 → 检索 → 写作 → 审阅）=====")
            research_events = await run_and_trace(research_team, research_task, trace_file)

            report = remove_markers(last_text(research_events, "WriterAgent"))
            review = last_text(research_events, "ReviewerAgent")
            post_audit = audit_citations(report)
            review_full = review + "\n\n课后引用复核：" + post_audit

            code_task = (
                f"研究主题：{args.topic}\n"
                f"调研结论摘录：\n{report[:1500]}\n\n"
                "请据此生成一段可演示的 IEC 61131-3 结构化文本(ST)代码示例。"
            )
            print("\n===== 阶段 2：代码生成与校验 =====")
            code_events = await run_and_trace(code_team, code_task, trace_file)

        final_code = last_code_block(code_events, "CodeGeneratorAgent")
        check_results = extract_check_results(code_events)
        deterministic = check_st_code(final_code)

        agent_verdict = "".join(
            f"第 {i} 次工具调用结果：\n{result}\n\n"
            for i, result in enumerate(check_results, 1)
        )
        if not agent_verdict:
            agent_verdict = "（未记录到工具调用结果）\n"

        (out_dir / "report.md").write_text(report + "\n", encoding="utf-8")
        (out_dir / "review.md").write_text(review_full + "\n", encoding="utf-8")
        (out_dir / "generated_code.st").write_text(final_code + "\n", encoding="utf-8")
        (out_dir / "verification.md").write_text(
            "Agent 工具校验（check_st_code）：\n"
            + agent_verdict
            + "\n确定性复核（check_st_code）：\n"
            + deterministic
            + "\n",
            encoding="utf-8",
        )
        save_json(out_dir / "evidence_registry.json", get_registry())
        safe_settings = {
            "base_url": settings["base_url"],
            "model": settings["model"],
            "max_tokens": settings["max_tokens"],
            "temperature": settings["temperature"],
        }
        save_json(
            out_dir / "run_summary.json",
            {
                "kind": "improved",
                "topic": args.topic,
                "settings": safe_settings,
                "research_revisions": research_state["revisions"],
                "code_revisions": code_state["revisions"],
                "code_verification_pass": deterministic.startswith("PASS"),
            },
        )

        print(f"\n报告已保存：{out_dir / 'report.md'}")
        print(f"审阅意见已保存：{out_dir / 'review.md'}")
        print(f"生成代码已保存：{out_dir / 'generated_code.st'}")
        print(f"校验结果已保存：{out_dir / 'verification.md'}")
        print(f"调研修订次数：{research_state['revisions']}，代码修订次数：{code_state['revisions']}")
    finally:
        await model_client.close()


if __name__ == "__main__":
    asyncio.run(main())
