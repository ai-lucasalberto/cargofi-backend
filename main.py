import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.docs import router as docs_router
from api.leads import router as leads_router

logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s — %(message)s")

app = FastAPI(title="CargoFi API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(docs_router, prefix="/api/docs")
app.include_router(leads_router, prefix="/api/leads")

@app.get("/")
async def root():
    return {"service": "CargoFi API", "version": "0.1.0"}
