"""
HomesDriver — conexão autenticada com a API do Homes.

Autenticação: Bearer Token (usuário/token de integração dedicado),
conforme alinhado em reunião com o time do Homes. Alguns pontos ainda
dependem de confirmação por parte do Homes — sinalizados com TODO abaixo.

Responsabilidades:
  - Consultar/atualizar tarefas e campos de um processo no Homes.
  - Aplicar rate limiting conforme limite informado (pendente confirmação
    oficial — usar valor conservador até então).
  - Aplicar retry com exponential backoff em falhas transitórias.
"""
import logging
import os
from typing import Any

import requests
from dotenv import load_dotenv

from drivers.base_driver import AuthenticationError, BaseApiDriver
from settings.holmes_settings import HOMES_API_CONFIG, HomesApiConfig

load_dotenv()

logger = logging.getLogger(__name__)


class HomesDriver(BaseApiDriver):
    """Cliente HTTP para a API do Homes."""

    def __init__(self, config: HomesApiConfig = HOMES_API_CONFIG):
        super().__init__(config)

    def _build_session(self) -> requests.Session:
        token = os.getenv("TOKEN_HOMES")

        if not token:
            raise AuthenticationError(
                "Variável TOKEN_HOMES não encontrada no ambiente. "
                "Solicitar conta/token de integração ao time do Homes."
            )

        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )
        return session

    # ------------------------------------------------------------------
    # Métodos de domínio (exemplos — adaptar após mapeamento dos fluxos)
    # ------------------------------------------------------------------

    def buscar_tarefa(self, tarefa_id: str) -> Any:
        """Consulta os dados atuais de uma tarefa/processo no Homes."""
        # TODO: confirmar endpoint exato com o time do Homes.
        return self.get(f"v1/tasks/{tarefa_id}")

    def preencher_campos_e_avancar(self, tarefa_id: str, payload: dict) -> Any:
        """Preenche campos do processo e avança a tarefa no fluxo do Homes."""
        # TODO: confirmar contrato de dados (nomes de campo, endpoint de
        # avanço de etapa) — depende do mapeamento dos fluxos no Homes.
        return self.patch(f"v1/tasks/{tarefa_id}", json=payload)
