"""
Endpoint de debug — sem validação de schema.

Objetivo: confirmar que a conexão (ngrok → FastAPI) está funcionando e
inspecionar exatamente o que está sendo enviado (headers + corpo), antes
de travar o contrato de dados com pydantic no webhook_router.py real.

Depois que o payload real do Homes estiver confirmado aqui, ajuste o
`HomesTaskWebhook` em webhook_router.py para bater com os campos vistos.
"""
import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/debug", tags=["debug"])


@router.api_route("/echo", methods=["GET", "POST", "PUT", "PATCH"])
async def echo(request: Request):
    """
    Aceita qualquer método/corpo, não valida nada, e devolve o que
    recebeu. Use esta URL no Homes (ou no Postman) só para testar a
    conectividade e ver o formato real do payload.
    """
    headers = dict(request.headers)
    raw_body = await request.body()

    # Tenta decodificar como JSON; se não for, devolve como texto puro.
    try:
        body_json = await request.json()
        body_repr = body_json
    except Exception:
        body_repr = raw_body.decode("utf-8", errors="replace")

    logger.info("== /webhooks/debug/echo recebido ==")
    logger.info("Method: %s", request.method)
    logger.info("Headers: %s", headers)
    logger.info("Body: %s", body_repr)

    return {
        "status": "ok",
        "message": "Conexão recebida com sucesso.",
        "method": request.method,
        "headers": headers,
        "body": body_repr,
    }