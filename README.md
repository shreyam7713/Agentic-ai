# Moodle AI Assistant — CSV Mode Setup Guide

This version uses `/Users/palakpandit/Desktop/students_500.csv` as the only
student database. Moodle SQL and the Excel workbook are no longer required for
the active app flow.

## Prerequisites
- Python 3.10+
- Docker Desktop (running)
- Git

## 1. Clone the repo

```bash
git clone https://github.com/parkervijay/final.git
cd final
```

## 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

## 3. CSV database

The app reads:

```bash
/Users/palakpandit/Desktop/students_500.csv
```

Override the path if needed:

```bash
STUDENT_DB_CSV_PATH=/absolute/path/to/students_500.csv
```

## 4. Create your .env file

Create a file called `.env` in the project root with:

```
GROQ_API_KEY=your_groq_api_key_here
```

Get a free Groq API key from https://console.groq.com/keys

## 5. Run the server

```bash
python -m uvicorn main:app --reload
```

Open http://localhost:8000 in your browser.

### Test queries

Try these user IDs:
- `1NT23IS001` — Student from `students_500.csv`
- `FAC001` — Faculty mode
- `ADM001` — Admin mode

Sample queries:
- "show me my attendance"
- "who is my mentor"
- "show my student profile"
- "what are my backlogs"
- "how many students are there"

## Troubleshooting

**"uvicorn.exe was blocked by Device Guard"**
→ Use `python -m uvicorn main:app --reload` instead

## Architecture

```
main.py                 → FastAPI server, routes, LLM calls
agentic_workflow.py     → Multi-agent orchestrator with trace
classifier.py           → Heuristic query classification
intent_agent.py         → Intent enrichment from CSV DB
csv_db.py               → students_500.csv adapter + derived mentor/attendance/backlogs
data_retriever.py       → CSV-only academic data retrieval
rbac.py                 → CSV/ID-pattern role detection
auth.py                 → Legacy auth (kept for reference)
response_formatter.py   → PDF/Word/Excel/TXT export
static/                 → Frontend (HTML/CSS/JS)
```
