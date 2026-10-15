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
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional

import requests
from dotenv import load_dotenv
import pandas as pd

from drivers.base_driver import AuthenticationError, BaseApiDriver
from drivers.utils.sienge_document_types import resolver_document_id as resolver_document_id_tabela
from settings.sienge_settings import SIENGE_API_CONFIG, SiengeApiConfig

load_dotenv()

logger = logging.getLogger(__name__)

_CAMINHO_CENTROS_CUSTO = Path(__file__).parent / "files" / "referencer" / "centros_de_custo.csv"


class FornecedorNaoEncontradoError(ValueError):
    """Nenhum credor encontrado no Sienge para o CPF/CNPJ informado (após todas as tentativas)."""

    def __init__(self, documento: str, tentativas: List[str]):
        self.documento = documento
        self.tentativas = tentativas
        super().__init__(
            f"Fornecedor com CPF/CNPJ {documento} não encontrado no Sienge "
            f"(valores consultados: {', '.join(tentativas)})."
        )


class EmpresaNaoEncontradaError(ValueError):
    """Centro de custo informado não existe na tabela de referência (centros_de_custo.csv)."""

    def __init__(self, centro_custo: str, codigo: Optional[int] = None):
        self.centro_custo = centro_custo
        self.codigo = codigo
        detalhe = f" (código {codigo})" if codigo is not None else ""
        super().__init__(
            f"Centro de custo '{centro_custo}'{detalhe} não encontrado na tabela de "
            f"centros de custo/empresas."
        )


@lru_cache(maxsize=1)
def _carregar_centros_custo() -> pd.DataFrame:
    """Lê o CSV de centros de custo uma única vez por processo."""
    return pd.read_csv(_CAMINHO_CENTROS_CUSTO, sep=";")


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

    @staticmethod
    def _extrair_id_credor(resultado: Any) -> Optional[int]:
        """
        Extrai o ID do credor da resposta do Sienge, tolerando os formatos
        possíveis. Retorna None quando a busca não trouxe nenhum credor
        (ex.: {"resultSetMetadata": {...}, "results": []}).
        """
        if not resultado:
            return None
        if isinstance(resultado, list):
            registros = resultado
        elif isinstance(resultado, dict) and "results" in resultado:
            registros = resultado.get("results") or []
        elif isinstance(resultado, dict):
            registros = [resultado]
        else:
            return None

        for registro in registros:
            if isinstance(registro, dict) and registro.get("id") is not None:
                return registro["id"]
        return None

    @staticmethod
    def _variacoes_documento(documento: str) -> List[str]:
        """
        Valores a consultar, em ordem:
          1. o documento exatamente como veio do Homes;
          2. o mesmo documento sem zeros à esquerda — contorna o bug de
             leitura do Enolaa que às vezes acrescenta um "0" na frente.
        """
        original = str(documento).strip()
        variacoes = [original]
        sem_zeros = original.lstrip("0")
        if sem_zeros and sem_zeros != original:
            variacoes.append(sem_zeros)
        return variacoes

    def resolver_supplier_id(self, cnpj: str) -> int:
        """
        Busca o fornecedor pelo CPF/CNPJ e retorna o ID interno do Sienge.

        Tenta primeiro com o valor original; se não encontrar e houver zero
        à esquerda, tenta uma última vez sem ele.

        Raises:
            FornecedorNaoEncontradoError: nenhuma das tentativas encontrou credor.
            requests.HTTPError / ApiUnavailableError: falha na chamada ao Sienge.
        """
        tentativas = self._variacoes_documento(cnpj)
        consultados: List[str] = []

        for i, valor in enumerate(tentativas):
            if i > 0:
                self.rate_limit_sleep()  # respeita o limite de 20 req/min
            consultados.append(valor)
            supplier_id = self._extrair_id_credor(self.buscar_credor_por_cnpj(valor))
            if supplier_id is not None:
                if valor != tentativas[0]:
                    logger.warning(
                        "Fornecedor encontrado só após remover zero(s) à esquerda: "
                        "'%s' -> '%s' (id %s).", tentativas[0], valor, supplier_id,
                    )
                return supplier_id
            logger.info("Nenhum credor encontrado no Sienge para '%s'.", valor)

        raise FornecedorNaoEncontradoError(tentativas[0], consultados)

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
        """
        Resolve o código da empresa no Sienge a partir do centro de custo
        informado no Homes (ex.: "TL 1234" -> 1234 -> cod_empresa).

        Raises:
            ValueError: centro de custo sem nenhum dígito.
            EmpresaNaoEncontradaError: código não existe na tabela de referência.
        """
        digitos = "".join(re.findall(r"\d+", str(tl)))
        if not digitos:
            raise ValueError(f"Centro de custo '{tl}' não contém um código numérico.")
        cod_centro_custo = int(digitos)

        df_centro_custo = _carregar_centros_custo()
        empresas = df_centro_custo.loc[
            df_centro_custo["cod_centro_custo"] == cod_centro_custo,
            "cod_empresa",
        ].dropna()

        if empresas.empty:
            raise EmpresaNaoEncontradaError(str(tl), cod_centro_custo)

        return int(empresas.iloc[0])
