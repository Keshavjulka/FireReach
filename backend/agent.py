import os
import json
from groq import Groq
from dotenv import load_dotenv
from tools import (
    tool_icp_company_finder,
    tool_email_finder,
    tool_signal_harvester,
    tool_research_analyst,
    tool_outreach_automated_sender,
    generate_email_preview
)

load_dotenv()
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "tool_icp_company_finder",
            "description": "Finds top 3 matching companies for the ICP. Call this FIRST and ONLY ONCE.",
            "parameters": {
                "type": "object",
                "properties": {"icp": {"type": "string"}},
                "required": ["icp"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_email_finder",
            "description": "Finds verified emails for a company domain via Hunter.io.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_domain": {"type": "string"},
                    "company_name":   {"type": "string"}
                },
                "required": ["company_domain", "company_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_signal_harvester",
            "description": "Fetches live signals for a company: funding, hiring, news.",
            "parameters": {
                "type": "object",
                "properties": {"company_name": {"type": "string"}},
                "required": ["company_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_research_analyst",
            "description": "Generates a 2-paragraph Account Brief from signals + ICP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "signals":      {"type": "object"},
                    "icp":          {"type": "string"}
                },
                "required": ["company_name", "signals", "icp"]
            }
        }
    }
]

SYSTEM_PROMPT = """You are FireReach, a B2B outreach agent.

WORKFLOW:
1. Call tool_icp_company_finder(icp) ONCE.
2. For each company (MAX 3 only): call tool_email_finder, then tool_signal_harvester, then tool_research_analyst.
3. STOP. Never repeat a tool for the same company.

RULES:
- Maximum 3 companies
- Each tool called exactly once per company
- Stop after all 3 researched
"""


def slim_result(tool_name: str, result: dict) -> dict:
    """Return minimal result to pass back to LLM — saves tokens."""
    if tool_name == "tool_icp_company_finder":
        return {
            "companies": [
                {"name": c["name"], "domain": c["domain"]}
                for c in result.get("companies", [])[:3]
            ]
        }
    elif tool_name == "tool_email_finder":
        return {
            "company":     result.get("company"),
            "total_found": result.get("total_found", 0),
            "dm_count":    len(result.get("decision_makers", []))
        }
    elif tool_name == "tool_signal_harvester":
        # Only top title per signal type
        tops = {}
        for k, v in result.get("signals", {}).items():
            if v and isinstance(v, list) and v[0].get("title"):
                tops[k] = v[0]["title"][:80]
        return {"company": result.get("company"), "signals": tops}
    elif tool_name == "tool_research_analyst":
        return {
            "company": result.get("company"),
            "brief":   result.get("account_brief", "")[:200]
        }
    return {"status": "done"}


def run_firereach_agent(icp: str):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"ICP: {icp}\n\nResearch 3 companies max."}
    ]

    companies    = {}   # name → {email, signals, brief} — full data in memory
    tools_called = set()  # prevent duplicate calls
    loop_count   = 0

    yield {"event": "start", "message": "FireReach launched — finding best companies for your ICP..."}

    while loop_count < 25:
        loop_count += 1

        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=800
            )
        except Exception as e:
            err = str(e)
            yield {"event": "error", "message": f"API error: {err}"}
            # If rate limit, stop gracefully
            if "rate_limit" in err or "413" in err or "too large" in err.lower():
                break
            break

        message       = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        # Append assistant turn
        asst = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            asst["tool_calls"] = [
                {
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                }
                for tc in message.tool_calls
            ]
        messages.append(asst)

        if finish_reason == "stop" or not message.tool_calls:
            break

        tool_msgs = []

        for tc in message.tool_calls:
            tname = tc.function.name
            try:
                targs = json.loads(tc.function.arguments)
            except Exception:
                continue

            cname = targs.get("company_name", "")
            call_key = f"{tname}::{cname}"

            # Skip duplicate calls
            if tname != "tool_icp_company_finder" and call_key in tools_called:
                tool_msgs.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps({"status": "already_called", "company": cname})
                })
                continue
            tools_called.add(call_key)

            yield {
                "event": "tool_start", "tool": tname,
                "args":  {k: v for k, v in targs.items() if k != "signals"}
            }

            # ── Execute tool ──
            try:
                if tname == "tool_icp_company_finder":
                    full = tool_icp_company_finder(**targs)
                    # Cap to 3 companies
                    full["companies"] = full.get("companies", [])[:3]

                elif tname == "tool_email_finder":
                    full = tool_email_finder(**targs)
                    if cname not in companies: companies[cname] = {}
                    companies[cname]["email"] = full

                elif tname == "tool_signal_harvester":
                    full = tool_signal_harvester(**targs)
                    if cname not in companies: companies[cname] = {}
                    companies[cname]["signals"] = full

                elif tname == "tool_research_analyst":
                    # Use full signals from memory, not the slim version the LLM has
                    mem_signals = companies.get(cname, {}).get("signals", targs.get("signals", {}))
                    full = tool_research_analyst(
                        company_name=cname,
                        signals=mem_signals,
                        icp=targs.get("icp", icp)
                    )
                    if cname not in companies: companies[cname] = {}
                    companies[cname]["brief"] = full

                else:
                    full = {"error": f"Unknown: {tname}"}

            except Exception as e:
                full = {"error": str(e), "company": cname}

            # Stream slim version to frontend
            slim = slim_result(tname, full)
            slim["company"] = full.get("company", cname)

            yield {"event": "tool_done", "tool": tname, "result": slim}

            # Pass slim result back to LLM (saves tokens)
            tool_msgs.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(slim)
            })

        messages.extend(tool_msgs)

        # ── Trim history: keep system + user + last 8 messages only ──
        if len(messages) > 12:
            messages = messages[:2] + messages[-10:]

        # Early exit if 3 companies fully researched
        done = [
            n for n, d in companies.items()
            if "email" in d and "signals" in d and "brief" in d
        ]
        if len(done) >= 3:
            break

    # ── Send approval events ──
    yield {"event": "research_complete", "message": "Research done — review each company below before sending."}

    for cname, data in companies.items():
        email_r  = data.get("email",   {})
        signal_r = data.get("signals", {})
        brief_r  = data.get("brief",   {})

        if not signal_r or not brief_r:
            continue

        recipients = email_r.get("decision_makers", [])

        try:
            preview = generate_email_preview(
                company_name     = cname,
                account_brief    = brief_r.get("account_brief", ""),
                signals          = signal_r,
                icp              = icp,
                recipient_emails = recipients
            )
        except Exception as e:
            preview = {"subject": f"Re: {cname}", "body": str(e), "recipients": recipients}

        yield {
            "event":         "awaiting_approval",
            "company_name":  cname,
            "domain":        email_r.get("domain", ""),
            "recipients":    recipients,
            "total_emails":  email_r.get("total_found", 0),
            "preview":       preview,
            "account_brief": brief_r.get("account_brief", ""),
            "signals":       signal_r,
            "icp":           icp
        }

    yield {"event": "all_done", "message": "All companies queued for approval."}


def confirm_and_send(company_name, account_brief, signals, icp, recipient_emails):
    return tool_outreach_automated_sender(
        company_name=company_name,
        account_brief=account_brief,
        signals=signals,
        icp=icp,
        recipient_emails=recipient_emails
    )