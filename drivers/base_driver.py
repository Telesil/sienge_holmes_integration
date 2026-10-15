"""
BaseApiDriver — lógica comum de conexão HTTP para todos os sistemas externos.

Cada integração (Sienge, Homes, ...) implementa apenas:
  - _build_session(): como autenticar (basic auth, bearer token, etc.)

O restante (retry com backoff exponencial, rate limiting, tratamento de
timeouts e status transitórios) é compartilhado por herança, evitando
duplicar a lógica que já existe em api_requester.py para cada sistema novo.
"""
import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# HTTP status que justificam retry (erros transitórios)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AuthenticationError(Exception):
    """Credenciais ausentes ou inválidas."""


class ApiUnavailableError(Exception):
    """
    A API externa não respondeu de forma utilizável após todos os retries
    (timeout, falha de conexão ou status 5xx persistente).

    Atributos:
        url: endpoint chamado.
        ultimo_status: último HTTP status recebido (None se nem houve resposta).
        motivo: descrição curta da última falha ("timeout", "conexao", "status 503"...).
    """

    def __init__(self, mensagem: str, url: str = "", ultimo_status: Optional[int] = None,
                 motivo: str = ""):
        super().__init__(mensagem)
        self.url = url
        self.ultimo_status = ultimo_status
        self.motivo = motivo


class RateLimitError(ApiUnavailableError):
    """Limite de requisições da API atingido (429) após todos os retries."""


class BaseApiDriver:
    """
    Cliente HTTP genérico com retry + rate limit.

    Subclasses devem implementar `_build_session()` retornando uma
    `requests.Session` já autenticada para o sistema alvo.

    Thread-safety: uma instância por thread/worker (mesma premissa do
    api_requester.py original).
    """

    def __init__(self, config: Any):
        """
        Args:
            config: objeto de configuração (ver app/settings/) expondo:
                base_url, timeout, rate_limit_seconds, max_retries,
                retry_backoff_factor.
        """
        self._config = config
        self._session = self._build_session()
        logger.info("%s inicializado.", self.__class__.__name__)

    # ------------------------------------------------------------------
    # Setup — sobrescrever em cada driver concreto
    # ------------------------------------------------------------------

    def _build_session(self) -> requests.Session:
        raise NotImplementedError(
            "Cada driver concreto deve implementar sua própria autenticação."
        )

    # ------------------------------------------------------------------
    # GET com retry + rate limiting
    # ------------------------------------------------------------------

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        """
        Executa GET com retry exponencial e respeita o rate limit.

        Args:
            path: caminho relativo à base_url (ex.: "customers").
            params: query params opcionais.

        Returns:
            Objeto Python deserializado do JSON da resposta.

        Raises:
            RateLimitError: quando 429 persiste após todos os retries.
            ApiUnavailableError: timeout/conexão/5xx persistentes após os retries.
            requests.HTTPError: para erros HTTP não recuperáveis.
        """
        url = self._build_url(path)
        return self._request("GET", url, params=params)

    # ------------------------------------------------------------------
    # POST/PATCH — necessários para escrever de volta no sistema (ex.: Homes)
    # ------------------------------------------------------------------

    def post(self, path: str, json: Optional[dict] = None) -> Any:
        url = self._build_url(path)
        return self._request("POST", url, json=json)

    def patch(self, path: str, json: Optional[dict] = None) -> Any:
        url = self._build_url(path)
        return self._request("PATCH", url, json=json)

    # ------------------------------------------------------------------
    # Núcleo compartilhado: retry + backoff + rate limit
    # ------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> Any:
        attempt = 0
        delay = self._config.rate_limit_seconds
        ultimo_status: Optional[int] = None
        motivo = ""

        while attempt <= self._config.max_retries:
            try:
                response = self._session.request(
                    method, url, timeout=self._config.timeout, **kwargs
                )

                if response.status_code == 429:
                    ultimo_status, motivo = 429, "rate limit (429)"
                    wait = self._retry_after(response, delay)
                    logger.warning(
                        "Rate limit atingido em %s. Aguardando %.1fs (retry %d/%d).",
                        url, wait, attempt + 1, self._config.max_retries,
                    )
                    time.sleep(wait)
                    attempt += 1
                    delay *= self._config.retry_backoff_factor
                    continue

                if response.status_code in RETRYABLE_STATUS:
                    ultimo_status, motivo = response.status_code, f"status {response.status_code}"
                    logger.warning(
                        "Status %d em %s. Retry %d/%d em %.1fs.",
                        response.status_code, url, attempt + 1,
                        self._config.max_retries, delay,
                    )
                    time.sleep(delay)
                    attempt += 1
                    delay *= self._config.retry_backoff_factor
                    continue

                response.raise_for_status()
                return response.json() if response.content else None

            except requests.Timeout:
                ultimo_status, motivo = None, "timeout"
                logger.warning(
                    "Timeout em %s. Retry %d/%d em %.1fs.",
                    url, attempt + 1, self._config.max_retries, delay,
                )
                time.sleep(delay)
                attempt += 1
                delay *= self._config.retry_backoff_factor

            except requests.ConnectionError as exc:
                # Queda de conexão / DNS / reset — também é transitório.
                ultimo_status, motivo = None, "falha de conexão"
                logger.warning(
                    "Falha de conexão em %s (%s). Retry %d/%d em %.1fs.",
                    url, exc, attempt + 1, self._config.max_retries, delay,
                )
                time.sleep(delay)
                attempt += 1
                delay *= self._config.retry_backoff_factor

        mensagem = (
            f"Máximo de retries ({self._config.max_retries}) atingido para {url} "
            f"(última falha: {motivo or 'desconhecida'})."
        )
        excecao = RateLimitError if ultimo_status == 429 else ApiUnavailableError
        raise excecao(mensagem, url=url, ultimo_status=ultimo_status, motivo=motivo)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self._config.base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def _retry_after(response: requests.Response, fallback: float) -> float:
        """Lê o header Retry-After da resposta 429, ou usa fallback."""
        header = response.headers.get("Retry-After")
        try:
            return float(header) if header else fallback
        except ValueError:
            return fallback

    def rate_limit_sleep(self) -> None:
        """Pausa respeitando o rate limit configurado para este sistema."""
        time.sleep(self._config.rate_limit_seconds)
