# Moodle AI Assistant — Architecture & Dry-Run Walkthrough

This document explains **how the repo works now**, then dry-runs **4 concrete
examples** end-to-end so you can trace exactly what each component does. All
numbers below are from real runs against `data/students.csv` (194 students,
Groq `llama-3.3-70b-versatile`).

---

## Part 1 — The big picture

### 1.1 Components (who does what)

```
                                   ┌───────────────────────────────┐
  Browser (static/index.html)      │           FastAPI (main.py)   │
  ────────────────────────────▶    │  /login  /ask  /ask/agentic   │
   user_id + query + 🧠 toggle      │  /ask/multiagent  /mcp/v2/*   │
                                    └───────────────┬───────────────┘
                                                    │ routes to one of 3 paths
        ┌───────────────────────────────────────────┼───────────────────────────────┐
        ▼                                            ▼                                ▼
  LEGACY PATH                              SINGLE-AGENT PATH                 MULTI-AGENT PATH
  agentic_workflow.py                      agent_loop.py                     multi_agent.py
  (deterministic 7-step,                   (LLM tool-calling loop)           (planner→workers→
   keyword routing)                                │                          synthesizer)
        │                                          ▼                                │
        │                                   agent_tools.py  ◀──────────────────────┘
        │                                   11 tools + authorize() RBAC gate
        └───────────────────────┬──────────────────┬───────────────────────────────┘
                                 ▼                  ▼
                           csv_db.py          rbac.py               Cross-cutting layers:
                     data/students.csv    role + identity            memory_store.py  (episodic memory)
                                                                     reflection.py    (self-correction)
                                                                     guardrails.py    (PII leak guard)
                                                                     mcp_stdio_server.py + mcp_client.py
                                                                       (real MCP over stdio)
                                                                     auth_store.py    (password verify)
```

### 1.2 The three request paths

| Endpoint | Path | Who decides the tools | Used for |
|---|---|---|---|
| `POST /ask` | **Legacy** deterministic pipeline | Python keyword rules (`classifier.py`) | file export (PDF/Excel/Word), backward-compat |
| `POST /ask/agentic` | **Single agent** | The LLM (one tool-calling loop) | simple/direct questions |
| `POST /ask/multiagent` | **Multi-agent** | Planner LLM + workers | compound questions, full trace, safety demo |

The browser's **🧠 Agentic** toggle (on by default) sends text queries to
`/ask/multiagent`; turning it off uses the legacy `/ask`. File formats always
use `/ask`.

### 1.3 The capability layer (`agent_tools.py`) — 11 tools

Every tool is small, single-purpose, and carries an **RBAC scope**. The LLM may
*request* any tool; `authorize()` decides if it may *execute* for this user.

| Scope | Tools | Who can run them |
|---|---|---|
| `individual` | `get_profile`, `get_attendance`, `get_grades`, `get_backlogs`, `get_mentor`, `get_courses`, `get_contact` | student → **only their own** record; faculty → their mentees; admin → anyone |
| `aggregate` | `get_student_count`, `get_class_average`, `recall_memory` | any recognized role (anonymized) |
| `directory` | `find_student_by_name`, `list_my_students` | faculty / admin only |

**Key rule:** for a student, `authorize()` *rewrites* the `user_id` argument to
their own USN — a student literally cannot address another student's record.

---

## Part 2 — The multi-agent pipeline (the interesting path)

`POST /ask/multiagent` runs six stages over a shared **Blackboard**
(`multi_agent.py`). Every stage writes to a `trace` list = the explainability
layer returned to the UI.

```
 1. RBAC-Guard: resolve_identity ──▶ role (student/faculty/admin/unknown)
 2. Planner (LLM, no tools) ───────▶ decompose query into 1–3 subtasks,
                                      each tagged [data] or [analytics]
 3. RBAC-Guard (policy) ───────────▶ drop subtasks out of the role's scope
                                      (e.g. a student may not browse directory)
 4. Workers (LLM + restricted tools):
       • Data worker      → individual-scope tools only
       • Analytics worker → aggregate + directory tools only
       …each is its own agent_loop run; results land on the blackboard
 5. Synthesizer (LLM, no tools) ───▶ merge worker findings into one answer
 6. Reflection critic (LLM) ───────▶ fact-check answer vs the tool observations,
                                      correct unsupported claims
 7. Output guardrail (code) ───────▶ redact any USN/email/phone the user
                                      isn't allowed to see
                         ──▶ record turn in episodic memory ──▶ return
```

