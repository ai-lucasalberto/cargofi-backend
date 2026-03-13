import base64
import json
import os
import re
from typing import Optional

import anthropic
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

router = APIRouter()

def get_client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured on server")
    return anthropic.Anthropic(api_key=key)

# ── Supported doc types ──────────────────────────────────────────────────────
DOC_SCHEMAS = {
    "bill_of_lading": {
        "label": "Bill of Lading (BOL)",
        "fields": [
            "shipper_name", "shipper_address",
            "consignee_name", "consignee_address",
            "notify_party",
            "bol_number", "booking_number",
            "vessel_voyage", "port_of_loading", "port_of_discharge",
            "place_of_delivery",
            "cargo_description", "hs_code",
            "quantity", "weight_kg", "volume_cbm",
            "freight_terms",        # prepaid / collect
            "issue_date",
        ]
    },
    "commercial_invoice": {
        "label": "Factura Comercial / Commercial Invoice",
        "fields": [
            "invoice_number", "invoice_date",
            "seller_name", "seller_address", "seller_tax_id",
            "buyer_name", "buyer_address", "buyer_tax_id",
            "incoterms", "payment_terms", "currency",
            "line_items",        # list of {description, hs_code, qty, unit_price, total}
            "subtotal", "taxes", "total_amount",
            "country_of_origin", "country_of_destination",
        ]
    },
    "packing_list": {
        "label": "Packing List",
        "fields": [
            "reference_number", "date",
            "shipper_name", "consignee_name",
            "packages",          # list of {box_no, description, qty, net_weight, gross_weight, dimensions}
            "total_packages", "total_net_weight_kg", "total_gross_weight_kg",
        ]
    },
    "carta_porte": {
        "label": "Carta Porte",
        "fields": [
            "folio_fiscal", "fecha_emision",
            "rfcEmisor", "nombreEmisor",
            "rfcReceptor", "nombreReceptor",
            "total_distance_km",
            "unit_type", "unit_plates", "operator_name", "operator_license",
            "origin_location", "origin_date",
            "destination_location", "estimated_arrival",
            "merchandise_description", "weight_kg", "quantity", "hs_code",
            "hazmat_flag",
        ]
    },
    "pedimento": {
        "label": "Pedimento Aduanal",
        "fields": [
            "pedimento_number", "aduana", "patente", "tipo_operacion",
            "fecha_pago", "fecha_entrada",
            "importador_exportador", "rfc",
            "total_value_usd", "total_taxes_mxn",
            "country_of_origin", "country_of_destination",
            "partidas",          # list of line items
        ]
    },
    "certificate_of_origin": {
        "label": "Certificado de Origen",
        "fields": [
            "certificate_number", "issue_date",
            "exporter_name", "exporter_address",
            "importer_name", "importer_address",
            "country_of_origin", "country_of_destination",
            "goods_description", "hs_code", "quantity", "value",
            "preference_criterion",    # e.g. USMCA
            "certifier_name", "certifier_signature",
        ]
    },
    "proof_of_delivery": {
        "label": "POD / Comprobante de Entrega",
        "fields": [
            "delivery_date", "delivery_time",
            "recipient_name", "recipient_signature",
            "delivery_address",
            "order_reference", "bol_reference",
            "items_received",    # list
            "condition_notes",
        ]
    },
}

EXTRACT_SYSTEM = """Eres un especialista en documentación aduanal y logística cross-border México–USA.
Analiza el documento adjunto y extrae la información de manera estructurada.

Responde ÚNICAMENTE con JSON válido. Sin markdown, sin texto adicional.
Si un campo no está presente en el documento, usa null.
Para listas vacías usa [].
Para campos de lista (line_items, packages, etc.) extrae todos los registros visibles."""

EXTRACT_PROMPT = """Documento a analizar: {doc_type_label}

Extrae los siguientes campos:
{fields_json}

Además incluye siempre:
- "doc_type": el tipo detectado de documento (usa el key en snake_case)
- "confidence": número del 0 al 1 indicando qué tan seguro estás de la lectura
- "language": "es", "en", o "bilingual"
- "warnings": lista de strings con observaciones (campos ambiguos, datos ilegibles, inconsistencias detectadas)

Responde con un objeto JSON plano (no anidado en ninguna clave raíz)."""

VALIDATE_SYSTEM = """Eres un experto en cumplimiento aduanal para operaciones cross-border México–USA.
Tu tarea es revisar un conjunto de documentos de un embarque y determinar si están completos y consistentes.
Responde ÚNICAMENTE con JSON válido. Sin markdown."""

