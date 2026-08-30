"""
SiengeDriver — conexão autenticada com a API do Sienge.

Autenticação: HTTP Basic Auth (usuário + senha), conforme api_requester.py
original. Mantém as mesmas responsabilidades do driver de referência:
  - Aplicar rate limiting conforme documentação oficial (20 req/min).
  - Aplicar retry com exponential backoff em falhas transitórias.
  - Retornar dados brutos (JSON) sem qualquer transformação.
"""
import logging
import os
from typing import Any
from pathlib import Path
import re

import requests
from dotenv import load_dotenv
import pandas as pd

from drivers.base_driver import AuthenticationError, BaseApiDriver
from drivers.utils.sienge_document_types import resolver_document_id as resolver_document_id_tabela
from settings.sienge_settings import SIENGE_API_CONFIG, SiengeApiConfig

load_dotenv()

logger = logging.getLogger(__name__)


class SiengeDriver(BaseApiDriver):
    """Cliente HTTP para a API Sienge (bulk-data, customers, bills, etc.)."""

    def __init__(self, config: SiengeApiConfig = SIENGE_API_CONFIG):
        super().__init__(config)

    def _build_session(self) -> requests.Session:
        usuario = os.getenv("USUARIO_SIENGE")
        senha = os.getenv("SENHA_SIENGE")

        if not usuario or not senha:
            raise AuthenticationError(
                "Variáveis USUARIO_SIENGE e SENHA_SIENGE não encontradas no ambiente."
            )

        session = requests.Session()
        session.auth = (usuario, senha)
        session.headers.update({"Content-Type": "application/json"})
        return session

    # ------------------------------------------------------------------
    # Métodos de domínio (exemplos — adaptar conforme os fluxos mapeados)
    # ------------------------------------------------------------------

    def buscar_credor_por_cnpj(self, cnpj: str) -> Any:
        """Consulta se já existe um credor cadastrado com o CNPJ informado."""
        return self.get("v1/creditors", params={"cnpj": cnpj})

    def resolver_supplier_id(self, cnpj: str) -> int:
        """Busca o fornecedor pelo CNPJ e retorna o ID interno do Sienge."""
        resultado = self.buscar_credor_por_cnpj(cnpj)
        if not resultado:
            raise ValueError(f"Fornecedor com CNPJ {cnpj} não encontrado no Sienge.")
        # TODO: confirmar o nome exato do campo de ID na resposta real do Sienge
        return resultado["results"][0]["id"] if "results" in resultado else resultado["id"]

    def resolver_document_id(self, tipo_nota_homes: str) -> str:
        """
        Traduz o "Tipo de Nota" enviado pelo Homes (descrição, ex.:
        "Nota Fiscal") para a sigla que o Sienge espera em `documentId`
        (ex.: "NF"). Levanta ValueError se a descrição não for
        reconhecida.
        """
        return resolver_document_id_tabela(tipo_nota_homes)

    def cadastrar_nota_fiscal_compra(self, payload: dict) -> Any:
        """
        Cadastra uma nota fiscal de compra no Sienge (passo 1 — dados principais).
        Endpoint: POST /v1/purchase-invoices
        """
        return self.post("v1/purchase-invoices", json=payload)

    def identificar_empresa(self, tl: str) -> int:
        caminho_base = Path(__file__).parent

        df_centro_custo = pd.read_csv(
            caminho_base / "files" / "referencer" / "centros_de_custo.csv",
            sep=";"
        )

        cod_centro_custo = int("".join(re.findall(r"\d+", tl)))

        empresa = df_centro_custo.loc[
            df_centro_custo["cod_centro_custo"] == cod_centro_custo,
            "cod_empresa"
        ].iloc[0]

        return int(empresa)
