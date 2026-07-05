# Implementing Agentic AI in the Moodle AI Assistant

A design document for turning the original **deterministic pipeline** into a
genuinely **agentic** system.

---

## ✅ Implementation status (as built)

All five levels below are now implemented and verified end-to-end against
`data/students.csv` (194 real student records) using Groq
`llama-3.3-70b-versatile`.

| Level | Capability | Status | Where |
|---|---|---|---|
| 1 | LLM tool-calling agent loop | ✅ Done | [`agent_loop.py`](agent_loop.py), endpoint `POST /ask/agentic` |
| 2 | Multiple real, single-purpose tools (11) with per-tool RBAC | ✅ Done | [`agent_tools.py`](agent_tools.py) |
| 3 | Planner + RBAC-guard + Data/Analytics workers + Synthesizer over a shared blackboard | ✅ Done | [`multi_agent.py`](multi_agent.py), endpoint `POST /ask/multiagent` |
| 4 | Real MCP server over **stdio** + MCP client bridge | ✅ Done | [`mcp_stdio_server.py`](mcp_stdio_server.py), [`mcp_client.py`](mcp_client.py), endpoints `/mcp/v2/tools`, `/mcp/v2/call` |
| 5 | Queryable episodic **memory**, **reflection** critic, output **guardrail** | ✅ Done | [`memory_store.py`](memory_store.py), [`reflection.py`](reflection.py), [`guardrails.py`](guardrails.py) |

**Runtime blockers fixed along the way:**
- CSV path was hardcoded to a missing absolute path → now repo-relative
  (`csv_db.DEFAULT_CSV_PATH = BASE_DIR/"data"/"students.csv"`, env-overridable).
- The loader read columns (`usn`, `id`) that don't exist in the shipped CSV, and
  *derived* attendance/mentor/backlogs → now maps the real columns
  (`student_id`, `attendance_percent`, `mentor_*`, `backlog_*`, …).
- `/login` ignored the password (any value logged in as anyone) → now verified
  against a salted-hash store ([`auth_store.py`](auth_store.py)).

The rest of this document is the original design write-up; each level's sketch
below is realized by the files named above.

---

## 1. Where the project stands today (honest baseline)

The current flow in [`agentic_workflow.py`](agentic_workflow.py) is a fixed,
7-step Python function chain:

```
role-guard → context → intent → data-router → data-agent → executor / composer
```

It works well, but it is **not agentic**. The reasons:

| Property | Agentic system | This project today |
|---|---|---|
| Who decides the next step | An LLM reasons and chooses | A hard-coded `if/elif` + dict lookup (`AGENTS`) |
| Tool selection | LLM picks a tool via tool-calling | `intent_from_query()` keyword matching in `classifier.py` |
| Control flow | Loop: act → observe → decide → repeat | Single forward pass, no loop |
| Agent-to-agent link | Messages / shared state a reasoner reads | Function return values passed as args |
| Role of the LLM | The decision-maker | A text formatter on the fallback branch only |
| "MCP server" | Real JSON-RPC/stdio transport + client | In-process `call_tool()` dispatch (`mcp_csv_server.py`) |
| "9 data agents" | Distinct autonomous units | 9 labels wrapping the **same** `call_tool("retrieve_data")` |

**Goal of this doc:** describe, in terms of the existing files, exactly what to
change so the LLM does the reasoning and the system earns the word "agentic."

---

## 2. What "agentic" actually requires

A system is agentic when an LLM is in the **control loop**, not just the output
step. Minimum bar — the "agent loop":

```
┌─────────────────────────────────────────────┐
│  1. LLM receives goal + available tools      │
│  2. LLM DECIDES which tool to call (or stop) │
│  3. System EXECUTES the chosen tool          │
│  4. Result fed back to the LLM as observation│
│  5. Repeat until LLM says "I have the answer"│
└─────────────────────────────────────────────┘
```

The difference from the current code: today **Python** decides step 2. In an
agentic version, the **model** decides step 2 — and can loop.

---

## 3. Implementation roadmap (progressive levels)

Each level is independently shippable. Level 1 alone makes "agentic AI" an
honest claim. Higher levels add depth.

### Level 1 — LLM tool-calling router (the core change)

Replace the keyword classifier + dict router with real **function calling**.
Groq's `llama-3.3-70b-versatile` supports the OpenAI-style `tools` parameter, so
this is a small, realistic change.

**What changes:** `classifier.py` (keyword heuristic) and the routing block in
`agentic_workflow.py` are replaced by a model that is handed the tool schemas
you *already* defined in [`mcp_csv_server.py`](mcp_csv_server.py) and chooses
which to call.

**New file: `agent_loop.py`**

