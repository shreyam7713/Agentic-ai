# Moodle AI Assistant — Agentic CSV Edition

An academic assistant with a genuinely **agentic** backend: an LLM reasons in a
tool-calling loop over a real student database (`data/students.csv`, 194
records), coordinated by a planner + specialised worker agents, with role-based
access control, a reflection critic, an output guardrail, and a real MCP server.

## Prerequisites
- Python 3.10+
- Git

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. CSV database

The app reads `data/students.csv` **by default** (repo-relative — works out of
the box, no absolute paths). Override only if you want a different file:

```bash
export STUDENT_DB_CSV_PATH=/absolute/path/to/your_students.csv
```

## 3. Create your .env file

```
GROQ_API_KEY=your_groq_api_key_here
# optional — shared login password for the demo (default: moodle@123)
MOODLE_DEFAULT_PASSWORD=moodle@123
```

Get a free Groq API key from https://console.groq.com/keys

## 4. Run the server

```bash
python -m uvicorn main:app --reload
```

Open http://localhost:8000 in your browser.

## Logging in

Login now **verifies the password** (it used to be ignored). Use the shared demo
password `moodle@123` (or whatever you set in `MOODLE_DEFAULT_PASSWORD`); set
per-user passwords via `auth_store.set_password(user_id, password)`.

Sample user IDs (roles are derived from the ID):
- `1NT23IS015` — a real **student** from `data/students.csv`
- `FAC001` — **faculty** mode (mentors 100+ students in the sample data)
- `ADM001` — **admin** mode

Sample queries:
- "show me my attendance"  ·  "who is my mentor"  ·  "what are my backlogs"
- "what is my CGPA and how does it compare to the class average?"  *(multi-hop)*
- "how many students are there"

Toggle **🧠 Agentic** in the composer to run the full planner→workers→
synthesizer pipeline and see the live agent trace.

## Agentic architecture (5 levels — see AGENTIC_AI_IMPLEMENTATION.md)

| Level | What | Files / endpoint |
|---|---|---|
| 1 | LLM tool-calling agent loop | `agent_loop.py` · `POST /ask/agentic` |
| 2 | 11 single-purpose tools, each RBAC-scoped | `agent_tools.py` |
| 3 | Planner + RBAC-guard + Data/Analytics workers + Synthesizer + blackboard | `multi_agent.py` · `POST /ask/multiagent` |
| 4 | Real MCP server over stdio + client | `mcp_stdio_server.py` · `mcp_client.py` · `/mcp/v2/{tools,call}` |
| 5 | Episodic memory · reflection critic · PII guardrail | `memory_store.py` · `reflection.py` · `guardrails.py` |

## Key endpoints

```
POST /login              # verifies user_id + password
POST /ask                # deterministic pipeline (legacy) + file export
POST /ask/agentic        # single-agent tool-calling loop  (Level 1-2)
POST /ask/multiagent     # full planner→workers→synth pipeline (Level 3-5)
GET  /memory/{user_id}   # inspect persisted episodic memory
GET  /mcp/v2/tools       # discover tools over the real MCP stdio server
POST /mcp/v2/call        # call a tool over the real MCP stdio server
```

Try the real MCP boundary standalone:

```bash
python mcp_stdio_server.py            # runs the MCP server on stdio
```

## Architecture map

```
main.py                 → FastAPI server, routes, endpoints
agent_loop.py           → Level 1: LLM tool-calling loop (single agent)
agent_tools.py          → Level 2: 11 RBAC-scoped tools + authorize() gate
multi_agent.py          → Level 3: planner + workers + synthesizer + blackboard
mcp_stdio_server.py     → Level 4: real MCP server (official SDK, stdio)
mcp_client.py           → Level 4: MCP client bridge (spawns + calls, with fallback)
memory_store.py         → Level 5: persistent, queryable episodic memory
reflection.py           → Level 5: fact-checking critic (self-correction)
guardrails.py           → Level 5: output PII-leak guardrail
auth_store.py           → salted-hash password verification for /login
csv_db.py               → data/students.csv adapter (reads real columns)
data_retriever.py       → CSV academic data retrieval
rbac.py                 → CSV/ID-pattern role + identity resolution
response_formatter.py   → PDF/Word/Excel/TXT export
agentic_workflow.py     → legacy deterministic pipeline (kept for /ask)
static/index.html       → Frontend (chat UI + 🧠 Agentic trace panel)
```
