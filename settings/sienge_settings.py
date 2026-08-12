"""
Configurações da conexão com a API do Sienge.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SiengeApiConfig:
    base_url: str = "https://api.sienge.com.br/telesil/public/api/"
    timeout: int = 240
    # Documentação oficial: 20 req/min → 1 req a cada 3s com margem de segurança
    rate_limit_seconds: float = 3.5
    max_retries: int = 3
    retry_backoff_factor: float = 2.0  # Exponential backoff: 3.5s, 7s, 14s


SIENGE_API_CONFIG = SiengeApiConfig()
