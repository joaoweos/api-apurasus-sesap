"""Funções comuns aos leitores de relatórios do ApuraSUS.

Cuidam do que é igual em todos os arquivos: codificação, separador,
números em formato brasileiro, datas e nomes. Cada relatório tem seu
próprio leitor (um arquivo por layout) e devolve um objeto `Leitura`.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTAVOS = Decimal("0.01")
TOLERANCIA_CONFERENCIA = Decimal("0.05")  # centavos de arredondamento

# 50,069,160,000,000,000,000 -> 50.069,16 (número "estourado" visto no Custo Total da Unidade)
_MALFORMADO = re.compile(r"^-?\d{1,3}(,\d{3}){5,}$")
_MILHAR_COM_PONTO = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
_MES_ANO = re.compile(r"(?<![\d/])(\d{2})/(\d{4})(?!\d)")


class ArquivoInvalido(ValueError):
    """O arquivo não tem a estrutura esperada para o tipo de relatório."""


@dataclass
class Leitura:
    tipo: str
    instituicao: str
    inicio: date
    fim: date
    linhas: list[dict]
    avisos: list[str] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)   # conferências que falharam
    extras: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.erros

    @property
    def competencias(self) -> list[date]:
        return sorted({l["competencia"] for l in self.linhas})


# ---------------------------------------------------------------------------
# Texto e tabela
# ---------------------------------------------------------------------------

def decodificar(conteudo: bytes) -> str:
    """O arquivo bruto do ApuraSUS vem em ISO-8859-1; arquivos abertos e
    salvos em planilha costumam virar UTF-8. Tenta os dois."""
    for codificacao in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return conteudo.decode(codificacao)
        except UnicodeDecodeError:
            continue
    raise ArquivoInvalido("Não foi possível decodificar o arquivo.")


def ler_tabela(conteudo: bytes) -> list[list[str]]:
    texto = decodificar(conteudo)
    # Arquivo bruto: ';' em todas as linhas de dados. Arquivo re-salvo: ','.
    delimitador = ";" if texto.count(";") > texto.count("\n") else ","
    return [list(l) for l in csv.reader(io.StringIO(texto), delimiter=delimitador)]


# ---------------------------------------------------------------------------
# Números, datas e nomes
# ---------------------------------------------------------------------------

def parse_numero(valor: str | None) -> Decimal | None:
    """Converte número em formato brasileiro para Decimal com 2 casas.

    Aceita '9053073,58', '3.180,00', '1037049,529999999952319' (ruído de
    ponto flutuante) e o formato estourado '50,069,160,000,000,000,000'.
    Vazio ou '-' devolve None.
    """
    s = (valor or "").replace("\xa0", "").strip()
    if s in ("", "-"):
        return None
    try:
        if _MALFORMADO.match(s):
            return (Decimal(s.replace(",", "")) / Decimal(10) ** 15).quantize(CENTAVOS, ROUND_HALF_UP)
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif _MILHAR_COM_PONTO.match(s):
            s = s.replace(".", "")
        return Decimal(s).quantize(CENTAVOS, ROUND_HALF_UP)
    except InvalidOperation:
        raise ArquivoInvalido(f"Número inválido: {valor!r}")


def primeiro_dia(mes: int, ano: int) -> date:
    return date(ano, mes, 1)


def parse_competencia(valor) -> date | None:
    """'2025-11', '2025-11-01' ou '11/2025' -> date(2025, 11, 1). Vazio -> None."""
    if valor is None or str(valor).strip() == "":
        return None
    t = str(valor).strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-01)?", t) or None
    if m:
        ano, mes = int(m[1]), int(m[2])
    else:
        m = re.fullmatch(r"(\d{2})/(\d{4})", t)
        if not m:
            raise ArquivoInvalido(f"Mês de referência inválido: {valor!r}. Use AAAA-MM (ex.: 2025-11).")
        mes, ano = int(m[1]), int(m[2])
    if not 1 <= mes <= 12:
        raise ArquivoInvalido(f"Mês de referência inválido: {valor!r}.")
    return primeiro_dia(mes, ano)


def mes_ano_da_celula(celula: str) -> date | None:
    """'01/2025' ou 'Valor(01/2025)' -> date(2025, 1, 1). Datas completas
    ('01/12/2025') não casam."""
    m = _MES_ANO.search(celula or "")
    return primeiro_dia(int(m.group(1)), int(m.group(2))) if m else None


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, espaços simples."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


def esqueleto(texto: str) -> str:
    """Só letras ASCII, dígitos e espaço. Serve para casar nomes cujos acentos
    foram destruídos (ex.: 'Servi蔞 de Lavanderia' -> 'servi de lavanderia')."""
    t = re.sub(r"[^A-Za-z0-9 ]", "", texto or "")
    return re.sub(r"\s+", " ", t).strip().lower()


def achar_instituicao(linhas: list[list[str]]) -> str:
    for l in linhas[:8]:
        c0 = (l[0] if l else "").strip()
        if not c0:
            continue
        baixo = normalizar(c0)
        if baixo.startswith("instituicao:") or re.match(r"^\s*institui.{0,3}o\s*:", c0, re.I):
            return c0.split(":", 1)[1].strip()          # tolera 'Institui��o:' (acentos corrompidos)
        if not re.search(r"\d{2}/\d{2}/\d{4}|periodo", baixo):
            return c0
    raise ArquivoInvalido("Não foi possível identificar a instituição no cabeçalho.")


def conferir(descricao: str, esperado: Decimal, obtido: Decimal,
             erros: list[str], tolerancia: Decimal = TOLERANCIA_CONFERENCIA) -> None:
    if abs(esperado - obtido) > tolerancia:
        erros.append(f"{descricao}: esperado {esperado}, obtido {obtido} "
                     f"(diferença {esperado - obtido}).")
