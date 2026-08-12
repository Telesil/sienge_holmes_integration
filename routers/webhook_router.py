"""
Endpoint que recebe o webhook disparado pelo Homes na transição de uma
tarefa, consulta/cadastra no Sienge, e retorna a resposta preenchendo
campos e avançando a tarefa no Homes.

Fluxo (ver diagrama enviado): Homes → webhook → middleware (este serviço)
→ Sienge (GET/POST) → middleware decide → Homes (preenche campos e avança).
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from drivers.holmes_driver import HomesDriver
from drivers.sienge_driver import SiengeDriver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/homes", tags=["homes"])


class HomesTaskWebhook(BaseModel):
    """
    Payload recebido do Homes na transição de tarefa.

    TODO: ajustar os campos abaixo assim que o mapeamento de fluxos e o
    contrato real do webhook do Homes estiverem confirmados.
    """
    tarefa_id: str
    cnpj: str


@router.post("/task")
def processar_task(payload: HomesTaskWebhook):
    """
    Recebe o evento do Homes, verifica/cadastra o credor no Sienge e
    atualiza a tarefa no Homes com o resultado.
    """
    sienge = SiengeDriver()
    homes = HomesDriver()

    try:
        credor = sienge.buscar_credor_por_cnpj(payload.cnpj)

        if not credor:
            # TODO: confirmar se o Sienge expõe endpoint de cadastro ou
            # se será necessário RPA (ver ponto de atenção da reunião).
            credor = sienge.cadastrar_credor({"cnpj": payload.cnpj})

        resultado = homes.preencher_campos_e_avancar(
            payload.tarefa_id, {"sienge_credor": credor}
        )
        return {"status": "ok", "resultado": resultado}

    except Exception as exc:  # noqa: BLE001 — logar e devolver erro tratável
        logger.exception("Falha ao processar tarefa %s", payload.tarefa_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