**Why this is genuinely multi-agent:** the planner's structured output is the
*message* the workers consume; the workers' findings are the *message* the
synthesizer consumes; each agent has a **different prompt, toolset, and job**.

---

## Part 3 — Dry-run examples

Reference data used below (real rows from `data/students.csv`):

| USN | Name | Attendance | CGPA | Backlogs | Mentor (id) |
|---|---|---|---|---|---|
| `1NT23IS015` | Ahmed Syed Mohammad | 94.0% | 8.5 | 0 | Dr. Asha Rao (FAC001) |
| `1NT23IS125` | Mohammed Athar | 94.0% | 8.29 | 0 | Prof. Vivek Shenoy (FAC002) |
| Class-wide | — | — | avg **7.81** | — | — |
| `FAC001` | Dr. Asha Rao | — | — | — | mentors **111** students |

---

### Example 1 — Student, simple self-query (single-agent path)

**Login:** `1NT23IS015` / `moodle@123`  →  `POST /ask/agentic`
**Query:** *"What is my attendance percentage and who is my mentor?"*

```
STEP  ACTOR         DECISION
────  ───────────   ─────────────────────────────────────────────────────
 0    rbac-guard    resolve_identity("1NT23IS015") → role=student
 1    LLM           tool_choice=auto → calls get_attendance {}      (no user_id)
 2    authorize()   student self-access → inject user_id=1NT23IS015 → RUN
      → observation {attendance_percent: 94.0, present: 75, absent: 5}
 3    LLM           reads result → calls get_mentor {}
 4    authorize()   self-access → RUN
      → observation {mentor: "Dr. Asha Rao", email, phone}
 5    LLM           no more tool calls → FINAL ANSWER
────────────────────────────────────────────────────────────────────────
ANSWER: "Your attendance percentage is 94.0% and your mentor is Dr. Asha Rao."
tool_calls: [get_attendance, get_mentor]
```

**What it demonstrates:** the *model* chooses the tools (not Python), fills the
args, reads results, and stops when done. The student never typed a USN — RBAC
defaulted every tool to their own record.

---

### Example 2 — Student, compound multi-hop query (multi-agent path)

**Login:** `1NT23IS015`  →  `POST /ask/multiagent`
**Query:** *"How many backlogs do I have and what is my CGPA compared to the
class average?"*

```
STAGE          AGENT            OUTPUT
─────────────  ───────────────  ────────────────────────────────────────────
1 resolve      rbac-guard       role=student
2 PLAN         planner          subtasks:
                                   [data]      "get backlogs and CGPA for the student"
                                   [analytics] "get class average CGPA"
3 GUARD        rbac-guard       both scopes OK for student → authorize both
4 WORK         data-worker      calls get_backlogs {} → {backlog_count: 0}
                                calls get_grades   {} → {cgpa: 8.5}
               analytics-worker calls get_class_average {metric: cgpa}
                                   → {average: 7.81, students_counted: 194}
5 SYNTH        synthesizer      merges both workers' findings
6 REFLECT      critic           checks answer vs observations → supported=True
7 GUARDRAIL    guardrail        scans for leaked PII → safe (only own data)
─────────────────────────────────────────────────────────────────────────────
ANSWER: "You have 0 backlogs. Your CGPA is 8.5, which is higher than the
         class average CGPA of 7.81."
plan: [data] + [analytics]   tools: [get_backlogs, get_grades, get_class_average]
reflection: supported ✓      guardrail: clean ✓      trace: 13 steps
```

**What it demonstrates:** one question requires **two different workers** (a
personal fact + an anonymized aggregate). The planner splits it, workers run in
their own scoped loops, the synthesizer composes, and the critic + guardrail
sign it off. This is multi-hop reasoning across agents.

---

### Example 3 — Faculty, aggregate over their cohort (single-agent path)

**Login:** `FAC001`  →  `POST /ask/agentic`
**Query:** *"How many students do I mentor and what is their average
attendance?"*

```
STEP  ACTOR         DECISION
────  ───────────   ─────────────────────────────────────────────────────
 0    rbac-guard    resolve_identity("FAC001") → role=faculty
 1    LLM           calls list_my_students {}
 2    authorize()   directory scope + faculty → RUN
      → observation {count: 111, students: [...first 30...]}
 3    LLM           calls get_class_average {metric: attendance}
 4    authorize()   aggregate → RUN → {average: 96.0}
 5    LLM           FINAL ANSWER
────────────────────────────────────────────────────────────────────────
ANSWER: "You mentor 111 students and their average attendance is 96.0%."
tool_calls: [list_my_students, get_class_average]
```

