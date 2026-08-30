"""
Orquestrador FastAPI da integração Homes <-> Sienge.

Executar localmente:
    uvicorn app.main:app --reload
"""
import logging


from routers import debug_router, webhook_router

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Integração Homes ↔ Sienge",
    description="Middleware que recebe eventos do Homes e orquestra as chamadas ao Sienge.",
    version="0.1.0",
)

app.include_router(webhook_router.router)
app.include_router(debug_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Intercepta erros de validação de schema (antes de chegar no endpoint)
    e devolve no mesmo formato padronizado usado no webhook_router.py,
    em vez do {"detail": [...]} padrão do FastAPI.
    """
    logging.warning("Payload inválido em %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "http_status": 422,
            "step": "validacao_schema_payload",
            "message": "O payload recebido não está no formato esperado pelo endpoint.",
            "data": None,
            "error": {"detalhes": exc.errors()},
        },
    )