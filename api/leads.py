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


async def _send_lead_notification(company: str, email: str, source: str):
    """Send email notification via Resend when a new lead arrives."""
    resend_key = os.environ.get("RESEND_API_KEY", "")
    notify_email = os.environ.get("NOTIFY_EMAIL", "0xlupuz@gmail.com")

    if not resend_key:
        print("[leads] RESEND_API_KEY not set — skipping email notification")
        return

    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <h2 style="color: #1e293b; margin-bottom: 4px;">🚛 Nuevo lead en CargoFi</h2>
      <p style="color: #64748b; margin-top: 0;">Alguien solicitó el piloto desde la landing page.</p>
      <table style="width: 100%; border-collapse: collapse; margin-top: 24px;">
        <tr>
          <td style="padding: 12px 16px; background: #f8fafc; border-radius: 8px 8px 0 0; font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">Empresa</td>
        </tr>
        <tr>
          <td style="padding: 12px 16px; font-size: 18px; font-weight: 700; color: #0f172a; border-bottom: 1px solid #e2e8f0;">{company}</td>
        </tr>
        <tr>
          <td style="padding: 12px 16px; background: #f8fafc; font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">Correo</td>
        </tr>
        <tr>
          <td style="padding: 12px 16px; font-size: 16px; color: #2563eb;">{email}</td>
        </tr>
        <tr>
          <td style="padding: 12px 16px; background: #f8fafc; font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">Fuente</td>
        </tr>
        <tr>
          <td style="padding: 12px 16px; font-size: 14px; color: #475569; border-radius: 0 0 8px 8px;">{source}</td>
        </tr>
      </table>
      <p style="margin-top: 32px; font-size: 12px; color: #94a3b8;">
        CargoFi · cargofi.io
      </p>
    </div>
    """

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": "CargoFi <notifications@cargofi.io>",
                    "to": [notify_email],
                    "subject": f"🚛 Nuevo lead: {company}",
                    "html": html,
                },
            )
            if resp.status_code in (200, 201):
                print(f"[leads] Email notification sent to {notify_email}")
            else:
                print(f"[leads] Resend error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[leads] Email notification exception: {e}")


@router.post("/")
async def capture_lead(lead: LeadIn):
    """Save a demo-request lead to Supabase and notify via email."""
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
                    print(f"[leads] Supabase error {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[leads] Supabase exception: {e}")

    # Fire email notification (non-blocking failure)
    await _send_lead_notification(lead.company, lead.email, lead.source)

    print(f"[leads] NEW LEAD — company={lead.company!r} email={lead.email!r} source={lead.source!r}")
    return JSONResponse({"ok": True})
