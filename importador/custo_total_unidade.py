"""Relatório 'Custo Total da Unidade' (Relatórios > Item de Custo - Sintético/Analítico).

Layout: meses em colunas (até 12), grupos (Pessoal, Material de Consumo,
Serviços de Terceiros, Despesas Gerais) com subtotal, itens abaixo de cada
grupo e uma linha 'TOTAL GERAL'. Os acentos costumam chegar destruídos, por
isso o item é devolvido com o nome bruto e com o 'esqueleto' ASCII.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from .comum import (ArquivoInvalido, Leitura, achar_instituicao, conferir,
                    ler_tabela, mes_ano_da_celula, normalizar, esqueleto,
                    parse_numero)

TIPO = "custo_total_unidade"

_GRUPOS_FIXOS = {
    "pessoal": "Pessoal",
    "material de consumo": "Material de Consumo",
    "despesas gerais": "Despesas Gerais",
}


def _grupo_canonico(rotulo: str) -> str | None:
    sk = esqueleto(rotulo)
    if sk in _GRUPOS_FIXOS:
        return _GRUPOS_FIXOS[sk]
    if re.fullmatch(r"servi\w* de terceiros", sk):  # 'Serviços' pode chegar sem o 'ç'
        return "Serviços de Terceiros"
    return None


def ler(conteudo: bytes) -> Leitura:
    linhas = ler_tabela(conteudo)
    instituicao = achar_instituicao(linhas)

    idx = next((i for i, l in enumerate(linhas)
                if "item custo" in normalizar(l[0] if l else "")
                and any(mes_ano_da_celula(c) for c in l[1:])), None)
    if idx is None:
        raise ArquivoInvalido("Cabeçalho 'Item Custo' com meses não encontrado.")
    meses = [(j, mes_ano_da_celula(c)) for j, c in enumerate(linhas[idx]) if j >= 1 and mes_ano_da_celula(c)]

    saida: list[dict] = []
    subtotais: dict[str, dict[date, Decimal | None]] = {}
    total_geral: dict[date, Decimal | None] = {}
    avisos: list[str] = []
    grupo = None

    for l in linhas[idx + 1:]:
        rotulo = (l[0] if l else "").strip()
        if not rotulo:
            continue
        valores = {comp: (parse_numero(l[j]) if j < len(l) else None) for j, comp in meses}
        if normalizar(rotulo) == "total geral":
            total_geral = valores
            continue
        g = _grupo_canonico(rotulo)
        if g:
            grupo = g
            subtotais[g] = valores
            continue
        if grupo is None:
            avisos.append(f"Linha '{esqueleto(rotulo)}' antes de qualquer grupo; ignorada.")
            continue
        for comp, v in valores.items():
            if v is not None:
                saida.append({"competencia": comp, "grupo": grupo, "item_bruto": rotulo,
                              "item_esqueleto": esqueleto(rotulo), "valor": v})

    # Conferências internas: itens = subtotal do grupo; grupos = total geral
    erros: list[str] = []
    for _, comp in meses:
        soma_grupos = Decimal(0)
        for g, sub in subtotais.items():
            soma_itens = sum((x["valor"] for x in saida if x["competencia"] == comp and x["grupo"] == g), Decimal(0))
            if sub.get(comp) is not None:
                conferir(f"{g} em {comp:%m/%Y}: soma dos itens x subtotal", sub[comp], soma_itens, erros)
                soma_grupos += sub[comp]
        if total_geral.get(comp) is not None:
            conferir(f"Total geral em {comp:%m/%Y}: soma dos grupos x total", total_geral[comp], soma_grupos, erros)
    if not total_geral:
        avisos.append("Linha 'TOTAL GERAL' não encontrada; conferência do total não executada.")

    return Leitura(TIPO, instituicao, meses[0][1], meses[-1][1], saida, avisos, erros,
                   {"total_geral": total_geral, "subtotais": subtotais})
