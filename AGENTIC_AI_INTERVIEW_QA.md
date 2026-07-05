# Interview Q&A — Agentic AI Implementation

Prep for the resume line:
> *Agentic AI Assistant for Organizational Knowledge Retrieval — Python, FastAPI,
> Groq (LLaMA 3.3), Pandas, n8n, Docker. Multi-agent orchestration with RBAC and
> hybrid execution; conditional query routing.*

This file covers the **agentic version** (the LLM-in-the-loop design from
`AGENTIC_AI_IMPLEMENTATION.md`). Your `notes.txt` already covers the current
deterministic pipeline. Read both.

---

## ⚠️ READ THIS FIRST — the one rule that keeps you safe

An interviewer's sharpest question will be: **"Is this actually agentic, or just a
pipeline?"** There is exactly one correct strategy: **tell the truth about which
version you're describing, and never claim a loop you didn't build.**

- If you have implemented **Level 1** (the tool-calling agent loop, `agent_loop.py`):
  every answer below is true — speak confidently.
- If you have **not** built it yet: say so — *"the current system is a deterministic
  multi-agent pipeline; the agentic tool-calling loop is the version I designed and
  am implementing."* That sentence is honest, senior-sounding, and un-trippable.

Bluffing a loop you can't show on screen is how candidates get rejected. Being able
to say *"here's the pipeline today, here's exactly how I make it agentic, and here's
why"* is how you get hired. Depth beats a buzzword.

---

## A. THE CORE CONCEPT (they will start here)

**Q1. What makes a system "agentic" rather than a normal pipeline?**
In a pipeline, *my code* decides what happens next (an `if/elif`, a keyword match).
In an agentic system, *the LLM* decides — it picks which tool to call, reads the
result, and decides whether to call another tool or answer. The intelligence lives
in a loop (decide → act → observe → repeat), not in hard-coded branches. The
concrete change in my project: I replace the keyword router in `classifier.py` with
LLM **function-calling**, so the model chooses the tool.

**Q2. Explain the agent loop in one breath.**
The model is given the goal plus a menu of tools. It responds with a *tool call*
instead of an answer; my code executes that tool and feeds the result back as an
observation; the model is called again with that new context; this repeats until the
model returns text with no tool call — that's the final answer.

**Q3. Where exactly does the LLM make the decision?**
In the Groq chat call I pass `tools=[...]` and `tool_choice="auto"`. The response
comes back with a `tool_calls` array — that array *is* the decision. My loop never
contains the words "attendance" or "mentor"; the model maps the natural-language
query to the right tool and fills in the arguments itself.

**Q4. So what's left for your code to do?**
Three things: (1) execute whatever tool the model names, reusing my existing
`call_tool()` dispatch; (2) enforce security *before* executing (the RBAC gate);
(3) loop, with a step budget to guarantee termination. The code is the "hands," the
model is the "brain."

---

## B. TOOL CALLING / FUNCTION CALLING (the mechanism)

**Q5. How does the LLM actually "call" a tool? It can't run code.**
It can't — it emits structured JSON. I hand it JSON-Schema tool definitions (built
from my existing `mcp_csv_server.py` schemas). The model replies with the tool name
and a JSON arguments object. *My* Python parses that and runs the real function. The
model requests; the runtime executes. That's the whole contract.

**Q6. Show the shape of a tool definition.**
```json
{ "type": "function", "function": {
    "name": "get_attendance",
    "description": "Get attendance % for a student by user_id",
    "parameters": { "type": "object",
      "properties": { "user_id": { "type": "string" } },
      "required": ["user_id"] } } }
```
The `description` is what the model reads to decide *when* to use it — so writing good
descriptions is real prompt engineering, not an afterthought.

**Q7. Does Groq / LLaMA 3.3 support this?**
Yes — `llama-3.3-70b-versatile` supports OpenAI-style tool calling via the `tools`
parameter. That's why the change is small: it's the same Groq client I already use,
plus a `tools` argument and a loop.