VALIDATE_PROMPT = """Revisa los siguientes documentos de un embarque cross-border México–USA:

{docs_summary}

Tipo de operación: {operation_type}

Evalúa y responde con:
{{
  "ready_to_cross": true/false,
  "score": 0-100,
  "missing_docs": ["lista de documentos que faltan para este tipo de operación"],
  "issues": [
    {{
      "severity": "critical" | "warning" | "info",
      "field": "nombre del campo o documento",
      "message": "descripción del problema"
    }}
  ],
  "checklist": [
    {{
      "item": "descripción del requisito",
      "status": "ok" | "missing" | "warning",
      "detail": "nota adicional o null"
    }}
  ],
  "recommendations": ["lista de acciones recomendadas antes de cruzar"]
}}"""


def _file_to_b64_media(content: bytes, media_type: str) -> tuple[str, str]:
    """Return (base64_data, media_type) suitable for Claude vision."""
    if media_type == "application/pdf":
        b64 = base64.standard_b64encode(content).decode("utf-8")
        return b64, "application/pdf"
    b64 = base64.standard_b64encode(content).decode("utf-8")
    if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        media_type = "image/jpeg"
    return b64, media_type


def _detect_doc_type(filename: str, hint: Optional[str]) -> str:
    """Best-effort doc type from filename or hint."""
    if hint and hint in DOC_SCHEMAS:
        return hint
    name = (filename or "").lower()
    if "bol" in name or "bill" in name or "lading" in name:
        return "bill_of_lading"
    if "invoice" in name or "factura" in name:
        return "commercial_invoice"
    if "packing" in name or "empaque" in name:
        return "packing_list"
    if "carta" in name or "porte" in name:
        return "carta_porte"
    if "pedimento" in name:
        return "pedimento"
    if "origen" in name or "origin" in name:
        return "certificate_of_origin"
    if "pod" in name or "delivery" in name or "entrega" in name:
        return "proof_of_delivery"
    return "commercial_invoice"   # safe default


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/extract")
async def extract_document(
    file: UploadFile = File(...),
    doc_type_hint: Optional[str] = Form(None),
):
    """
    Upload a document (PDF or image) and extract structured fields using AI.
    """
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "Archivo demasiado grande. Máximo 20 MB.")

    media_type = file.content_type or "image/jpeg"
    b64_data, media_type = _file_to_b64_media(content, media_type)

    doc_type = _detect_doc_type(file.filename or "", doc_type_hint)
    schema = DOC_SCHEMAS.get(doc_type, DOC_SCHEMAS["commercial_invoice"])

    prompt = EXTRACT_PROMPT.format(
        doc_type_label=schema["label"],
        fields_json=json.dumps(schema["fields"], ensure_ascii=False, indent=2),
    )

    # Build content block (PDF vs image)
    if media_type == "application/pdf":
        doc_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data},
        }
    else:
        doc_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64_data},
        }

    try:
        response = get_client().messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=4096,
            system=EXTRACT_SYSTEM,
            messages=[{
                "role": "user",
                "content": [doc_block, {"type": "text", "text": prompt}]
            }]
        )
    except Exception as e:
        raise HTTPException(502, f"Error al llamar a Claude: {str(e)}")

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        extracted = json.loads(raw)
    except json.JSONDecodeError:
        extracted = {"raw_text": raw, "parse_error": True}

    extracted.setdefault("doc_type", doc_type)
    extracted.setdefault("doc_type_label", schema["label"])
    extracted.setdefault("filename", file.filename)

    return JSONResponse(content=extracted)


@router.post("/validate")
async def validate_shipment(
    docs: str = Form(...),          # JSON string: list of extracted docs
    operation_type: str = Form("import_mex_usa"),
):
    """
    Given a list of already-extracted documents, validate completeness
    and flag issues before crossing the border.
    """
    try:
        docs_list = json.loads(docs)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "docs debe ser un JSON array válido")

    docs_summary = json.dumps(
        [{
            "doc_type": d.get("doc_type"),
            "doc_type_label": d.get("doc_type_label"),
            "filename": d.get("filename"),
            "warnings": d.get("warnings", []),
            "confidence": d.get("confidence"),
        } for d in docs_list],
        ensure_ascii=False, indent=2
    )

    prompt = VALIDATE_PROMPT.format(
        docs_summary=docs_summary,
        operation_type=operation_type,
    )

    response = get_client().messages.create(
        model="claude-3-haiku-20240307",
        max_tokens=2048,
        system=VALIDATE_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"raw_text": raw, "parse_error": True}

    return JSONResponse(content=result)


@router.get("/types")
async def get_doc_types():
    """Return supported document types."""
    return {k: v["label"] for k, v in DOC_SCHEMAS.items()}


@router.get("/health")
async def health():
    """Health check — verify API key is loaded."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return {
        "status": "ok",
        "api_key_set": bool(key),
        "api_key_prefix": key[:10] + "..." if key else None,
    }
