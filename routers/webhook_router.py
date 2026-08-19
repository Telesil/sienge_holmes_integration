"""
Endpoint que recebe o webhook disparado pelo Homes na transição da tarefa
"Central de Lançamento | Registrar NF", e cadastra o passo 1 da nota
fiscal de compra no Sienge (POST /v1/purchase-invoices).

Schema confirmado via /webhooks/debug/echo em 18/08 (payload real do Homes).
"""
import logging
import random
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from drivers.sienge_driver import SiengeDriver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/homes", tags=["homes"])


class HomesAuthor(BaseModel):
    id: str
    name: str
    email: str


class HomesDocument(BaseModel):
    process_document_id: str
    name: str
    document_id: Optional[str] = None
    status: str


class HomesTaskWebhook(BaseModel):
    """
    Payload real observado no /webhooks/debug/echo.
    `properties` hoje só traz "CPF/CNPJ" e "Valor da Nota" — os demais
    campos exigidos pelo Sienge (number, series, companyId,
    movementTypeId) ainda não estão configurados no processo do Homes.
    """
    id: str
    author: HomesAuthor
    created_at: Optional[str] = None
    properties: Dict[str, Any]
    documents: List[HomesDocument] = []

    def cnpj(self) -> str:
        valor = self.properties.get("CPF/CNPJ")
        if not valor:
            raise ValueError("Campo 'CPF/CNPJ' não encontrado em properties.")
        return valor


@router.post("/registrar-nf")
def registrar_nota_fiscal(payload: HomesTaskWebhook):
    """
    Passo 1 apenas: cadastra os dados principais da nota fiscal de compra
    no Sienge (POST /v1/purchase-invoices).
    """
    sienge = SiengeDriver()

    try:

        supplier_id = sienge.resolver_supplier_id(payload.cnpj())
        author = payload.author

        # TODO: os campos abaixo ainda não vêm no payload do Homes.
        # Precisam ser adicionados como properties no processo, ou
        # combinados (ex.: companyId fixo por instância/subdomínio).

        print(payload)
        nf_payload = {
            "documentId": "NF",  # TODO: obter essa informação no fluxo
            "number": f"{random.randint(1000, 9999)}",  # TODO: obter essa informação no fluxo
            "supplierId": supplier_id,
            "companyId": 1,  # TODO: obter a informação de um campo único do payload, sendo ele o centro de custo ou nome da obra, após ler ele, criar uma função para buscar a empresa dele
            "notes": (   #TODO: isso pode ser personalizado para preencher da forma desejada
                f"Lançamento via integração Homes | "
                f"Processo: {payload.id} | "
                f"Solicitante: {author.name} | "
                f"E-mail: {author.email} | "
                f"Usuário: {author.id}"
            ),
        }

        campos_faltando = [k for k, v in nf_payload.items() if v in (None, "")]
        if campos_faltando:
            raise ValueError(
                f"Campos obrigatórios ausentes no payload do Homes: {campos_faltando}"
            )

        resultado = sienge.cadastrar_nota_fiscal_compra(nf_payload)
        return {"status": "ok", "resultado": resultado}

    except ValueError as exc:
        logger.warning("Payload da tarefa %s incompleto: %s", payload.id, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except Exception as exc:  # noqa: BLE001
        logger.exception("Falha ao registrar NF no Sienge (tarefa %s)", payload.id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc