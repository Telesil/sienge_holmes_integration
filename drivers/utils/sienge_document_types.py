"""
Tabela de tipos de documento do Sienge (código <-> descrição).

Usada para traduzir o campo "Tipo de Nota" enviado pelo Homes (texto
livre/descrição, ex.: "NOTA FISCAL") para a sigla que o Sienge espera no
campo `documentId` do payload de cadastro (ex.: "NF").

Fonte: tabela de códigos de documento fornecida pelo time financeiro.
"""
import unicodedata
from typing import Dict

# Código -> Descrição (fonte oficial)
CODIGO_PARA_DESCRICAO: Dict[str, str] = {
    "ADTO": "ADIANTAMENTO FORNECEDOR",
    "ADTV": "ADIANTAMENTO ALVORADA",
    "APT": "APORTES",
    "AV": "AVISO DE LANÇAMENTO",
    "BOL": "BOLETO",
    "CAU": "CAUÇÃO",
    "CC": "CARTÃO DE CRÉDITO",
    "CESD": "CESSAO DE DIREITOS",
    "CF": "CONHECIMENTO DE FRETE",
    "CHQ": "CHEQUE",
    "CT": "CONTRATO",
    "CTS": "CONTRATO DE SUPRIMENTOS",
    "DARF": "DARF",
    "DISC": "DISTRATO CARTA DE CREDITO",
    "DIST": "DISTRATO",
    "EMP": "EMPRESTIMOS BANCARIOS",
    "FAT": "FATURA",
    "FF": "FUNDO FIXO",
    "FL": "FOLHA DE PAGAMENTO",
    "FLR": "FOLHA RESSARCIDA",
    "FP": "FATURA DE PERMUTA",
    "FTD": "FATURAMENTO DIRETO",
    "GRAD": "GUIA DE RECOLHIMENTO DA ARECADAÇÃO DIRET",
    "GRCA": "GUIA DE RECOLHIMENTO DA CONTRB ADICIONAL",
    "GRCS": "GUIA RECOLHIMENTO DE CONTRIB. SINDICAL",
    "GRJ": "GUIA DE RECOLHIMENTO JUDICIAL",
    "GRRF": "Guia de Recolhimento Rescisório do FGTS",
    "GRTC": "GUIA DE RECOLHIMENTO DE TERMO DE COOP.",
    "GUIA": "GUIA PARA RECOLHIMENTO DE IMPOSTOS",
    "INI": "INICIALIZAÇÃO CAIXA/BANCOS",
    "INIE": "INICIALIZAÇÃO DE ESTOQUE",
    "IPTU": "IMPOSTO PREDIAL E TERRITORIAL URBANO",
    "MCOI": "DOC. CONTAB. SISTEMA CUSTO ORÇ. E INCOR",
    "MFE": "Movimento de Frotas e Equipamento",
    "MLE": "Movimento de Locação de Equipamentos",
    "MOV": "Movimento de transf. de equipamento",
    "MSST": "MOVIMENTO SEGURANÇA SAUDE NO TRABALHO",
    "NC": "NOTA CANCELADA",
    "NF": "NOTA FISCAL",
    "NFD": "NOTA FISCAL DE DEVOLUÇÃO",
    "NFE": "NOTA FISCAL ELETRÔNICA DE DEVOLUÇÃO",
    "NFEF": "NOTA FISCAL DE ENTREGA FUTURA",
    "NFP": "NOTA FISCAL DE PERMUTA",
    "NFR": "NOTA FISCAL DE REMESSA",
    "NFS": "NOTA FISCAL DE SERVIÇO",
    "NFSP": "NOTA FISCAL DE SERVIÇO - PERMUTA",
    "NFT": "NOTA FISCAL DE TALAO",
    "NFV": "NOTA FISVAL DE VENDA",
    "NR": "NOTA DE SIMPLES REMESSA",
    "NRA": "NOTA DE REMESSA AVULSA",
    "NT": "NOTA DE DEBITO",
    "PCT": "PREVISÃO FINANCEIRA DE CONTR. DE MED.",
    "PENS": "PENSAO ALIMENTICIA",
    "PER": "PERMUTA",
    "PPC": "PREVISÃO FINANCEIRA DE PEDIDOS DE COMPRA",
    "PRC": "PREVISÃO DE COMISSÃO",
    "PRDI": "PREVISÃO DE DISTRATO",
    "PRV": "PREVISÃO DE PAGAMENTO/RECEBIMENTO",
    "REAL": "REALOCAÇÃO DE MATERIAIS",
    "REC": "RECIBO",
    "REQ": "REQUISIÇÃO DE MATERIAIS",
    "RFCS": "REGISTRO FINANCIAMENTO, CONTRATO, SINAL",
    "RSOC": "REPASSE AOS SÓCIOS",
    "SI": "SINAL",
    "SPED": "SPED",
    "TAC": "TAXA DE ADMINISTRAÇÃO CENTRAL",
    "TR": "DOCUMENTO DE TRANSFERÊNCIA DE MATERIAIS",
    "TRA": "TRANSFERENCIA ALVOARADA",
    "TRCT": "Termo de Resc do Contrato de Trabalho",
    "TRES": "TRANSF./RESERVA DE ESTOQUE P/SOLICITAÇÃO",
    "TRS": "TRANSF. DE MATERIAIS POR SOLICITAÇÃO",
    "VEXP": "VEXPENSES",
    "VLT": "VALE TRANSPORTE",
}


def _normalizar(texto: str) -> str:
    """Maiúsculas, sem espaços nas pontas, sem espaços duplicados no meio."""
    return " ".join(texto.strip().upper().split())


def _remover_acentos(texto: str) -> str:
    """Remove acentuação, mantendo o restante do texto intacto."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _construir_indice() -> Dict[str, str]:
    """
    Constrói o índice reverso (descrição normalizada, sem acento -> código),
    detectando duplicidades na fonte (o que indicaria um erro na tabela).
    """
    indice: Dict[str, str] = {}
    for codigo, descricao in CODIGO_PARA_DESCRICAO.items():
        chave = _remover_acentos(_normalizar(descricao))
        if chave in indice and indice[chave] != codigo:
            raise ValueError(
                f"Descrição duplicada na tabela de tipos de documento: "
                f"'{descricao}' mapeia tanto para '{indice[chave]}' quanto para '{codigo}'."
            )
        indice[chave] = codigo
    return indice


_INDICE_DESCRICAO_PARA_CODIGO = _construir_indice()


def resolver_document_id(descricao_homes: str) -> str:
    """
    Traduz a descrição do tipo de nota vinda do Homes (ex.: "Nota Fiscal")
    para a sigla esperada pelo Sienge no campo `documentId` (ex.: "NF").

    A comparação ignora maiúsculas/minúsculas, espaços extras e acentos —
    então "Nota Fiscal", "NOTA FISCAL " e "nota fiscal" resolvem igual.

    Raises:
        ValueError: se a descrição não existir na tabela.
    """
    if not descricao_homes or not descricao_homes.strip():
        raise ValueError("Tipo de nota não informado.")

    chave = _remover_acentos(_normalizar(descricao_homes))
    codigo = _INDICE_DESCRICAO_PARA_CODIGO.get(chave)

    if codigo is None:
        raise ValueError(
            f"Tipo de nota '{descricao_homes}' não encontrado na tabela de "
            f"tipos de documento do Sienge."
        )

    return codigo
