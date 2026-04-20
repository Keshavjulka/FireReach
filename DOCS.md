# FireReach v2 — Agent Documentation

## Overview
FireReach is an autonomous B2B outreach engine. It finds verified emails via Hunter.io, harvests live buyer signals via Serper, generates an AI research brief, and sends a hyper-personalized cold email to every decision maker — all in one sequential agent loop powered by Groq (Llama 3.3 70B).

---

## Logic Flow

```
User Input (Company Name + Domain + ICP)
        ↓
[tool_email_finder]  ← Hunter.io
  → Finds all verified emails at the domain
  → Ranks by confidence score
  → Filters decision makers (CTO, CEO, VP, Head, Director)
        ↓
[tool_signal_harvester]  ← Serper (Google Search)
  → Fetches live signals: funding, hiring, leadership, news, techstack
  → Deterministic — real web data, no LLM guessing
        ↓
[tool_research_analyst]  ← Groq Llama 3.3 70B
  → Reads signals + ICP
  → Generates 2-paragraph Account Brief grounded in real data
        ↓
[tool_outreach_automated_sender]  ← Groq + Gmail SMTP
  → Generates personalized email referencing specific signals
  → Sends to ALL decision maker emails from Step 1
  → Personalizes greeting per recipient (first name from Hunter)
        ↓
Dashboard shows: emails found · signals · brief · email preview · delivery report
```

---

## Tool Schemas

### tool_email_finder
**Type:** Deterministic (Hunter.io API)

```json
{
  "name": "tool_email_finder",
  "description": "Uses Hunter.io to find real verified email addresses for a company domain. Returns emails ranked by confidence score and filters decision makers.",
  "parameters": {
    "type": "object",
    "properties": {
      "company_domain": { "type": "string", "description": "e.g. stripe.com" },
      "company_name":   { "type": "string", "description": "e.g. Stripe" }
    },
    "required": ["company_domain", "company_name"]
  }
}
```

**Returns:**
```json
{
  "status": "success",
  "company": "Stripe",
  "domain": "stripe.com",
  "total_found": 8,
  "emails": [
    { "email": "john@stripe.com", "first_name": "John", "position": "CTO", "confidence": 94 }
  ],
  "decision_makers": [...],
  "pattern": "{first}.{last}@stripe.com"
}
```

---

### tool_signal_harvester
**Type:** Deterministic (Serper / Google Search API)

```json
{
  "name": "tool_signal_harvester",
  "description": "Fetches live buyer signals for a target company using Google Search. Searches funding, hiring, leadership, news, and tech stack.",
  "parameters": {
    "type": "object",
    "properties": {
      "company_name": { "type": "string" }
    },
    "required": ["company_name"]
  }
}
```

---

### tool_research_analyst
**Type:** AI-powered (Groq Llama 3.3 70B)

```json
{
  "name": "tool_research_analyst",
  "description": "Generates a 2-paragraph Account Brief from signals + ICP.",
  "parameters": {
    "type": "object",
    "properties": {
      "company_name": { "type": "string" },
      "signals":      { "type": "object" },
      "icp":          { "type": "string" }
    },
    "required": ["company_name", "signals", "icp"]
  }
}
```

---

### tool_outreach_automated_sender
**Type:** AI-powered + Execution (Groq + Gmail SMTP)

```json
{
  "name": "tool_outreach_automated_sender",
  "description": "Generates a personalized cold email referencing live signals, sends it to ALL Hunter.io decision makers via Gmail SMTP.",
  "parameters": {
    "type": "object",
    "properties": {
      "company_name":      { "type": "string" },
      "account_brief":     { "type": "string" },
      "signals":           { "type": "object" },
      "icp":               { "type": "string" },
      "recipient_emails":  { "type": "array", "items": { "type": "object" } }
    },
    "required": ["company_name", "account_brief", "signals", "icp", "recipient_emails"]
  }
}
```

---

## System Prompt

```
You are FireReach, an autonomous B2B outreach agent built for elite GTM teams.

YOUR PERSONA:
- World-class SDR with deep expertise in reading market signals
- Think like a strategist, write like a human, execute like a machine
- Never guess — always ground research in real data

YOUR MISSION — 4-step workflow:

STEP 1 — tool_email_finder: Find verified emails for the domain. Use decision_makers for sending.
STEP 2 — tool_signal_harvester: Fetch live signals. Never fabricate.
STEP 3 — tool_research_analyst: Generate Account Brief connecting signals to ICP.
STEP 4 — tool_outreach_automated_sender: Send personalized email to ALL decision makers.

CONSTRAINTS:
- Always complete all 4 steps in exact order
- Never fabricate signals or emails
- Email must feel human — zero templates
- Pass decision_makers from Step 1 as recipient_emails in Step 4
```

---

## Environment Variables

```env
GROQ_API_KEY=your_groq_key
SERPER_API_KEY=your_serper_key
GMAIL_USER=yourname@gmail.com
GMAIL_APP_PASSWORD=your_16char_app_password
HUNTER_API_KEY=your_hunter_api_key
```

---

## Run Locally

```bash
cd firereach/backend
pip install -r requirements.txt
python main.py
# Open http://localhost:8000
```