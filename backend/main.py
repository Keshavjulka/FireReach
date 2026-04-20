import json
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel

from agent import run_firereach_agent, confirm_and_send
from memory import (
    find_similar_campaign,
    save_campaign,
    save_sent_email,
    save_icp_search,
    get_campaign_by_id,
    get_sent_history,
    get_campaigns,
    get_icp_searches
)
from tools import tool_outreach_automated_sender

app = FastAPI(title="FireReach — Autonomous Outreach Engine v5")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
_frontend = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(_frontend):
    app.mount("/static", StaticFiles(directory=_frontend), name="static")


# ─────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────
class OutreachRequest(BaseModel):
    icp: str


class ConfirmRequest(BaseModel):
    company_name:     str
    account_brief:    str
    signals:          dict
    icp:              str
    recipient_emails: list
    campaign_id:      str = ""


class ResendRequest(BaseModel):
    campaign_id: str
    icp:         str


# ─────────────────────────────────────────────────────────────
# Serve frontend
# ─────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return FileResponse(os.path.join(_frontend, "index.html"))


@app.get("/health")
def health():
    return {"status": "FireReach is live", "version": "5.0.0"}


# ─────────────────────────────────────────────────────────────
# Check MongoDB for similar ICP (>=90% match)
# ─────────────────────────────────────────────────────────────
@app.post("/api/check-memory")
def check_memory(request: OutreachRequest):
    """
    Before running the agent, check if a similar ICP campaign
    already exists in MongoDB.
    Returns: { match: bool, campaign: dict|null, similarity: float }
    """
    match = find_similar_campaign(request.icp, threshold=0.9)
    if match:
        # Log this as a memory_match search
        save_icp_search(
            icp=request.icp,
            companies_found=[
                {"name": c.get("company_name",""), "domain": c.get("domain","")}
                for c in match.get("companies", [])
            ],
            action="memory_match"
        )
        return JSONResponse({
            "match":      True,
            "campaign":   match,
            "similarity": match.get("similarity", 100.0)
        })
    return JSONResponse({"match": False, "campaign": None, "similarity": 0})


# ─────────────────────────────────────────────────────────────
# Run full agent (new ICP search)
# ─────────────────────────────────────────────────────────────
@app.post("/api/run")
def run_agent(request: OutreachRequest):
    """
    Streams agent execution as SSE.
    Collects awaiting_approval events to save campaign to MongoDB
    when all_done fires.
    """
    approval_buffer = []   # collect company data for DB save

    def event_stream():
        for event in run_firereach_agent(icp=request.icp):

            # Buffer company data for MongoDB save
            if event.get("event") == "awaiting_approval":
                approval_buffer.append({
                    "company_name":  event["company_name"],
                    "domain":        event.get("domain", ""),
                    "recipients":    event.get("recipients", []),
                    "account_brief": event.get("account_brief", ""),
                    "signals":       event.get("signals", {}),
                    "preview":       event.get("preview", {}),
                    "icp":           event.get("icp", request.icp)
                })

            # Save campaign to MongoDB and attach campaign_id to all_done event
            if event.get("event") == "all_done" and approval_buffer:
                try:
                    campaign_id = save_campaign(
                        icp       = request.icp,
                        companies = approval_buffer
                    )
                    event["campaign_id"] = campaign_id
                except Exception as e:
                    event["campaign_id"] = ""
                    event["db_error"]    = str(e)

            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ─────────────────────────────────────────────────────────────
# Confirm send — user approved one company
# ─────────────────────────────────────────────────────────────
@app.post("/api/confirm")
def confirm_send(request: ConfirmRequest):
    """
    Sends emails to all recipients for one company.
    Saves each sent record to MongoDB.
    """
    result = confirm_and_send(
        company_name     = request.company_name,
        account_brief    = request.account_brief,
        signals          = request.signals,
        icp              = request.icp,
        recipient_emails = request.recipient_emails
    )

    # Save each email outcome to MongoDB
    for sr in result.get("send_results", []):
        try:
            save_sent_email(
                campaign_id  = request.campaign_id,
                company_name = request.company_name,
                recipient    = sr,
                subject      = result.get("subject", ""),
                body         = result.get("email_body", ""),
                status       = sr.get("status", "unknown"),
                icp          = request.icp
            )
        except Exception:
            pass  # Don't fail the send if DB write fails

    return JSONResponse(content=result)


# ─────────────────────────────────────────────────────────────
# Resend — reuse existing campaign from MongoDB
# ─────────────────────────────────────────────────────────────
@app.post("/api/resend")
def resend_campaign(request: ResendRequest):
    """
    Fetches a campaign by ID from MongoDB and resends emails
    to the same recipients with a fresh email generated by Groq.
    """
    campaign = get_campaign_by_id(request.campaign_id)
    if not campaign:
        return JSONResponse(
            {"error": "Campaign not found"},
            status_code=404
        )

    # Log this resend as an ICP search
    save_icp_search(
        icp=request.icp,
        companies_found=[
            {"name": c.get("company_name",""), "domain": c.get("domain","")}
            for c in campaign.get("companies", [])
        ],
        action="resend"
    )

    all_results = []

    for company in campaign.get("companies", []):
        cname      = company.get("company_name", "")
        recipients = company.get("recipients", [])
        brief      = company.get("account_brief", "")
        signals    = company.get("signals", {})

        if not recipients:
            all_results.append({
                "company":      cname,
                "total_sent":   0,
                "total_failed": 0,
                "note":         "No recipients"
            })
            continue

        result = tool_outreach_automated_sender(
            company_name     = cname,
            account_brief    = brief,
            signals          = signals,
            icp              = request.icp,
            recipient_emails = recipients
        )

        # Save resent emails to MongoDB
        for sr in result.get("send_results", []):
            try:
                save_sent_email(
                    campaign_id  = request.campaign_id,
                    company_name = cname,
                    recipient    = sr,
                    subject      = result.get("subject", ""),
                    body         = result.get("email_body", ""),
                    status       = sr.get("status", "unknown"),
                    icp          = request.icp
                )
            except Exception:
                pass

        all_results.append({
            "company":      cname,
            "total_sent":   result.get("total_sent", 0),
            "total_failed": result.get("total_failed", 0),
            "send_results": result.get("send_results", [])
        })

    return JSONResponse({
        "status":  "resent",
        "results": all_results
    })


# ─────────────────────────────────────────────────────────────
# History endpoints
# ─────────────────────────────────────────────────────────────
@app.get("/api/history/emails")
def email_history():
    """Returns all sent email records from MongoDB, newest first."""
    return JSONResponse(get_sent_history(limit=100))


@app.get("/api/history/campaigns")
def campaign_history():
    """Returns all campaigns from MongoDB, newest first."""
    return JSONResponse(get_campaigns(limit=20))


@app.get("/api/history/searches")
def search_history():
    """Returns all ICP searches from MongoDB, newest first."""
    return JSONResponse(get_icp_searches(limit=50))


# ─────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)