**Q8. What if the model hallucinates an argument or picks the wrong tool?**
Three guards: (1) JSON-Schema validation rejects malformed arguments before they run;
(2) the tool itself validates (e.g. "student not found" comes back as an observation
the model can react to); (3) the step budget caps retries. Crucially, a wrong tool
just returns data the model then judges — it can self-correct on the next loop, which
a pipeline can't.

---

## C. MULTI-AGENT ORCHESTRATION (the "5 agents")

**Q9. Your resume says 5 agents — name them and their jobs.**
In the agentic design the five are:
1. **Planner** — decomposes the goal into sub-tasks; no data tools, outputs a task list.
2. **Data Agent** — retrieval tools only (grades, attendance, mentor, backlogs).
3. **Analytics Agent** — aggregation/stat tools (averages, counts, comparisons).
4. **RBAC Guard Agent** — validates every proposed data access against `rbac.py` before it runs.
5. **Synthesizer** — no tools; composes the final natural-language answer.
Each is an LLM loop with a *restricted* toolset and its own system prompt.

**Q10. How do the agents communicate?**
Through structured messages on a shared **blackboard** (an evolution of my `trace`
list). The planner writes a task list; workers read their task, do it, and write
results back; the synthesizer reads all results and writes the answer. They don't
"chat" freely — communication is structured hand-offs, which keeps it debuggable.

**Q11. Why split into multiple agents instead of one big agent with all tools?**
Three reasons: (1) **least privilege** — the Analytics agent physically can't touch
raw student rows, which supports RBAC; (2) **focus** — a smaller toolset per agent
means fewer wrong-tool mistakes; (3) **separation of concerns** — I can test, swap, or
re-prompt one agent without touching the others.

**Q12. Isn't a single tool-calling agent (Level 1) enough? Why the planner?**
For simple queries, yes — one agent loop handles "who's my mentor." The planner earns
its place on **compound** queries like *"compare my attendance to the class average
and flag if I'm below the backlog-risk line"* — that needs a plan across several
tools and two data scopes. The planner makes multi-step intent explicit and ordered.

---

## D. HYBRID EXECUTION (your strongest, most real claim)

**Q13. What is "hybrid execution" in the agentic version?**
Two dimensions of hybrid. (1) **Rule vs LLM**: known, well-formed intents can still be
answered by deterministic templates (fast, exact, zero hallucination), while open
ones go through the agent loop. (2) **Deterministic guardrails around an LLM brain**:
routing *inside* the loop is the model's, but security, schema validation, and the
step budget are hard code. So the LLM has freedom to reason but not to bypass policy.

**Q14. How do you decide template vs agent loop?**
A fast pre-check: if the query maps cleanly to a single known intent with an exact
template (e.g. `student_count`), answer deterministically — no tokens, no latency.
Otherwise hand it to the agent loop. It's a cost/accuracy optimization, not a
limitation.

**Q15. Trade-off of the hybrid approach?**
Benefit: cheap and exact on the common path, flexible on the long tail. Cost: two code
paths to maintain and a routing decision that can itself be wrong. I accept that
because the deterministic path removes hallucination on exactly the queries where a
wrong number would be most damaging.

---

## E. CONDITIONAL QUERY ROUTING

**Q16. Explain the conditional routing.**
Every query is first split into **general** (conceptual — "explain neural networks")
vs **organizational** (needs our data — "my attendance"). General goes straight to the
LLM; organizational enters the agentic data path. In the pipeline this split is
keyword-based (`classifier.py`); in the agentic version the model itself can make it,
because "no data tool is relevant" is a decision the model can reach.

**Q17. Give a query that's hard to route and how you handle it.**
*"What is my attendance"* vs *"what is attendance"* — one is personal data, one is a
definition. Keywords struggle; the agent version handles it because the model sees the
possessive "my" + the `get_attendance` tool and calls it, whereas the bare definition
gets a direct answer with no tool call.

