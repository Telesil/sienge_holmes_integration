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

Tratamento de erros:
  Cada etapa roda dentro de `_etapa(...)`, que converte qualquer exceção
  (HTTP, indisponibilidade, validação ou inesperada) em um
  `EtapaIntegracaoError` com step, status HTTP, mensagem amigável e
  detalhe técnico. Assim nenhuma falha "vaza" como 500 sem corpo, e o
  Homes sempre recebe o retorno quando a atividade já foi localizada.

Schema do webhook confirmado via /webhooks/debug/echo em 18/08 (payload
real do Homes).
"""
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from drivers.base_driver import ApiUnavailableError, AuthenticationError, RateLimitError
from drivers.holmes_driver import HomesDriver
from drivers.sienge_driver import FornecedorNaoEncontradoError, SiengeDriver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/homes", tags=["homes"])


# ----------------------------------------------------------------------
# Schemas
# ----------------------------------------------------------------------

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

    @staticmethod
    def _normalizar_chave(chave: str) -> str:
        """Ignora maiúsculas/minúsculas e espaços (ex.: 'Centro de Custo/ Obra' == 'centro de custo/obra')."""
        return "".join(str(chave).split()).lower()

    def buscar_campo(self, nome_campo: str, *alternativos: str) -> str:
        """
        Busca o valor de um campo do processo. Se o nome principal não
        existir ou estiver vazio, tenta os nomes alternativos na ordem.
        Por último, tenta casar os nomes ignorando espaços e maiúsculas.
        """
        nomes = [nome_campo, *alternativos]

        # 1) Busca exata, na ordem informada
        for nome in nomes:
            valor = self.properties.get(nome)
            if valor is not None and str(valor).strip():
                return str(valor).strip()

        # 2) Busca tolerante (sem espaços / case-insensitive)
        alvos = {self._normalizar_chave(n) for n in nomes}
        for chave, valor in self.properties.items():
            if self._normalizar_chave(chave) in alvos and valor is not None and str(valor).strip():
                logger.info("Campo '%s' encontrado pela chave '%s'.", nome_campo, chave)
                return str(valor).strip()

        tentativas = ", ".join(f"'{n}'" for n in nomes)
        raise ValueError(
            f"Campo '{nome_campo}' não preenchido no processo do Homes. "
            f"(nomes consultados: {tentativas})"
        )


# ----------------------------------------------------------------------
# Erro padronizado de etapa
# ----------------------------------------------------------------------

class EtapaIntegracaoError(Exception):
    """
    Falha em uma etapa da integração, já traduzida.

    message: texto curto e acionável — é o que o usuário vê no Homes.
    error:   detalhe técnico (status, corpo da resposta, exceção) — vai
             para o log e para a resposta HTTP do webhook.
    """

    def __init__(self, step: str, http_status: int, message: str,
                 error: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.step = step
        self.http_status = http_status
        self.message = message
        self.error = error or {}

    def como_resultado(self) -> Dict[str, Any]:
        return dict(
            http_status=self.http_status,
            success=False,
            step=self.step,
            message=self.message,
            data=None,
            error=self.error,
        )


_AVISO_INDISPONIVEL = " O sistema parece estar indisponível no momento — tente novamente em alguns minutos."


def _ler_corpo(response: Optional[requests.Response]) -> Any:
    """Corpo da resposta como JSON quando possível, senão texto."""
    if response is None:
        return None
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return response.text


def _mensagem_do_sistema(corpo: Any, fallback: str) -> str:
    """
    Reaproveita a mensagem de negócio que o Sienge/Homes já devolvem
    (clientMessage, depois developerMessage/message) — normalmente já é
    específica e legível (ex.: "nota fiscal X já cadastrada para o credor Y").
    """
    if isinstance(corpo, dict):
        for chave in ("clientMessage", "developerMessage", "message"):
            if corpo.get(chave):
                return str(corpo[chave])
    return fallback


@contextmanager
def _etapa(step: str, sistema: str, mensagem_falha: str,
           http_status_validacao: int = 422) -> Iterator[None]:
    """
    Executa um bloco convertendo qualquer exceção em EtapaIntegracaoError.

    Args:
        step: identificador da etapa (vai para resposta e log).
        sistema: "sienge" ou "homes" — usado nas chaves do detalhe técnico.
        mensagem_falha: mensagem amigável usada quando o sistema externo
            não devolve uma própria.
        http_status_validacao: status para erros de dado/validação.
    """
    try:
        yield
    except EtapaIntegracaoError:
        raise
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        corpo = _ler_corpo(exc.response)
        mensagem = _mensagem_do_sistema(corpo, mensagem_falha)
        if isinstance(status, int) and status >= 500:
            mensagem += _AVISO_INDISPONIVEL
        logger.error("[%s] %s respondeu %s — %s", step, sistema, status, corpo)
        raise EtapaIntegracaoError(
            step, 502, mensagem,
            {f"{sistema}_status": status, f"{sistema}_response": corpo},
        ) from exc
    except RateLimitError as exc:
        logger.error("[%s] limite de requisições do %s — %s", step, sistema, exc)
        raise EtapaIntegracaoError(
            step, 503,
            f"{mensagem_falha} O {sistema.capitalize()} está limitando as requisições no momento — "
            f"tente novamente em alguns minutos.",
            {f"{sistema}_status": exc.ultimo_status, "motivo": exc.motivo, "detail": str(exc)},
        ) from exc
    except ApiUnavailableError as exc:
        logger.error("[%s] %s indisponível — %s", step, sistema, exc)
        raise EtapaIntegracaoError(
            step, 503, mensagem_falha + _AVISO_INDISPONIVEL,
            {f"{sistema}_status": exc.ultimo_status, "motivo": exc.motivo, "detail": str(exc)},
        ) from exc
    except AuthenticationError as exc:
        logger.error("[%s] credenciais do %s ausentes/inválidas — %s", step, sistema, exc)
        raise EtapaIntegracaoError(
            step, 500,
            "A integração está sem credenciais válidas. Acione o time responsável pela integração.",
            {"detail": str(exc)},
        ) from exc
    except ValueError as exc:
        # Erros de dado: a mensagem da exceção já é legível para o usuário.
        logger.warning("[%s] dado inválido — %s", step, exc)
        raise EtapaIntegracaoError(
            step, http_status_validacao, str(exc),
            {"detail": str(exc), "tipo": type(exc).__name__},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] falha inesperada", step)
        raise EtapaIntegracaoError(
            step, 500,
            f"{mensagem_falha} Ocorreu um erro inesperado na integração.",
            {"detail": str(exc), "tipo": type(exc).__name__},
        ) from exc


# ----------------------------------------------------------------------
# Registro da nota no Sienge
# ----------------------------------------------------------------------

def _registrar_nota_fiscal(payload: HomesTaskWebhook) -> Dict[str, Any]:
    """
    Executa o cadastro da nota fiscal no Sienge (passo 1).
    Levanta EtapaIntegracaoError em qualquer falha.
    """
    # --- Etapa 1: ler campos obrigatórios do processo ---
    with _etapa("validacao_campos_homes", "homes",
                "Algum campo obrigatório do processo está ausente."):
        documento_fornecedor = payload.buscar_campo("CPF/CNPJ")
        centro_custo = payload.buscar_campo(
            "Centro de Custo/ Obra",
            "Centro de Custo"
        )
        number = payload.buscar_campo("Número da Nota")
        tipo_nota = payload.buscar_campo("Tipo de Nota")

    with _etapa("conexao_sienge", "sienge", "Não foi possível iniciar a conexão com o Sienge."):
        sienge = SiengeDriver()

    # --- Etapa 2: traduzir tipo de nota e empresa (tabelas locais) ---
    with _etapa("resolucao_tipo_nota", "sienge", "Tipo de nota inválido."):
        document_id = sienge.resolver_document_id(tipo_nota)

    with _etapa("identificacao_empresa", "sienge",
                "Não foi possível identificar a empresa pelo centro de custo."):
        company_id = sienge.identificar_empresa(centro_custo)

    # --- Etapa 3: resolver fornecedor (tenta com e sem zero à esquerda) ---
    with _etapa("consulta_fornecedor_sienge", "sienge",
                "Não foi possível consultar o fornecedor no Sienge."):
        try:
            supplier_id = sienge.resolver_supplier_id(documento_fornecedor)
        except FornecedorNaoEncontradoError as exc:
            raise EtapaIntegracaoError(
                "consulta_fornecedor_sienge", 422,
                f"Fornecedor com CPF/CNPJ {exc.documento} não está cadastrado no Sienge. "
                f"Verifique o documento no processo ou cadastre o credor e reenvie.",
                {"documento": exc.documento, "valores_consultados": exc.tentativas},
            ) from exc

    author = payload.author
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
        raise EtapaIntegracaoError(
            "validacao_payload_sienge", 422,
            "Faltam informações obrigatórias para cadastrar a nota fiscal no Sienge.",
            {"campos_faltando": campos_faltando},
        )

    # --- Etapa 4: cadastrar no Sienge ---
    with _etapa("cadastro_nota_fiscal_sienge", "sienge",
                "O Sienge recusou o cadastro da nota fiscal."):
        resultado = sienge.cadastrar_nota_fiscal_compra(nf_payload)

    logger.info("Tarefa %s: nota fiscal cadastrada com sucesso — %s", payload.id, resultado)
    return dict(
        http_status=200,
        success=True,
        step="cadastro_nota_fiscal_sienge",
        message="Nota fiscal cadastrada com sucesso no Sienge.",
        data={"sienge": resultado, "nf_payload_enviado": nf_payload},
        error=None,
    )


def _tentar_registrar_nota_fiscal(payload: HomesTaskWebhook) -> Dict[str, Any]:
    """Wrapper que nunca levanta: sempre devolve o dict de resultado."""
    try:
        return _registrar_nota_fiscal(payload)
    except EtapaIntegracaoError as exc:
        logger.warning("Tarefa %s: falha na etapa %s — %s | %s",
                       payload.id, exc.step, exc.message, exc.error)
        return exc.como_resultado()
    except Exception as exc:  # noqa: BLE001 — rede de segurança final
        logger.exception("Tarefa %s: erro não tratado no registro da nota", payload.id)
        return EtapaIntegracaoError(
            "erro_interno", 500,
            "Ocorreu um erro inesperado na integração com o Sienge.",
            {"detail": str(exc), "tipo": type(exc).__name__},
        ).como_resultado()


# ----------------------------------------------------------------------
# Mensagem gravada no Homes
# ----------------------------------------------------------------------

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
    mensagem = resultado.get("message") or "Ocorreu um erro ao processar a integração com o Sienge."
    return f"Erro: {mensagem}{ref}"


def _anexar_falha_homes(resultado: Dict[str, Any], detalhe: Dict[str, Any]) -> None:
    """Sinaliza no resultado que a gravação de volta no Homes falhou."""
    erro = resultado.get("error") or {}
    if not isinstance(erro, dict):
        erro = {"detalhe_original": erro}
    resultado["error"] = {**erro, "homes_persist_falhou": True, **detalhe}


def _resposta(status_code: int, **kwargs) -> JSONResponse:
    """Monta o corpo padronizado e devolve com o HTTP status correspondente."""
    body = IntegrationResponse(http_status=status_code, **kwargs)
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _resposta_de(resultado: Dict[str, Any]) -> JSONResponse:
    return _resposta(
        resultado["http_status"],
        success=resultado["success"],
        step=resultado["step"],
        message=resultado["message"],
        data=resultado.get("data"),
        error=resultado.get("error"),
    )


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------

@router.post("/registrar-nf", response_model=IntegrationResponse)
def registrar_nota_fiscal(payload: HomesTaskWebhook):
    # --- Etapa 0: buscar o processo e a atividade de integração no Homes ---
    # Se falhar aqui, não há onde gravar o retorno no Homes — só respondemos.
    try:
        with _etapa("consulta_processo_homes", "homes",
                    "Não foi possível consultar o processo no Homes."):
            homes = HomesDriver()
            processo = homes.consultar_processo(payload.id)

        with _etapa("busca_atividade_homes", "homes",
                    "Não foi possível localizar a etapa de integração dentro do processo."):
            id_atividade = homes.fluxo_notas(processo or {})
    except EtapaIntegracaoError as exc:
        logger.error("Tarefa %s: %s — %s", payload.id, exc.message, exc.error)
        return _resposta_de(exc.como_resultado())

    if not id_atividade:
        logger.warning("Tarefa %s: atividade de integração não encontrada no processo.", payload.id)
        return _resposta(
            404,
            success=False,
            step="busca_atividade_homes",
            message="Atividade de integração não encontrada entre as atividades atuais do processo.",
            error={"processo_id": payload.id},
        )

    # --- Etapas 1-4: tentar registrar a nota fiscal no Sienge (nunca levanta) ---
    resultado_registro = _tentar_registrar_nota_fiscal(payload)

    # --- Etapa 5: persistir o resultado (sucesso ou erro) de volta no Homes ---
    try:
        with _etapa("persistir_resultado_homes", "homes",
                    "Não foi possível gravar o resultado no Homes."):
            homes.persistir_resultado(id_atividade, _mensagem_para_homes(resultado_registro, payload.id))
    except EtapaIntegracaoError as exc:
        # A tentativa no Sienge já ocorreu; não sobrescrevemos esse
        # resultado, só anexamos que o retorno ao Homes falhou.
        logger.error("Tarefa %s: falha ao persistir resultado no Homes (atividade %s) — %s",
                     payload.id, id_atividade, exc.error)
        _anexar_falha_homes(resultado_registro, exc.error)

    return _resposta_de(resultado_registro)