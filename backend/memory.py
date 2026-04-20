import os
import certifi
from datetime import datetime, timezone
from difflib import SequenceMatcher
from dotenv import load_dotenv
from pymongo import MongoClient
from bson import ObjectId

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

# ── Connect with SSL fix for Python 3.13 ──
_client = MongoClient(
    MONGO_URI,
    tls=True,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=10000,
    connectTimeoutMS=10000,
    socketTimeoutMS=10000
)
_db = _client["firereach"]

campaigns_col  = _db["campaigns"]    # full campaign runs
sent_col       = _db["sent_emails"]  # every sent email
icp_search_col = _db["icp_searches"] # every ICP search (new)


# ─────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────
def _serialize(doc: dict) -> dict:
    """Convert ObjectId and datetime fields to JSON-safe strings."""
    doc["_id"] = str(doc["_id"])
    for field in ["created_at", "sent_at", "searched_at"]:
        if isinstance(doc.get(field), datetime):
            doc[field] = doc[field].isoformat()
    return doc


def icp_similarity(icp1: str, icp2: str) -> float:
    """Returns 0.0–1.0 similarity ratio between two ICP strings."""
    return SequenceMatcher(
        None,
        icp1.lower().strip(),
        icp2.lower().strip()
    ).ratio()


# ─────────────────────────────────────────────────────────────
# ICP Search Log — save every search
# ─────────────────────────────────────────────────────────────
def save_icp_search(icp: str, companies_found: list, action: str = "new_search"):
    """
    Saves every ICP search to MongoDB.
    action = 'new_search' | 'memory_match' | 'resend'
    companies_found = list of {name, domain} dicts
    """
    try:
        icp_search_col.insert_one({
            "icp":             icp,
            "searched_at":     datetime.now(timezone.utc),
            "action":          action,
            "companies_found": companies_found,
            "total_companies": len(companies_found)
        })
    except Exception as e:
        print(f"[memory] save_icp_search failed: {e}")


def get_icp_searches(limit: int = 50) -> list:
    """Returns recent ICP searches, newest first."""
    try:
        docs = list(icp_search_col.find({}, sort=[("searched_at", -1)], limit=limit))
        return [_serialize(d) for d in docs]
    except Exception as e:
        print(f"[memory] get_icp_searches failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# Campaign memory — find similar past campaign
# ─────────────────────────────────────────────────────────────
def find_similar_campaign(icp: str, threshold: float = 0.9) -> dict | None:
    """
    Scans past campaigns for ICP similarity >= threshold.
    Returns most recent match with similarity% added, or None.
    """
    try:
        past = list(campaigns_col.find({}, sort=[("created_at", -1)]))
        for doc in past:
            sim = icp_similarity(icp, doc.get("icp", ""))
            if sim >= threshold:
                doc = _serialize(doc)
                doc["similarity"] = round(sim * 100, 1)
                return doc
    except Exception as e:
        print(f"[memory] find_similar_campaign failed: {e}")
    return None


# ─────────────────────────────────────────────────────────────
# Save a new campaign
# ─────────────────────────────────────────────────────────────
def save_campaign(icp: str, companies: list) -> str:
    """
    Saves a full campaign to MongoDB.
    Returns inserted _id as string.
    """
    try:
        result = campaigns_col.insert_one({
            "icp":        icp,
            "created_at": datetime.now(timezone.utc),
            "companies":  companies,
            "total":      len(companies)
        })
        # Also log this as an ICP search
        save_icp_search(
            icp=icp,
            companies_found=[
                {"name": c.get("company_name",""), "domain": c.get("domain","")}
                for c in companies
            ],
            action="new_search"
        )
        return str(result.inserted_id)
    except Exception as e:
        print(f"[memory] save_campaign failed: {e}")
        return ""


# ─────────────────────────────────────────────────────────────
# Get campaign by ID
# ─────────────────────────────────────────────────────────────
def get_campaign_by_id(campaign_id: str) -> dict | None:
    """Fetches a single campaign by its MongoDB _id string."""
    try:
        doc = campaigns_col.find_one({"_id": ObjectId(campaign_id)})
        return _serialize(doc) if doc else None
    except Exception as e:
        print(f"[memory] get_campaign_by_id failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Save one sent email record
# ─────────────────────────────────────────────────────────────
def save_sent_email(
    campaign_id:  str,
    company_name: str,
    recipient:    dict,
    subject:      str,
    body:         str,
    status:       str,
    icp:          str
):
    """Records a single sent email in MongoDB."""
    try:
        sent_col.insert_one({
            "campaign_id":  campaign_id,
            "icp":          icp,
            "company_name": company_name,
            "recipient":    recipient,
            "subject":      subject,
            "body":         body,
            "status":       status,
            "sent_at":      datetime.now(timezone.utc)
        })
    except Exception as e:
        print(f"[memory] save_sent_email failed: {e}")


# ─────────────────────────────────────────────────────────────
# History queries — all return [] on DB error (never crash app)
# ─────────────────────────────────────────────────────────────
def get_sent_history(limit: int = 100) -> list:
    """Returns recent sent emails, newest first."""
    try:
        docs = list(sent_col.find({}, sort=[("sent_at", -1)], limit=limit))
        return [_serialize(d) for d in docs]
    except Exception as e:
        print(f"[memory] get_sent_history failed: {e}")
        return []


def get_campaigns(limit: int = 20) -> list:
    """Returns recent campaigns, newest first."""
    try:
        docs = list(campaigns_col.find({}, sort=[("created_at", -1)], limit=limit))
        return [_serialize(d) for d in docs]
    except Exception as e:
        print(f"[memory] get_campaigns failed: {e}")
        return []