**What it demonstrates:** the **same tools behave differently by role**.
`list_my_students` returns *the faculty's own mentees* (111, matched by real
`mentor_id = FAC001` in the CSV), where a student would have been denied the
directory scope entirely.

---

### Example 4 — Student overreach → BLOCKED (security, defense-in-depth)

**Login:** `1NT23IS015`  →  `POST /ask/multiagent`
**Query:** *"What is the CGPA and phone number of student 1NT23IS125?"*
(A student trying to read **another** student's private data.)

```
STAGE          AGENT            OUTPUT
─────────────  ───────────────  ────────────────────────────────────────────
1 resolve      rbac-guard       role=student
2 PLAN         planner          [data] "get CGPA and phone of 1NT23IS125"
3 GUARD        rbac-guard       individual scope → allowed to attempt
4 WORK         data-worker      calls get_grades {user_id: 1NT23IS125}
                 authorize()      student may only access OWN record → DENIED ⛔
               data-worker      calls get_contact {user_id: 1NT23IS125}
                 authorize()      DENIED ⛔
5 SYNTH        synthesizer      composes from denials
6 GUARDRAIL    guardrail        finds "1NT23IS125" in the draft → REDACT ✏️
─────────────────────────────────────────────────────────────────────────────
ANSWER: "I'm unable to provide the CGPA and phone number of student
         [redacted-USN] … access to the specific student's records is restricted."
guardrail: {safe: false, violations: ["USN:1NT23IS125"]}
LEAK CHECK: other student's real CGPA "8.29" present? → NO
            other student's phone present? → NO
```

**What it demonstrates: three independent layers of protection.**
1. `authorize()` refuses the tool calls (a student can't address another USN).
2. Even if the model paraphrased something, the **output guardrail** redacts any
   identifier outside the user's allowed set.
3. The trace shows every denial — security is *explainable*, not silent.

---

### (Bonus) Example 5 — Memory across turns

Because `/ask/agentic` and `/ask/multiagent` write each turn to
`memory_store.py`, and the loop is seeded with recent turns + a `recall_memory`
tool:

```
Turn 1  student: "what is my attendance"        → "94%"        (stored)
Turn 2  student: "and remind me what we just discussed?"
        → agent calls recall_memory {query: "attendance"} → returns Turn 1
        → "Earlier you asked about your attendance; it was 94%."
```

`GET /memory/1NT23IS015` returns the persisted turns; `POST /chat/clear` wipes
both the in-memory and persisted stores.

---

## Part 4 — What enforces safety (summary)

| Layer | File | Guarantees |
|---|---|---|
| Identity | `rbac.py` | ID → role (ADM*/FAC* prefix, or CSV lookup for students) |
| Auth | `auth_store.py` | `/login` verifies password (salted PBKDF2); wrong → 401 |
| RBAC gate | `agent_tools.authorize()` | rewrites/blocks tool args **before** execution |
| Plan guard | `multi_agent._guard()` | drops out-of-scope subtasks before workers run |
| Reflection | `reflection.py` | corrects claims not supported by tool data |
| Output guard | `guardrails.py` | redacts USN/email/phone outside the user's scope |

Security is **defense in depth**: a request must pass identity → auth → plan
guard → per-tool RBAC → output guard. Any single layer failing does not leak
data.

---

## Part 5 — Endpoint & file reference

```
POST /login            → verify user_id + password (auth_store)
POST /ask              → legacy deterministic pipeline + file export
POST /ask/agentic      → single LLM tool-calling loop        (agent_loop.py)
POST /ask/multiagent   → planner→workers→synth pipeline       (multi_agent.py)
GET  /memory/{user_id} → inspect persisted episodic memory    (memory_store.py)
GET  /mcp/v2/tools     → discover tools over real MCP stdio    (mcp_client.py)
POST /mcp/v2/call      → call a tool over real MCP stdio       (mcp_stdio_server.py)
GET  /user-context/{id}→ role-scoped dashboard context
POST /mentor/assign    → faculty/admin assign a mentor
POST /chat/clear       → wipe conversation + episodic memory
```

**Data source:** `data/students.csv` (194 rows), resolved by
`csv_db.CSV_PATH` (repo-relative default, override with `STUDENT_DB_CSV_PATH`).
