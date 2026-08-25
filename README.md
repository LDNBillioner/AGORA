# AGORA AI Accountant 🤖📊

**WhatsApp AI Accountant — B2B Micro-SaaS untuk UMKM Indonesia**

AGORA lets Indonesian small businesses (UMKM) record income and expenses simply by chatting on WhatsApp — text, voice notes, or a photo of a receipt. An agentic AI pipeline (LangGraph + Gemini) extracts, validates, and stores every transaction, and syncs it to a multi-tenant web dashboard in real time.

> Dibuat untuk **Vibecode @ Antigravity**. Lihat `deskripsi_projek.md` untuk latar belakang produk lengkap.

## Why AGORA?

- **No new app to learn** — bookkeeping happens inside WhatsApp.
- **Multi-modal input** — plain text ("Jual 5 kopi susu @15rb"), voice notes (auto-transcribed), or receipt photos (OCR).
- **Anti-hallucination by design** — if the amount or item is unclear, the agent *asks back* instead of guessing (`request_clarification`).
- **RAG-powered context** — past transactions are embedded into a pgvector store per tenant, so the AI learns each business's categories.

## Architecture

```
WhatsApp Cloud API
      │  webhook
      ▼
FastAPI Engine ──── BackgroundTasks ──► tasks.py (multi-modal router)
      │                                      │
      │                    text ─────────────┤
      │                    audio ─► Gemini STT (google-genai SDK)
      │                    image ─► NVIDIA Nemotron-OCR-v2 (+ Gemini fallback)
      │                                      │
      │                                      ▼
      │                            agent.py (LangGraph)
      │                       agent ⇄ tools (record_transaction,
      │                     request_clarification, recap_transactions,
      │                                      │   get_dashboard_link)
      │                                      ▼
      ▼                              PostgreSQL + pgvector
/dashboard/ui/{tenant}  ◄────  (SQLAlchemy models, Alembic migrations,
                                RAG history via langchain PGVector)
```

### Tech Stack

| Layer | Technology |
|---|---|
| API server | FastAPI + Uvicorn |
| Agent orchestration | LangGraph + LangChain |
| LLM / STT / Vision | Google Gemini (`langchain-google-genai`, `google-genai`) |
| Receipt OCR | NVIDIA Nemotron-OCR-v2 → Gemini structuring fallback |
| Database | PostgreSQL + pgvector (Supabase-compatible) |
| ORM / migrations | SQLAlchemy 2.0 + Alembic |

## Repository Layout

```
src/
  Engine.py        # FastAPI app: webhook, OCR, transactions, dashboard, users
  tasks.py         # Async background processor for incoming WhatsApp messages
  agent.py         # LangGraph agent graph (agent → tools → process_result)
  tools.py         # Agent tools: record_transaction, clarification, recap, dashboard link
  rag.py           # pgvector RAG: index & retrieve past transactions per tenant
  models.py        # SQLAlchemy models (Tenant, User, Transaction)
  database.py      # Engine/session setup from DATABASE_URL
  setup_db.py      # One-click DB bootstrap (connect → pgvector → migrate → seed)
  dashboard.html   # Self-contained visual dashboard page served by FastAPI
migrations/        # Alembic configuration + initial migration
tests/             # Pytest suite (54 tests): normalizers, OCR pipeline, API, tools, agent graph
docker-compose.yml # Local pgvector/pg16 database
```

## Getting Started

### Prerequisites

- Python 3.10+
- A PostgreSQL database with the `vector` extension (e.g. [Supabase](https://supabase.com)), or Docker for a local one

### 1. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL connection string (Supabase URI works) |
| `GOOGLE_API_KEY` | ✅ | Gemini (agent LLM, STT, vision fallback) — [aistudio.google.com](https://aistudio.google.com) |
| `NVIDIA_API_KEY` | optional | Nemotron-OCR primary provider ([build.nvidia.com](https://build.nvidia.com)) |
| `META_ACCESS_TOKEN` | for WhatsApp | WhatsApp Cloud API token |
| `META_PHONE_NUMBER_ID` | for WhatsApp | Sender phone number ID |
| `META_VERIFY_TOKEN` | for WhatsApp | Webhook verification secret (default: `agora_verify_token`) |
| `AGORA_ENGINE_URL` | optional | Public base URL used in dashboard links (default `http://localhost:8000`) |

### 3. Set up the database

```bash
# Option A: local Postgres with pgvector
docker compose up -d

# Then bootstrap schema + seed data
cd src && python setup_db.py
```

`setup_db.py` tests the connection, enables pgvector, runs Alembic migrations, and seeds a demo tenant (`default-tenant`) plus an owner user.

### 4. Run the engine

```bash
cd src
uvicorn Engine:app --host 0.0.0.0 --port 8000
```

- Swagger UI: http://localhost:8000/docs
- Dashboard UI: http://localhost:8000/dashboard/ui/default-tenant

### 5. Connect WhatsApp (production)

1. Deploy the engine somewhere publicly reachable (Railway/Render/Ngrok).
2. In the [Meta Developer Console](https://developers.facebook.com), register the webhook:
   - Callback URL: `https://<your-host>/webhook`
   - Verify token: value of `META_VERIFY_TOKEN`
3. Subscribe to the `messages` field. New senders automatically receive the onboarding message.

## Key Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Health check |
| GET/POST | `/webhook` | Meta webhook verification & message intake |
| POST | `/extract-receipt` | Upload a receipt image → structured JSON (NVIDIA OCR → Gemini fallback) |
| POST/GET | `/transactions` | Manual transaction save / raw listing |
| GET | `/dashboard/summary` | Income/expense/net summary per tenant (+user/date filters) |
| GET | `/dashboard/transactions` | Paginated, filtered transaction list |
| POST | `/users` | Register owner/employee by WhatsApp number |
| GET | `/tenants/{id}/users` | List tenant users |
| GET | `/dashboard/ui/{tenant_id}[/{user_id}]` | Visual HTML dashboard |

## Testing

```bash
pytest tests/ -q
```

The suite covers:

- **Numeric normalizers** — Indonesian/US currency formats (`15.000`, `1.234,56`, …)
- **OCR pipeline** — payload normalization, markdown stripping, item fallback parsing, validation
- **HTTP API** — health, Meta webhook handshake, transaction CRUD, dashboard totals, user registration
- **Agent tools** — tool context, clarification protocol, DB writes against SQLite test doubles
- **LangGraph wiring** — entry point, conditional routing, success/clarification/error outcomes

Tests run fully offline (in-memory/file-based SQLite); no API keys or network calls are required except a dummy `GOOGLE_API_KEY` env var for model instantiation.

## Design Notes

- **Tenant isolation**: every query and vector collection is scoped by `tenant_id`; users are keyed by their WhatsApp number.
- **Clarification-first**: the system prompt forbids hallucinating amounts; missing data routes through `request_clarification`.
- **Graceful degradation**: OCR falls back NVIDIA → Gemini; RAG falls back pgvector → recent-SQL; failures never block transaction recording.