```python
"""
agent_loop.py — LLM-driven tool-calling agent.
The model chooses which CSV tool to call instead of keyword heuristics.
"""
import json
from groq import Groq
from mcp_csv_server import call_tool, list_tools

# Convert your existing MCP-style TOOLS into Groq's tool-calling schema
def _as_groq_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in list_tools()
    ]

def run_agent(client: Groq, *, user_id: str, role: str, query: str,
              trace: list, max_steps: int = 5) -> str:
    """The agent loop: model decides tools, we execute, repeat until done."""
    tools = _as_groq_tools()
    messages = [
        {"role": "system", "content": (
            f"You are Moodle AI for NMIT. The user's role is '{role}' and their "
            f"id is '{user_id}'. Use the provided tools to fetch real data from "
            f"the CSV database before answering. Respect role-based access: a "
            f"student may only see their own data. When you have enough "
            f"information, reply with the final answer and no tool call."
        )},
        {"role": "user", "content": query},
    ]

    for step in range(max_steps):
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=tools,
            tool_choice="auto",        # <-- the model decides
            temperature=0.2,
        )
        msg = resp.choices[0].message
        messages.append(msg)

        # No tool call => the model is done reasoning
        if not msg.tool_calls:
            trace.append({"agent": "reasoner", "action": "final_answer",
                          "status": "completed", "detail": f"Answered in {step+1} step(s)."})
            return msg.content or ""

        # Execute every tool the model asked for, feed results back
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            args.setdefault("user_id", user_id)
            args.setdefault("role", role)
            result = call_tool(tc.function.name, args)   # reuse existing dispatch
            trace.append({"agent": "tool-executor", "action": tc.function.name,
                          "status": "completed",
                          "detail": f"Model chose {tc.function.name}."})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

    return "I could not complete the request within the step budget."
```

**Why this is genuinely agentic:** the model — not `classifier.py` — chooses
`retrieve_data` vs `get_user_context`, fills in the arguments, reads the result,
and can call a *second* tool if the first wasn't enough. That is the agent loop.

**Wiring it in:** in [`main.py`](main.py) `/ask`, call `run_agent(...)` instead
of (or as a branch alongside) `run_agentic_workflow(...)`. Keep the RBAC role
resolution before the loop so the system prompt is already role-scoped.

---

### Level 2 — Add real multi-tool tools so the loop has decisions to make

An agent is only interesting if there is more than one meaningful tool to choose
between. Today `retrieve_data` does everything. Split the capability so the model
must actually plan:

Add these tools to [`mcp_csv_server.py`](mcp_csv_server.py)'s `TOOLS` list and
`call_tool()` dispatch:

- `find_student(name_or_usn)` → resolve an entity before fetching its data
- `get_grades(user_id)` / `get_attendance(user_id)` / `get_backlogs(user_id)`
- `get_mentor(user_id)`
- `list_course_students(course)` (faculty/admin only)
- `aggregate_stats(course, metric)` → averages/counts

Now a query like *"is my attendance low enough to affect my backlog risk?"*
forces the model to chain: `get_attendance` → `get_backlogs` → reason → answer.
**Multi-hop reasoning over tools is the clearest demonstration of agency.**

---

### Level 3 — Multiple genuinely distinct agents (planner + workers)

Level 1 is a *single* agent. To justify "**multi**-agent," give agents different
system prompts, tools, and responsibilities, coordinated by a planner.

```
                 ┌──────────────┐
  query ───────▶ │ Planner Agent│  decomposes goal into sub-tasks
                 └──────┬───────┘
          ┌────────────┼─────────────┐
          ▼            ▼             ▼
   ┌───────────┐ ┌───────────┐ ┌───────────┐
   │ Data Agent│ │Analytics  │ │  RBAC     │   each = own prompt + tools
   │(retrieval)│ │Agent      │ │  Guard    │
   └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
         └─────────────┼─────────────┘
                       ▼
               ┌───────────────┐
               │Synthesizer    │  merges results → final answer
               │Agent          │
               └───────────────┘
```

Each agent is its own `run_agent`-style loop with a **restricted toolset**:

- **Planner** — no data tools; only outputs a task list (structured JSON).
- **Data Agent** — retrieval tools only.
- **Analytics Agent** — aggregation/stat tools only.
- **RBAC Guard Agent** — validates every proposed data access against `rbac.py`
  *before* it runs (an agent enforcing policy, not a hard-coded check).
- **Synthesizer** — no tools; composes the natural-language answer.

Communication becomes real here: the planner's structured output is the message
the workers consume, and workers' results are messages the synthesizer consumes.
This is where the existing `trace` list can evolve into a genuine **shared
blackboard** that agents read *and* write.

---

### Level 4 — Make MCP a real protocol boundary

Today `mcp_csv_server.py` is "MCP-style" — called in-process. To make it a true
Model Context Protocol server:

1. Use the official `mcp` Python SDK (`pip install mcp`).
2. Expose the CSV tools over **stdio** as an MCP server process.
3. Have the FastAPI app connect as an **MCP client** and discover tools at
   runtime instead of importing `call_tool` directly.

