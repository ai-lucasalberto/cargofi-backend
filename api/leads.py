import os
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

router = APIRouter()

class LeadIn(BaseModel):
    company: str
    email: EmailStr
    source: str = "landing"

@router.post("/")
async def capture_lead(lead: LeadIn):
    """Save a demo-request lead to Supabase cargofi_leads table."""
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    if supabase_url and supabase_key:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(
                    f"{supabase_url}/rest/v1/cargofi_leads",
                    headers={
                        "apikey": supabase_key,
                        "Authorization": f"Bearer {supabase_key}",
                        "Content-Type": "application/json",
                        "Prefer": "return=minimal",
                    },
                    json={
                        "company": lead.company,
                        "email": lead.email,
                        "source": lead.source,
                    },
                )
                if resp.status_code not in (200, 201):
                    # Log but don't fail — still return success to user
                    print(f"[leads] Supabase error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[leads] Supabase exception: {e}")

    # Always return success — worst case lead goes to server log
    print(f"[leads] NEW LEAD — company={lead.company!r} email={lead.email!r} source={lead.source!r}")
    return JSONResponse({"ok": True})
