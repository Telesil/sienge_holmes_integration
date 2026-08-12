"""
Configurações da conexão com a API do Homes.

ATENÇÃO — pontos pendentes de confirmação com o time do Homes (reunião
inicial de integração):
  - base_url oficial do ambiente de produção/homologação.
  - Limite exato de requisições (citado informalmente como ~100 req / 5 min,
    fora da fila de webhooks — precisa confirmação oficial).
  - Se existirá endpoint de "request direto" (feature em desenvolvimento
    pelo Homes) que pode simplificar ou substituir parte deste driver.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class HomesApiConfig:
    base_url: str = "https://api.homes.example.com/"  # TODO: confirmar URL oficial
    timeout: int = 60
    # TODO: confirmar limite oficial. Valor abaixo é estimativa conservadora
    # a partir do informado em reunião (~100 req / 5 min ⇒ ~3s por req).
    rate_limit_seconds: float = 3.0
    max_retries: int = 3
    retry_backoff_factor: float = 2.0  # Exponential backoff: 3s, 6s, 12s


HOMES_API_CONFIG = HomesApiConfig()