```python
# mcp_csv_server.py (real MCP, sketch)
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("moodle-csv")

@mcp.tool()
def retrieve_data(intent: str, entity: str, role: str, user_id: str) -> dict:
    """Retrieve CSV data for a classified academic intent."""
    from data_retriever import retrieve_data as _rd
    return _rd(intent=intent, entity=entity, role=role, user_id=user_id)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Now the tool boundary is a genuine process + protocol boundary — which is what
"MCP integration" actually means, and it is defensible in a technical interview.

---

### Level 5 — Memory, reflection, and guardrails (advanced)

- **Persistent memory:** upgrade the in-memory `_chat_history` in `main.py` to a
  store the agent can *query* ("what did we discuss earlier?") — episodic memory.
- **Reflection loop:** after producing an answer, a critic agent checks it
  against the retrieved data for hallucination, and can trigger a retry. This is
  the "self-correction" pattern.
- **Guardrails:** a validation agent confirms no answer leaks another student's
  data before it is returned — RBAC enforced by reasoning, logged in the trace.

---

## 4. File-by-file upgrade map

| Current file | Today's role | Agentic upgrade |
|---|---|---|
| `classifier.py` | Keyword intent heuristic | **Remove/demote** — the LLM router replaces it (Level 1) |
| `agentic_workflow.py` | Fixed 7-step chain | Becomes the **planner/orchestrator** invoking agent loops (Level 3) |
| `data_agents.py` | 9 labels on one call | Split into real tools with distinct schemas (Level 2) |
| `mcp_csv_server.py` | In-process dispatch | Real MCP server over stdio (Level 4) |
| `intent_agent.py` | Classifier + enrich | Folded into the reasoner's system prompt |
| `main.py` | FastAPI routes | Calls `run_agent`; adds queryable memory (Level 5) |
| `rbac.py` | Role detection | Powers the RBAC Guard agent (Level 3) |
| `trace` list | Write-only log | Evolves into a read/write **blackboard** (Level 3) |

---

## 5. What to KEEP (already good)

Do not throw these away — they are the parts a strong system needs and they stay
valid in the agentic version:

- **RBAC / role scoping** (`rbac.py`) — agents must respect it; wrap it, don't drop it.
- **The `trace`** — it becomes your explainability + observability layer.
- **`response_formatter.py`** — multi-format export (pdf/excel/word) is orthogonal
  and still useful.
- **The CSV data layer** (`data_retriever.py`, `csv_db.py`) — becomes the tool
  implementations behind MCP.

---

## 6. Suggested build order

1. **Level 1** — `agent_loop.py` with tool-calling. *(Half a day. This alone makes
   the "agentic AI" claim true and defensible.)*
2. **Level 2** — split `retrieve_data` into several real tools so the loop plans.
3. **Level 4** — real MCP transport (independent, high interview value).
4. **Level 3** — planner + worker agents + blackboard.
5. **Level 5** — memory, reflection, guardrails.

---

## 7. One-paragraph honest summary (for a report or viva)

> The system was refactored from a deterministic intent-routing pipeline into an
> agentic architecture. An LLM reasoning loop (Groq `llama-3.3-70b-versatile`)
> selects and invokes tools via function-calling instead of keyword heuristics,
> enabling multi-hop retrieval. Tools are exposed through a Model Context
> Protocol server over stdio. A planner agent decomposes queries and delegates to
> specialised worker agents (retrieval, analytics, RBAC guard, synthesis) that
> communicate through a shared blackboard, with role-based access control
> enforced at the agent level and a full execution trace for explainability.

Every clause of that paragraph maps to a level above — and, as of this build,
every clause is **implemented and verified**, not aspirational:

- *"LLM reasoning loop selects and invokes tools via function-calling"* →
  [`agent_loop.py`](agent_loop.py) (`tool_choice="auto"`, step budget, trace).
- *"multi-hop retrieval"* → verified: a compound query chains `get_grades` +
  `get_class_average` in one run.
- *"Tools exposed through a Model Context Protocol server over stdio"* →
  [`mcp_stdio_server.py`](mcp_stdio_server.py) via the official `mcp` SDK; the
  app consumes them as a client in [`mcp_client.py`](mcp_client.py).
- *"planner decomposes and delegates to specialised workers … shared
  blackboard"* → [`multi_agent.py`](multi_agent.py) (`Blackboard`, planner,
  RBAC-guard, Data/Analytics workers, synthesizer).
- *"RBAC enforced at the agent level"* → the RBAC-guard drops out-of-scope
  subtasks *and* [`agent_tools.authorize()`](agent_tools.py) gates every tool.
- *"full execution trace for explainability"* → returned by every agentic
  endpoint and rendered in the UI's 🧠 Agentic trace panel.
- Plus Level 5: episodic memory, a reflection critic, and a PII-leak guardrail.
