# FireReach v2 🔥
### Autonomous Outreach Engine — with Hunter.io Email Discovery

## What it does
1. **Finds emails** — Hunter.io scans the company domain for verified contacts
2. **Harvests signals** — Serper fetches live: funding, hiring, leadership, news
3. **Generates research** — Groq AI writes a personalized Account Brief
4. **Sends automatically** — Gmail SMTP fires emails to every decision maker found

## Folder Structure
```
firereach/
├── .env.example              ← Copy to .env and fill keys
├── README.md
├── DOCS.md                   ← Full documentation (submit this)
├── backend/
│   ├── tools.py              ← All 4 tool functions
│   ├── agent.py              ← Groq agentic loop
│   ├── main.py               ← FastAPI app
│   └── requirements.txt
└── frontend/
    └── index.html            ← Dashboard UI
```

## Quick Start
```bash
# 1. Enter backend
cd firereach/backend

# 2. Create .env from example
cp ../.env.example ../.env
# Edit .env and fill in your 5 API keys

# 3. Install
pip install -r requirements.txt

# 4. Run
python main.py

# 5. Open
# http://localhost:8000
```

## API Keys Required
| Key | Get it from | Free tier |
|-----|-------------|-----------|
| GROQ_API_KEY | console.groq.com | Yes |
| SERPER_API_KEY | serper.dev | 2500 req |
| GMAIL_USER | your Gmail | — |
| GMAIL_APP_PASSWORD | myaccount.google.com → Security → App Passwords | — |
| HUNTER_API_KEY | hunter.io/api-keys | 25 searches/mo |