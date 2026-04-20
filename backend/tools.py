import os
import httpx
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

SERPER_API_KEY     = os.getenv("SERPER_API_KEY")
GMAIL_USER         = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY")
HUNTER_API_KEY     = os.getenv("HUNTER_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)


# ─────────────────────────────────────────────────────────────
# TOOL 0: ICP Company Finder  (NEW)
# Uses Serper to find top matching companies from ICP description
# ─────────────────────────────────────────────────────────────
def tool_icp_company_finder(icp: str) -> dict:
    """
    Takes the user's ICP description and uses Serper (Google Search)
    to find real matching companies. Extracts company name + domain.
    Returns top 5 companies that best match the ICP.
    Fully deterministic — no guessing.
    """
    headers = {
        "X-API-KEY":    SERPER_API_KEY,
        "Content-Type": "application/json"
    }

    # Step 1: Use Groq to extract smart search queries from ICP
    query_prompt = f"""You are a B2B sales intelligence expert.

Given this ICP (Ideal Customer Profile):
"{icp}"

Generate 2 Google search queries to find REAL companies that match this ICP.
Focus on: company stage, industry, size signals mentioned in the ICP.

Return ONLY a JSON array of 2 query strings. Example:
["Series B cybersecurity startups 2024 2025", "funded security tech startups hiring engineers 2025"]

Return only the JSON array, nothing else."""

    query_response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": query_prompt}],
        temperature=0.3,
        max_tokens=200
    )

    try:
        raw = query_response.choices[0].message.content.strip()
        raw = raw.replace("```json","").replace("```","").strip()
        search_queries = json.loads(raw)
    except Exception:
        search_queries = [f"{icp} companies 2025", f"top startups {icp} recent funding"]

    # Step 2: Search Serper with each query and collect company results
    raw_results = []
    for query in search_queries[:2]:
        try:
            resp = httpx.post(
                "https://google.serper.dev/search",
                headers=headers,
                json={"q": query, "num": 5},
                timeout=10
            )
            data = resp.json()
            for item in data.get("organic", [])[:5]:
                raw_results.append({
                    "title":   item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link":    item.get("link", "")
                })
        except Exception:
            pass

    # Step 3: Use Groq to extract structured company list from search results
    extract_prompt = f"""You are a B2B data extraction expert.

From the search results below, extract up to 5 REAL companies that match this ICP:
ICP: "{icp}"

Search Results:
{json.dumps(raw_results, indent=2)}

For each company extract:
- name: company name
- domain: website domain (e.g. stripe.com) — must be a real domain
- reason: 1 sentence why they match the ICP

Return ONLY a JSON array. Example:
[
  {{"name": "Acme Corp", "domain": "acmecorp.com", "reason": "Series B startup hiring 10 engineers"}},
  {{"name": "Beta Inc",  "domain": "betainc.io",   "reason": "Recently raised $20M, expanding security team"}}
]

Return only the JSON array, no explanation."""

    extract_response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": extract_prompt}],
        temperature=0.2,
        max_tokens=600
    )

    try:
        raw2 = extract_response.choices[0].message.content.strip()
        raw2 = raw2.replace("```json","").replace("```","").strip()
        companies = json.loads(raw2)
        # Validate each entry has required fields
        companies = [
            c for c in companies
            if c.get("name") and c.get("domain")
        ][:5]
    except Exception:
        companies = []

    return {
        "status":    "success",
        "icp":       icp,
        "companies": companies,
        "total":     len(companies)
    }


# ─────────────────────────────────────────────────────────────
# TOOL 1: Email Finder (Hunter.io) — Deterministic
# ─────────────────────────────────────────────────────────────
def tool_email_finder(company_domain: str, company_name: str) -> dict:
    """
    Uses Hunter.io Domain Search API to find real verified email addresses.
    Returns emails ranked by confidence score.
    Filters decision makers: CTO, CEO, VP, Head, Director, Founder, Security.
    """
    try:
        response = httpx.get(
            "https://api.hunter.io/v2/domain-search",
            params={
                "domain":  company_domain,
                "api_key": HUNTER_API_KEY,
                "limit":   10,
                "type":    "personal"
            },
            timeout=15
        )
        data = response.json()

        if "errors" in data:
            return {
                "status":  "error",
                "error":   data["errors"][0]["details"],
                "emails":  [],
                "company": company_name
            }

        emails_raw = data.get("data", {}).get("emails", [])
        emails = []
        for e in emails_raw:
            emails.append({
                "email":      e.get("value", ""),
                "first_name": e.get("first_name", ""),
                "last_name":  e.get("last_name", ""),
                "position":   e.get("position", "Unknown Role"),
                "confidence": e.get("confidence", 0),
                "linkedin":   e.get("linkedin", "")
            })

        emails.sort(key=lambda x: x["confidence"], reverse=True)

        decision_makers = [
            e for e in emails
            if any(t in (e.get("position") or "").lower()
                   for t in ["cto","ceo","vp","head","director","founder","engineer","security"])
        ]

        return {
            "status":          "success",
            "company":         company_name,
            "domain":          company_domain,
            "total_found":     len(emails),
            "emails":          emails,
            "decision_makers": decision_makers[:5],
            "pattern":         data.get("data", {}).get("pattern", "unknown")
        }

    except Exception as e:
        return {
            "status":  "error",
            "error":   str(e),
            "emails":  [],
            "company": company_name
        }


