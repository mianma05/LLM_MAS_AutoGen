# LLM-based Multi-Agent System for Technical Research (with PLC Code Generation & Verification)

This repository is a course project for the *Agent & MAS* course. It contains a small
LLM-based multi-agent system (MAS) built on **Microsoft AutoGen** (AgentChat API) that turns
a research topic into a cited technical report. On top of the original implementation, this
repository adds a **PLC code generation + deterministic verification loop** as an improvement.

The project uses any OpenAI-compatible chat-completion API. By default it is configured for
DeepSeek (`deepseek-chat`), but it also works with Volcano Engine Ark or OpenAI.

## The three programs

| Script | Architecture | Description |
|---|---|---|
| `basic.py` | `PlannerAgent → SearchAgent → WriterAgent` | Fixed sequential pipeline (RoundRobinGroupChat). |
| `advanced.py` | `+ ReviewerAgent` (SelectorGroupChat) | Adds a review step and one allowed revision loop. |
| `improved.py` | `advanced.py` + `CodeGeneratorAgent → CodeVerifierAgent` | **Improvement**: after the research report is approved, generates an IEC 61131-3 Structured Text (ST) code example and verifies it with a deterministic tool. |

### Original flow (basic / advanced)

```
PlannerAgent  →  SearchAgent  →  WriterAgent  →  ReviewerAgent
                                  (search_literature)   ↘ needs revision → back to WriterAgent
```

- `PlannerAgent` splits the topic into 2–3 concrete search questions.
- `SearchAgent` calls the real tool `search_literature` (course source library + Crossref).
- `WriterAgent` writes a 500–700 word cited report using `[S#]` evidence numbers.
- `ReviewerAgent` (advanced only) checks citation–evidence consistency and requests at most one revision.

### Improved flow (`improved.py`)

```
Stage 1 (research, reuse advanced.build_team):
  PlannerAgent → SearchAgent → WriterAgent → ReviewerAgent  (up to 1 revision)

Stage 2 (code generation & verification, NEW):
  CodeGeneratorAgent → CodeVerifierAgent
                          (check_st_code, a deterministic ST static checker)
                            ↳ FAIL → back to CodeGeneratorAgent (up to 1 revision)
                            ↳ PASS → stop
```

The new components are:

- **`CodeGeneratorAgent`** — generates a demo IEC 61131-3 ST program (motor start/stop with
  an emergency-stop safety interlock and a `TON` delay timer) based on the research findings.
- **`CodeVerifierAgent`** — calls the new deterministic tool `check_st_code` on the generated code.
- **`check_st_code`** (in `plc_tools.py`) — a *real* tool (no LLM): it strips comments and checks
  (1) keyword pairing balance (`IF/END_IF`, `PROGRAM/END_PROGRAM`, ...), (2) bracket balance, and
  (3) the common LLM mistake of using `=` instead of `:=` for assignment. It returns a `PASS`/`FAIL`
  report. The `FAIL` result deterministically drives the revision loop.

## Setup

Requirements: **Python 3.11+** (tested on 3.13).

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

## Configure the API

Copy `.env.example` to `.env` and fill in an OpenAI-compatible key. For DeepSeek:

```ini
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_MAX_TOKENS=1800
LLM_TEMPERATURE=0.2
```

> Use `deepseek-chat` (not `deepseek-reasoner`): the MAS relies on function calling, which the
> reasoning model does not support. The `.env` file is git-ignored — never commit real keys.

Verify the connection:

```bash
python check_api.py
```

## Run

All three programs take a `--topic` argument and write a fresh directory under `outputs/`.

```bash
# Task 1 — original MAS
python basic.py    --topic "LLM-assisted PLC code generation and industrial automation programming"
python advanced.py --topic "LLM-assisted PLC code generation and industrial automation programming"

# Task 2 — improved MAS
python improved.py --topic "LLM-assisted PLC code generation and industrial automation programming"
```

### Outputs

Every run produces (in `outputs/<kind>_<timestamp>/`):

- `report.md` — final report.
- `trace.jsonl` — full message + tool-call trace (for log interpretation).
- `evidence_registry.json` — the `[S#]` → source mapping used in this run.
- `run_summary.json` — run settings (no secrets) and revision counts.

`advanced.py` additionally writes `review.md`; `improved.py` additionally writes
`generated_code.st` (the verified ST code) and `verification.md` (the tool-check result and a
deterministic re-check).

## Screenshots

`/screenshots` contains PNG snapshots of the run logs and key output files for Task 1 and Task 2.

## Tools & data sources

`research_tools.py` provides two sources: a bundled course source library
(`data/course_sources.json`) and a live Crossref search (no extra key required).

## Notes

- Do not commit real API keys.
- The reports are LLM-generated and still need human review, especially for citations, numbers
  and causal claims.
- AutoGen and model services change over time; pin the dependency versions in `requirements.txt`.
