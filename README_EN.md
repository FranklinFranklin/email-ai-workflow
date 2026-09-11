# AI E-mail Workflow

---

## How does it work?

Imagine this: you receive a large volume of emails every day at work.
Some are questions, some are complaints, some are about invoices.
It takes a lot of time to read all those emails *and* respond to them.

**This program helps with that.** It reads your emails, understands what they're about,
and prepares a draft response for you.

You then decide whether to send that response, edit it, or discard it.

> 📬 **The program never sends anything on its own.**
> You always press "send" yourself.

This is intentional — because a computer can make mistakes,
and you know best what your customers need.

---

## How it works at a glance

```
1. Email arrives
        │
        ▼
2. The program reads the email
   (sensitive data is masked immediately)
        │
        ▼
3. AI determines: is this a complaint? a question? an invoice?
        │
        ▼
4. AI writes a draft response
        │
        ▼
5. You review the draft, edit if needed,
   and send it yourself via your email client
```

---

## What do you need to get started?

| What | Where to get it |
|---|---|
| **Docker Desktop** | [docker.com/get-started](https://www.docker.com/get-started) |
| **An Anthropic API key** | [console.anthropic.com](https://console.anthropic.com) |
| **A Google Cloud project** with Gmail API enabled | [console.cloud.google.com](https://console.cloud.google.com) |
| **Prefer no cloud service?** | see line 88 for the local version |

---

## Installation — step by step

### Step 1 — Download the code

```bash
git clone <repo-url>
cd email-ai-workflow
```

### Step 2 — Create your configuration file

```bash
cp .env.example .env
```

Open `.env` in a text editor and fill in the required fields:

```
# Minimum required to start:

APP_SECRET_KEY=         ← enter a random long text (min. 32 characters)
POSTGRES_PASSWORD=      ← choose a strong database password
REDIS_PASSWORD=         ← choose a strong cache password
ANTHROPIC_API_KEY=      ← paste your Anthropic API key (starts with sk-ant-...)
GMAIL_CLIENT_ID=        ← from your Google Cloud project
GMAIL_CLIENT_SECRET=    ← from your Google Cloud project
```

> **Prefer no cloud service?** See below how to use Ollama with a local model.

---

## Option: Using Ollama (local model, no API key needed)

With Ollama, the AI model runs completely on your own computer.
No API costs, no internet required for AI, and your data never leaves your machine.

> You'll need a reasonably powerful computer (minimum 8 GB RAM recommended).

### Step 1 — Install Ollama

Go to [ollama.com](https://ollama.com) and download the installer for your system.
Start Ollama after installation — it runs as a background service on port `11434`.

### Step 2 — Download a model

Open a terminal and type:

```bash
ollama pull llama3.2
```

This downloads the LLaMA 3.2 model (~2 GB). Other good options:

| Model | Command | Size | Quality |
|---|---|---|---|
| LLaMA 3.2 (recommended) | `ollama pull llama3.2` | ~2 GB | ⭐⭐⭐⭐ |
| Mistral | `ollama pull mistral` | ~4 GB | ⭐⭐⭐⭐ |
| Qwen 2.5 | `ollama pull qwen2.5` | ~4 GB | ⭐⭐⭐⭐ |
| Phi-3 Mini (fast, lightweight) | `ollama pull phi3` | ~2 GB | ⭐⭐⭐ |

### Step 3 — Configure Ollama as provider

Edit your `.env` file:

```
# Switch to Ollama
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2       ← use the name of the model you downloaded

# ANTHROPIC_API_KEY is no longer required when LLM_PROVIDER=ollama
ANTHROPIC_API_KEY=
```

### Step 4 — Verify Ollama is running

```bash
curl http://localhost:11434/api/tags
# Expected: a list of downloaded models
```

### Step 5 — Start the application as usual

```bash
docker compose up -d
```

That's it. The app now automatically uses the local model.

> **Switching between Anthropic and Ollama?**
> Just change `LLM_PROVIDER=` in `.env` and restart the application.
> All other settings remain the same.

---

Generate an encryption key for sensitive data:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the output as the value for `ENCRYPTION_KEY=` in your `.env`.

### Step 3 — Start the application

```bash
docker compose up -d
```

This automatically starts:
- The application (port `8000`)
- The database (PostgreSQL)
- The cache (Redis)

Verify everything is running:

```bash
curl http://localhost:8000/healthz
# Expected: {"status": "ok"}
```

### Step 4 — Initialize the database

```bash
docker compose exec api alembic upgrade head
```

### Step 5 — Connect Gmail

Go to `http://localhost:8000/docs` and use the OAuth endpoints
to link your Gmail account. The system only requests
**read access** — it can never send emails on its own.

---

## Daily usage

Once everything is running:

1. **New emails arrive automatically** via Gmail.
2. **Open the review dashboard** (default at `http://localhost:8000/docs`).
3. **For each email you'll see:**
   - The original email
   - The category (e.g., *Complaint*, *Invoice*, *Question*)
   - A ready-made draft response
4. **Approve, edit, or reject** the draft.
5. **Approved?** Your email client opens with the draft filled in.
   You send it.

---

## Frequently Asked Questions

**Can the program accidentally send emails?**
No. The system has no technical access to send emails.
The Gmail connection has read-only permissions.

**What happens to my email data?**
Sensitive data (names, email addresses, IBANs) is encrypted
and stored, never sent to external AI services without
anonymization.

**What if the AI makes a mistake in the draft?**
You always see the draft before it's sent.
If the AI is uncertain, a warning appears with the draft.

**How do I stop the application?**
```bash
docker compose down
```

---

## Technical Overview (for developers)

```
email-ai-workflow/
├── agents/          LLM agents (classification + draft generation)
├── api/             FastAPI routes and middleware
├── config/          Settings and logging
├── database/        Database connection and repositories
├── models/          ORM models and Pydantic schemas
├── security/        Encryption, PII detection, audit log
├── services/        Gmail integration and processing logic
└── tests/           Unit, integration, and security tests
```

See the technical documentation in `docs/` for architecture diagrams
and API specifications.

---

## License

Internal use — see `LICENSE` for details.
