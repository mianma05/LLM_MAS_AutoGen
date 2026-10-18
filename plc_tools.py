"""改进版新增的确定性工具：IEC 61131-3 结构化文本(ST)代码静态检查器。

这是一个“真实工具”：不调用任何 LLM，只用正则与计数对代码做可核查的检查，
返回以 PASS 或 FAIL 开头的结构化报告，供 CodeVerifierAgent 据此判断。
"""

from __future__ import annotations

import re

# IEC 61131-3 ST 中成对出现的关键字
_OPEN_TO_CLOSE = {
    "IF": "END_IF",
    "CASE": "END_CASE",
    "FOR": "END_FOR",
    "WHILE": "END_WHILE",
    "REPEAT": "END_REPEAT",
    "PROGRAM": "END_PROGRAM",
    "FUNCTION_BLOCK": "END_FUNCTION_BLOCK",
    "FUNCTION": "END_FUNCTION",
}


def _blank_non_newlines(match: re.Match) -> str:
    """把匹配到的内容里除换行外的字符替换成空格，以保留行号。"""
    return re.sub(r"[^\n]", " ", match.group(0))


def _strip_comments(code: str) -> str:
    """去掉块注释 (* ... *) 与行注释 //，保留换行，行号不变。"""
    code = re.sub(r"\(\*.*?\*\)", _blank_non_newlines, code, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", _blank_non_newlines, code)
    return code


def check_st_code(code: str) -> str:
    """对 ST 代码做确定性静态检查，返回以 PASS/FAIL 开头的报告。

    检查项：
    1. 关键字配对是否平衡（IF/END_IF、PROGRAM/END_PROGRAM 等）；
    2. 圆括号、方括号是否平衡；
    3. 是否把比较用的 '=' 误写成赋值（ST 的赋值是 ':='）——这是 LLM 生成
       ST 代码最常见的错误之一。
    统计前会先剥离注释，避免把注释里的文字误判为关键字或运算符。
    """

    if not code or not code.strip():
        return "FAIL\n- 代码为空。请先提供完整的 ST 代码。"

    clean = _strip_comments(code)
    issues: list[str] = []

    # 0) 基础结构：必须包含 PROGRAM 或 FUNCTION_BLOCK 等声明，否则可能不是有效 ST 代码
    if not re.search(r"\b(PROGRAM|FUNCTION_BLOCK|FUNCTION)\b", clean):
        return (
            "FAIL\n- 未检测到 PROGRAM/END_PROGRAM（或 FUNCTION_BLOCK/FUNCTION）声明，"
            "输入可能不是有效的 ST 代码。"
        )

    # 1) 关键字配对平衡
    for open_kw, close_kw in _OPEN_TO_CLOSE.items():
        n_open = len(re.findall(rf"\b{open_kw}\b", clean))
        n_close = len(re.findall(rf"\b{close_kw}\b", clean))
        if n_open != n_close:
            issues.append(
                f"关键字配对不平衡：{open_kw} 出现 {n_open} 次，"
                f"{close_kw} 出现 {n_close} 次。"
            )

    # 2) 括号配对
    for open_ch, close_ch in (("(", ")"), ("[", "]")):
        if clean.count(open_ch) != clean.count(close_ch):
            issues.append(
                f"括号不平衡：'{open_ch}' {clean.count(open_ch)} 个，"
                f"'{close_ch}' {clean.count(close_ch)} 个。"
            )

    # 3) 赋值运算符：检测把 '=' 误写成 ':=' 的常见错误
    for lineno, line in enumerate(clean.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        # 条件行（IF/ELSIF/WHILE/UNTIL/FOR）里的 '=' 是合法比较，跳过
        if re.search(r"\b(IF|ELSIF|WHILE|UNTIL|FOR)\b", s, re.IGNORECASE):
            continue
        # 找既不是 := 也不是 == >= <= <> => 的孤立 '='
        bad = re.findall(r"(?<![:\<>=!])=(?!=|>)", s)
        if bad and ":=" not in s:
            issues.append(f"第 {lineno} 行疑似把 '=' 用作赋值（ST 应使用 ':='）：{s[:80]}")

    if issues:
        return "FAIL\n" + "\n".join(f"- {item}" for item in issues)
    return "PASS\n- 未发现明显问题：关键字配对、括号、赋值运算符均正常。"
