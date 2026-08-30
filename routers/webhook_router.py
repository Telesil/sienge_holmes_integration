"""
Endpoint que recebe o webhook disparado pelo Homes na transição da tarefa
"Central de Lançamento | Registrar NF".

Fluxo completo:
  1. Consulta o processo no Homes (consultar_processo) para obter as
     atividades atuais.
  2. Localiza, dentro do processo, a atividade de integração
     (fluxo_notas) — é nela que o resultado será gravado de volta.
  3. Se a atividade for encontrada, tenta cadastrar o passo 1 da nota
     fiscal de compra no Sienge (POST /v1/purchase-invoices).
  4. Independente do resultado (sucesso ou erro), grava o que aconteceu
     de volta na atividade do Homes via persistir_resultado, para que o
     time acompanhe o status sem sair da ferramenta.

Schema do webhook confirmado via /webhooks/debug/echo em 18/08 (payload
real do Homes).
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from drivers.holmes_driver import HomesDriver
from drivers.sienge_driver import SiengeDriver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/homes", tags=["homes"])


class IntegrationResponse(BaseModel):
    success: bool
    http_status: int
    step: str
    message: str
    data: Optional[dict] = None
    error: Optional[dict] = None


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
    id: str
    author: HomesAuthor
    created_at: Optional[str] = None
    properties: Dict[str, Any]
    documents: List[HomesDocument] = []

    def buscar_campo(self, nome_campo: str) -> str:
        valor = self.properties.get(nome_campo)
        if not valor:
            raise ValueError(f"Campo {nome_campo} não encontrado em properties.")
        return valor


def _resposta(status_code: int, **kwargs) -> JSONResponse:
    """Monta o corpo padronizado e devolve com o HTTP status correspondente."""
    body = IntegrationResponse(http_status=status_code, **kwargs)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _ler_erro_sienge(response: Optional[requests.Response], fallback: str) -> Tuple[Any, str]:
    """
    Lê o corpo de uma resposta de erro do Sienge e tenta extrair a
    mensagem de negócio que ele já devolve (clientMessage, e em segundo
    lugar developerMessage) — normalmente já é específica e legível
    (ex.: "nota fiscal X já cadastrada para o credor Y"), então é melhor
    reaproveitá-la do que reescrever uma mensagem genérica por status
    HTTP.

    Retorna (corpo_bruto, mensagem) — corpo_bruto vai pro campo `error`
    da resposta/log (debug completo), mensagem vai pro que o
    usuário/Homes vê.
    """
    corpo: Any = None
    if response is not None:
        try:
            corpo = response.json()
        except Exception:
            corpo = response.text

    mensagem = fallback
    if isinstance(corpo, dict):
        mensagem = corpo.get("clientMessage") or corpo.get("developerMessage") or fallback

    return corpo, mensagem


def _tentar_registrar_nota_fiscal(sienge: SiengeDriver, payload: HomesTaskWebhook) -> Dict[str, Any]:
    """
    Executa o cadastro da nota fiscal no Sienge (passo 1).

    Retorna um dict com http_status/success/step/message/data/error,
    usado tanto para montar a resposta HTTP do webhook quanto para
    compor o valor que será persistido de volta no Homes.
    """
    # --- Etapa 1: resolver fornecedor pelo CNPJ e empresa pelo centro de custo ---
    try:
        supplier_id = sienge.resolver_supplier_id(payload.buscar_campo("CPF/CNPJ"))
        company_id = sienge.identificar_empresa(payload.buscar_campo("Centro de Custo"))
        number = payload.buscar_campo("Número da Nota")
        document_id = sienge.resolver_document_id(payload.buscar_campo("Tipo de Nota"))

    except ValueError as exc:
        logger.warning("Tarefa %s: campo ausente/inválido — %s", payload.id, exc)
        return dict(
            http_status=422,
            success=False,
            step="validacao_cnpj",
            message="Algum campo obrigatório (CPF/CNPJ, empresa, número ou tipo de nota) está ausente ou inválido no processo do Homes.",
            data=None,
            error={"detail": str(exc)},
        )
    except requests.HTTPError as exc:
        status_sienge = exc.response.status_code if exc.response is not None else 502
        corpo_sienge, mensagem = _ler_erro_sienge(
            exc.response, "Não foi possível consultar o fornecedor no Sienge."
        )
        logger.error(
            "Tarefa %s: falha ao consultar fornecedor no Sienge (status %s) — %s",
            payload.id, status_sienge, corpo_sienge,
        )
        return dict(
            http_status=502,
            success=False,
            step="consulta_fornecedor_sienge",
            message=mensagem,
            data=None,
            error={"sienge_status": status_sienge, "sienge_response": corpo_sienge},
        )

    author = payload.author
    # --- Etapa 2: montar payload e validar campos obrigatórios ---
    nf_payload = {
        "documentId": document_id,
        "number": number,
        "supplierId": supplier_id,
        "companyId": company_id,
        "notes": (
            f"Lançamento via integração Homes | "
            f"Processo: {payload.id} | "
            f"Solicitante: {author.name} | "
            f"E-mail: {author.email} | "
            f"Usuário: {author.id}"
        ),
    }

    campos_faltando = [k for k, v in nf_payload.items() if v in (None, "")]
    if campos_faltando:
        logger.warning("Tarefa %s: campos faltando — %s", payload.id, campos_faltando)
        return dict(
            http_status=422,
            success=False,
            step="validacao_payload_sienge",
            message="Faltam dados obrigatórios para cadastrar a nota no Sienge.",
            data=None,
            error={"campos_faltando": campos_faltando},
        )

    # --- Etapa 3: cadastrar no Sienge ---
    try:
        resultado = sienge.cadastrar_nota_fiscal_compra(nf_payload)
    except requests.HTTPError as exc:
        status_sienge = exc.response.status_code if exc.response is not None else 502
        corpo_sienge, mensagem = _ler_erro_sienge(
            exc.response, "O Sienge recusou o cadastro da nota fiscal."
        )

        logger.error(
            "Tarefa %s: Sienge recusou o cadastro (status %s) — %s",
            payload.id, status_sienge, corpo_sienge,
        )
        return dict(
            http_status=502,
            success=False,
            step="cadastro_nota_fiscal_sienge",
            message=mensagem,
            data=None,
            error={"sienge_status": status_sienge, "sienge_response": corpo_sienge},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tarefa %s: falha inesperada ao cadastrar no Sienge", payload.id)
        return dict(
            http_status=502,
            success=False,
            step="cadastro_nota_fiscal_sienge",
            message="Erro inesperado ao tentar cadastrar a nota no Sienge.",
            data=None,
            error={"detail": str(exc)},
        )

    # --- Sucesso ---
    logger.info("Tarefa %s: nota fiscal cadastrada com sucesso — %s", payload.id, resultado)
    return dict(
        http_status=200,
        success=True,
        step="cadastro_nota_fiscal_sienge",
        message="Nota fiscal cadastrada com sucesso no Sienge.",
        data={"sienge": resultado, "nf_payload_enviado": nf_payload},
        error=None,
    )


# Mensagens amigáveis por step — o que o usuário vê no Homes não deve
# trazer status HTTP, URLs ou payloads técnicos, só uma explicação
# acionável. O detalhe técnico completo continua nos logs e na resposta
# HTTP do webhook (para debug e suporte).
_MENSAGENS_AMIGAVEIS: Dict[str, str] = {
    "consulta_processo_homes": "Não foi possível consultar os dados do processo no Homes.",
    "busca_atividade_homes": "Não foi possível localizar a etapa de integração dentro do processo.",
    "validacao_cnpj": "Algum campo obrigatório do processo está ausente ou em formato inválido "
                      "(CPF/CNPJ, empresa, número ou tipo de nota). Verifique os campos e reenvie.",
    "validacao_payload_sienge": "Faltam informações obrigatórias para cadastrar a nota fiscal no Sienge.",
    # consulta_fornecedor_sienge e cadastro_nota_fiscal_sienge não
    # entram aqui de propósito: usam a mensagem real devolvida pelo
    # Sienge (ver _STEPS_COM_MENSAGEM_SIENGE). Este dict só serve de
    # fallback caso, por algum motivo, essa mensagem não venha.
}


# Steps em que resultado["message"] já é a mensagem de negócio devolvida
# pelo próprio Sienge (via _ler_erro_sienge) — nesses casos, usar ela
# direto é melhor do que reescrever um texto genérico.
_STEPS_COM_MENSAGEM_SIENGE = {"consulta_fornecedor_sienge", "cadastro_nota_fiscal_sienge"}


def _mensagem_amigavel_erro(resultado: Dict[str, Any]) -> str:
    """Traduz o resultado técnico de uma falha numa frase curta e acionável."""
    erro = resultado.get("error") or {}
    status_sienge = erro.get("sienge_status") if isinstance(erro, dict) else None

    if resultado["step"] in _STEPS_COM_MENSAGEM_SIENGE and resultado.get("message"):
        base = resultado["message"]
    else:
        base = _MENSAGENS_AMIGAVEIS.get(
            resultado["step"],
            "Ocorreu um erro ao processar a integração com o Sienge.",
        )

    # Erro 5xx normalmente é indisponibilidade do Sienge, não uma regra
    # de negócio — vale complementar mesmo quando já veio uma mensagem
    # específica, porque nesse caso o clientMessage tende a não existir
    # ou não ser útil.
    if isinstance(status_sienge, int) and status_sienge >= 500:
        base += " O Sienge parece estar indisponível no momento — tente novamente em alguns minutos."

    return base


def _mensagem_para_homes(resultado: Dict[str, Any], tarefa_id: Optional[str] = None) -> str:
    """Resume o resultado da integração num texto curto para gravar no Homes."""
    if resultado["success"]:
        sienge_id = None
        if resultado.get("data"):
            sienge_data = resultado["data"].get("sienge")
            if isinstance(sienge_data, dict):
                sienge_id = sienge_data.get("id") or sienge_data.get("sequentialNumber")
        sufixo = f" (id Sienge: {sienge_id})" if sienge_id else ""
        return f"Sucesso: {resultado['message']}{sufixo}"

    ref = f" (ref: {tarefa_id})" if tarefa_id else ""
    return f"Erro: {_mensagem_amigavel_erro(resultado)}{ref}"


@router.post("/registrar-nf", response_model=IntegrationResponse)
def registrar_nota_fiscal(payload: HomesTaskWebhook):
    homes = HomesDriver()

    # --- Etapa 0: buscar o processo no Homes ---
    try:
        processo = homes.consultar_processo(payload.id)
    except requests.HTTPError as exc:
        status_homes = exc.response.status_code if exc.response is not None else 502
        logger.error("Tarefa %s: falha ao consultar processo no Homes — %s", payload.id, exc)
        return _resposta(
            502,
            success=False,
            step="consulta_processo_homes",
            message="Não foi possível consultar o processo no Homes.",
            error={"homes_status": status_homes, "detail": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tarefa %s: falha inesperada ao consultar processo no Homes", payload.id)
        return _resposta(
            502,
            success=False,
            step="consulta_processo_homes",
            message="Erro inesperado ao consultar o processo no Homes.",
            error={"detail": str(exc)},
        )

    # --- Etapa 0.1: localizar a atividade de integração dentro do processo ---
    id_atividade = homes.fluxo_notas(processo)

    if not id_atividade:
        logger.warning("Tarefa %s: atividade de integração não encontrada no processo.", payload.id)
        return _resposta(
            404,
            success=False,
            step="busca_atividade_homes",
            message="Atividade de integração não encontrada entre as atividades atuais do processo.",
            error={"processo_id": payload.id},
        )

    # --- Etapas 1-3: tentar registrar a nota fiscal no Sienge ---
    sienge = SiengeDriver()
    resultado_registro = _tentar_registrar_nota_fiscal(sienge, payload)

    # --- Etapa 4: persistir o resultado (sucesso ou erro) de volta no Homes ---
    try:
        homes.persistir_resultado(id_atividade, _mensagem_para_homes(resultado_registro, payload.id))
    except Exception:  # noqa: BLE001
        logger.exception(
            "Tarefa %s: falha ao persistir resultado no Homes (atividade %s).",
            payload.id, id_atividade,
        )
        # A tentativa no Sienge já ocorreu (com sucesso ou erro); não
        # sobrescrevemos esse resultado, só sinalizamos que o retorno ao
        # Homes falhou, para facilitar o diagnóstico.
        erro = resultado_registro.get("error") or {}
        if isinstance(erro, dict):
            erro = {**erro, "homes_persist_falhou": True}
        else:
            erro = {"detalhe_original": erro, "homes_persist_falhou": True}
        resultado_registro["error"] = erro

    return _resposta(
        resultado_registro["http_status"],
        success=resultado_registro["success"],
        step=resultado_registro["step"],
        message=resultado_registro["message"],
        data=resultado_registro.get("data"),
        error=resultado_registro.get("error"),
    )