---

## F. RBAC / SECURITY IN THE LOOP (expect heavy probing)

**Q18. How does RBAC work when the LLM is choosing tools — isn't that dangerous?**
That's exactly why security is **never** delegated to the model. The model may *ask*
for any tool, but every tool call passes through a hard `check_permission()` gate
(`rbac.py`) before it runs. If a student's loop tries `get_grades(user_id=someone_else)`,
the gate blocks it and feeds back an `ACCESS_DENIED` observation. The model can
apologize; it cannot access the data. Reasoning is the model's; authorization is code's.

**Q19. Walk me through a student trying to read another student's marks.**
Loop 1: model calls `get_grades(user_id="1NT21CS099")`. Before executing, the RBAC
guard checks the requester's role/id → denied. I return `{"error":"ACCESS_DENIED"}` as
the tool result. Loop 2: model reads that and responds *"I can only show your own
records."* No data leaked, and it's logged in the trace.

**Q20. Honest gap in the current code?**
In today's pipeline, `check_permission()` is defined in `rbac.py` but not enforced on
the `/ask` path — access is implicit via which data function runs, and `/login`
doesn't verify the password. The agentic redesign *fixes* this by making the RBAC gate
a mandatory step in the loop. I'd describe today's state as "role-scoped" and the
agentic version as "enforced RBAC."

---

## G. MCP (Model Context Protocol)

**Q21. What's the MCP piece and is it a real MCP server?**
Be precise. Today `mcp_csv_server.py` is **MCP-style** — named, schema-described tools
(`retrieve_data`, `get_user_context`) with an in-process `call_tool()` dispatch,
inspectable via `/mcp/tools` and `/mcp/call`. It is *not* yet a real protocol server.
The upgrade is to run it as an actual MCP server over stdio using the `mcp` SDK and
have FastAPI connect as an MCP client — then the tool boundary is a genuine
process/protocol boundary.

**Q22. Why does MCP matter for an agentic system?**
It standardizes "what capabilities exist" separately from "who uses them." The same
tool server can be consumed by my agent loop, by Claude Desktop, or by any MCP client —
and the model discovers tools at runtime instead of me hard-wiring them. It's the clean
seam between reasoning and capability.

---

## H. GROQ / LLaMA / LLM DETAILS

**Q23. Why Groq and why LLaMA 3.3 70B?**
Groq gives very low-latency inference and a free tier, which matters because the agent
loop makes *multiple* LLM calls per query — latency compounds. LLaMA 3.3 70B is a
strong open model that reliably does tool calling. Model id: `llama-3.3-70b-versatile`.

**Q24. The loop makes several LLM calls — isn't that slow/expensive?**
Yes, that's the real cost of agency, and I manage it: (1) the deterministic path skips
the LLM entirely for common queries; (2) a step budget caps calls per query; (3) Groq's
speed keeps multi-step latency acceptable; (4) I can cache tool results within a loop.

**Q25. How do you stop the LLM from inventing student data?**
Grounding: the model doesn't get the database, only tools. Every fact in the answer
must come from a tool result I fed back. The synthesizer is instructed to answer only
from retrieved observations. Combined with the deterministic path for exact numbers,
that's retrieval-grounded generation, not free generation.

**Q26. Temperature?**
Low (~0.2–0.3) for factual consistency — it's an information assistant, not a creative
one. Lower temperature also makes tool selection more deterministic.

---

## I. FAILURE MODES & ROBUSTNESS (senior-level questions)

**Q27. What stops an infinite loop?**
A hard `max_steps` budget (e.g. 5). If the model keeps calling tools without
converging, the loop exits with a graceful "couldn't complete within step budget"
message. Termination is guaranteed by code, not by hoping the model stops.