# ─────────────────────────────────────────────────────────────
# TOOL 2: Signal Harvester (Serper — Deterministic)
# ─────────────────────────────────────────────────────────────
def tool_signal_harvester(company_name: str) -> dict:
    """
    Fetches live buyer signals for a target company using Serper (Google Search API).
    Searches: funding rounds, hiring trends, leadership changes, tech news.
    """
    signals = {}
    headers = {
        "X-API-KEY":    SERPER_API_KEY,
        "Content-Type": "application/json"
    }

    queries = {
        "funding":    f"{company_name} funding round 2024 2025",
        "hiring":     f"{company_name} hiring engineers jobs 2025",
        "leadership": f"{company_name} new CTO CEO hire leadership 2025",
        "news":       f"{company_name} expansion growth product launch 2025",
        "techstack":  f"{company_name} technology stack engineering blog"
    }

    for signal_type, query in queries.items():
        try:
            response = httpx.post(
                "https://google.serper.dev/search",
                headers=headers,
                json={"q": query, "num": 3},
                timeout=10
            )
            data = response.json()
            results = []
            for item in data.get("organic", [])[:3]:
                results.append({
                    "title":   item.get("title", ""),
                    "snippet": item.get("snippet", ""),
                    "link":    item.get("link", "")
                })
            signals[signal_type] = results
        except Exception as e:
            signals[signal_type] = [{"error": str(e)}]

    try:
        news_resp = httpx.post(
            "https://google.serper.dev/news",
            headers=headers,
            json={"q": company_name, "num": 5},
            timeout=10
        )
        news_data = news_resp.json()
        signals["latest_news"] = [
            {"title": n.get("title",""), "snippet": n.get("snippet",""), "date": n.get("date","")}
            for n in news_data.get("news", [])[:5]
        ]
    except Exception as e:
        signals["latest_news"] = [{"error": str(e)}]

    return {
        "company": company_name,
        "signals": signals,
        "status":  "success"
    }


# ─────────────────────────────────────────────────────────────
# TOOL 3: Research Analyst (Groq AI)
# ─────────────────────────────────────────────────────────────
def tool_research_analyst(company_name: str, signals: dict, icp: str) -> dict:
    """
    Takes harvested signals + ICP and generates a 2-paragraph Account Brief.
    """
    signals_text = json.dumps(signals, indent=2)

    prompt = f"""You are a senior B2B sales research analyst.

Write a 2-paragraph Account Brief:
- Paragraph 1: Summarize the company's current growth signals and priorities
- Paragraph 2: Connect those signals to the seller's ICP, identify pain points and strategic alignment

Company: {company_name}
Seller ICP: {icp}

Live Signals:
{signals_text}

Be specific — reference actual signals. Do not be generic."""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
        max_tokens=600
    )

    return {
        "company":       company_name,
        "account_brief": response.choices[0].message.content.strip(),
        "icp":           icp,
        "status":        "success"
    }


