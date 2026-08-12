"""
Orquestrador FastAPI da integração Homes <-> Sienge.

Executar localmente:
    uvicorn app.main:app --reload
"""
import logging

from fastapi import FastAPI

from routers import webhook_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Integração Homes ↔ Sienge",
    description="Middleware que recebe eventos do Homes e orquestra as chamadas ao Sienge.",
    version="0.1.0",
)

app.include_router(webhook_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