**Q28. What if a tool throws or the CSV is missing a field?**
The error is caught and returned to the model as an observation (`{"error": "..."}`),
so the model can try an alternative or explain the gap — the loop degrades gracefully
instead of 500-ing. FastAPI still maps unrecoverable errors to proper HTTP codes.

**Q29. How would you test an agentic system that's non-deterministic?**
Test the deterministic parts directly (RBAC gate, tool functions, schema validation).
For the loop, use low temperature + recorded fixtures, assert on *which tools get
called* and *that no unauthorized tool executes*, and add guardrail tests (a student
loop can never produce another student's data regardless of model output).

**Q30. How do you observe/debug it in production?**
The `trace`/blackboard logs every decision: which tool, which arguments, which result,
how many loops. That's my explainability layer — I can replay exactly why the system
answered as it did, which a single mega-prompt can't offer.

---

## J. CURVEBALLS & HONESTY (rehearse these — do not bluff)

**Q31. Show me the agent actually choosing a tool at runtime.**
If built: run it and point at the `tool_calls` in the response / the trace. If not
built: *"The current deployed version is the deterministic pipeline; the tool-calling
loop is implemented in `agent_loop.py`/on a branch"* — and describe it. Never claim a
live loop you can't show.

**Q32. Where's the n8n workflow?**
(Honest) n8n is **not** wired into the repo. In the agentic design its natural role is
the *external* automation layer — e.g. an n8n flow on a schedule that syncs the CSV
nightly, triggers the `/ask` pipeline on a webhook, or sends alerts when a student
crosses a backlog-risk threshold. If pressed for a live flow, say it's planned
automation, or build one small flow — don't pretend it exists.

**Q33. Where do you use Pandas?**
(Honest) It's a dependency; the live CSV path uses Python's stdlib `csv` module for a
small dataset. Say "in the data-processing/legacy path," or migrate `csv_db.py` to
pandas so the claim is current. Don't overstate it.

**Q34. Is the student data real?**
(Honest) The CSV has real-shaped fields (name, USN, CGPA, email), but mentor,
attendance %, backlogs and course lists are **derived by rules** in `csv_db.py`, not
measured. Be upfront that those are synthetic/derived.

**Q35. Isn't calling this "agentic" overselling a keyword router?**
(The key honest answer) *"The version in my report is a deterministic multi-agent
pipeline. I then designed and implemented the agentic upgrade — LLM tool-calling in a
loop with an RBAC gate — because I wanted to understand the real difference. The pipeline
taught me orchestration and grounding; the agentic version taught me tool-calling,
loop control, and why you never let the model make the security decision."* That answer
turns the weakness into the strongest thing you can say in the room.

**Q36. If you had more time, what would you build next?**
Enforce `check_permission()` on every tool call, real auth (JWT + hashed passwords),
a reflection/critic agent that checks answers against retrieved data before returning,
persistent memory (Redis) the agent can query, real MCP over stdio, and a proper test
suite for the guardrails.

---

## One-line map: resume claim → what's true

| Resume phrase | Honest status | Say this |
|---|---|---|
| "Agentic AI / multi-agent" | Pipeline today; loop designed (Level 1) | "Deterministic pipeline built; agentic loop designed & implemented on top" |
| "5-agent orchestration" | Real as labeled stages / planned as loops | Name the 5, explain the flow, don't oversell autonomy |
| "RBAC" | Role-scoping real; hard gate not enforced yet | "Role-scoped now; enforced RBAC in the agentic redesign" |
| "Hybrid execution" | **Fully real** | Template path vs LLM path — lead with this |
| "Conditional routing" | **Fully real** | general vs organizational split |
| FastAPI / Groq / LLaMA / Docker | **Fully real** | Speak freely |
| Pandas / n8n | Weak / absent | Soften or build — never bluff |

**Golden rule:** know it cold, name the file, admit the gaps. An interviewer forgives
"here's what I'd improve"; they never forgive a bluff they catch.