# ─────────────────────────────────────────────────────────────
# TOOL 4: Outreach Automated Sender (Groq AI + Gmail SMTP)
# ─────────────────────────────────────────────────────────────
def tool_outreach_automated_sender(
    company_name:     str,
    account_brief:    str,
    signals:          dict,
    icp:              str,
    recipient_emails: list
) -> dict:
    """
    Generates a personalized cold email and sends to all recipients via Gmail SMTP.
    """
    signals_summary = []
    for signal_type, results in signals.get("signals", {}).items():
        for r in results[:1]:
            if "title" in r and r["title"]:
                signals_summary.append(f"[{signal_type}] {r['title']}: {r.get('snippet','')}")

    signals_text = "\n".join(signals_summary[:6])

    email_prompt = f"""You are an elite B2B sales copywriter. Write a cold outreach email.

RULES:
- Subject: clever, specific to this company
- Opening: reference a SPECIFIC signal (funding, hiring, news)
- Body: connect their growth to the seller's solution
- Zero generic lines
- Max 150 words
- NO greeting line — added automatically
- End with soft CTA (15-min call)
- Sign off: "Alex Rivera, FireReach"

Company: {company_name}
ICP: {icp}
Brief: {account_brief}
Signals: {signals_text}

Format:
SUBJECT: <subject>
BODY:
<body without greeting>"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": email_prompt}],
        temperature=0.6,
        max_tokens=500
    )

    email_content = response.choices[0].message.content.strip()
    subject = f"Growth at {company_name} — Quick thought"
    body    = email_content

    lines = email_content.split("\n")
    for i, line in enumerate(lines):
        if line.upper().startswith("SUBJECT:"):
            subject = line.split(":", 1)[1].strip()
        if line.upper().startswith("BODY:"):
            body = "\n".join(lines[i+1:]).strip()
            break

    send_results  = []
    success_count = 0
    failed_count  = 0

    for recipient in recipient_emails:
        email_addr = recipient.get("email", "")
        first_name = recipient.get("first_name", "")
        position   = recipient.get("position", "")
        if not email_addr:
            continue

        greeting          = f"Hi {first_name}," if first_name else "Hi there,"
        personalized_body = f"{greeting}\n\n{body}"

        try:
            msg            = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = GMAIL_USER
            msg["To"]      = email_addr

            html_body    = personalized_body.replace("\n", "<br>")
            html_content = f"""<html><body style="font-family:Georgia,serif;font-size:15px;color:#222;max-width:600px;margin:0 auto;padding:20px;">
<p>{html_body}</p>
<hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
<p style="font-size:12px;color:#999;">Sent via FireReach — Autonomous Outreach Engine</p>
</body></html>"""

            msg.attach(MIMEText(personalized_body, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
                server.sendmail(GMAIL_USER, email_addr, msg.as_string())

            send_results.append({
                "email": email_addr,
                "name": f"{first_name} {recipient.get('last_name','')}".strip(),
                "position": position, "status": "sent"
            })
            success_count += 1

        except Exception as e:
            send_results.append({
                "email": email_addr, "position": position,
                "status": "failed", "error": str(e)
            })
            failed_count += 1

    return {
        "status":       "completed",
        "company":      company_name,
        "subject":      subject,
        "email_body":   body,
        "total_sent":   success_count,
        "total_failed": failed_count,
        "send_results": send_results
    }


# ─────────────────────────────────────────────────────────────
# HELPER: Generate Email Preview (no sending)
# ─────────────────────────────────────────────────────────────
def generate_email_preview(
    company_name:     str,
    account_brief:    str,
    signals:          dict,
    icp:              str,
    recipient_emails: list
) -> dict:
    signals_summary = []
    for signal_type, results in signals.get("signals", {}).items():
        for r in results[:1]:
            if "title" in r and r["title"]:
                signals_summary.append(f"[{signal_type}] {r['title']}: {r.get('snippet','')}")

    signals_text = "\n".join(signals_summary[:6])

    email_prompt = f"""You are an elite B2B sales copywriter. Write a cold outreach email.

RULES:
- Subject: clever, specific to this company
- Opening: reference a SPECIFIC signal
- Body: connect growth to seller's solution
- Zero generic lines, max 150 words
- NO greeting line
- Soft CTA, sign off as "Alex Rivera, FireReach"

Company: {company_name}
ICP: {icp}
Brief: {account_brief}
Signals: {signals_text}

Format:
SUBJECT: <subject>
BODY:
<body without greeting>"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": email_prompt}],
        temperature=0.6,
        max_tokens=500
    )

    email_content = response.choices[0].message.content.strip()
    subject = f"Growth at {company_name} — Quick thought"
    body    = email_content

    lines = email_content.split("\n")
    for i, line in enumerate(lines):
        if line.upper().startswith("SUBJECT:"):
            subject = line.split(":", 1)[1].strip()
        if line.upper().startswith("BODY:"):
            body = "\n".join(lines[i+1:]).strip()
            break

    return {"subject": subject, "body": body, "recipients": recipient_emails}