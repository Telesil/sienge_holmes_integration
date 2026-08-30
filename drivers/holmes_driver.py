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
from typing import Any, Optional

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

    def consultar_processo(self, tarefa_id: str) -> Any:
        """
        Consulta os dados atuais de uma tarefa/processo no Homes.

        Retorna o JSON bruto do processo, que deve conter (entre outras
        coisas) a chave 'current_activities' — usada por fluxo_notas()
        para localizar a atividade de integração.
        """
        # TODO: confirmar endpoint exato com o time do Homes.

        payload = {
            "return_tasks": True
        }
        return self.get(f"https://app-api.holmesdoc.io/v2/processes/{tarefa_id}", params=payload)

    def fluxo_notas(self, tarefa: dict) -> Optional[str]:
        """
        Localiza, entre as atividades atuais do processo, a atividade de
        integração "Central de Lançamento | Registrar NF" (task_id fixo)
        e retorna o seu id.

        Esse id é necessário depois para chamar persistir_resultado() e
        gravar de volta no Homes o que aconteceu na tentativa de
        integração com o Sienge.

        Retorna None se a atividade não estiver mais entre as atividades
        atuais do processo (ex.: já avançou, ou o processo foi alterado).
        """
        atividade_integracao = 'Activity_1ih97f8'

        atividades = tarefa.get('current_activities', [])


        for atividade in atividades:
            if atividade.get('task_id') == atividade_integracao:
                return atividade.get('id')

        return None

    def persistir_resultado(self, id_atividade: str, result_sienge: Any) -> Any:
        """
        Grava de volta na atividade do Homes o resultado da tentativa de
        integração com o Sienge (sucesso ou erro), para que o time
        acompanhe o status sem precisar sair da ferramenta.

        Args:
            id_atividade: id da atividade retornado por fluxo_notas().
            result_sienge: mensagem/detalhe do resultado a ser exibido
                no campo do Homes (será convertido para string).
        """

        print(result_sienge)


        print('aquiiii')
        payload = {
            "task": {
                "action_id": "Flow_02yfr8o",
                "property_values": [
                    {
                        "id": "60a98fb0-a2ed-11f1-833d-8b847e0671e5",
                        "value": str(result_sienge),
                    }
                ],
                "confirm_action": False,
            }
        }

        return self.post(f"https://app-api.holmesdoc.io/v1/tasks/{id_atividade}/action", json=payload)