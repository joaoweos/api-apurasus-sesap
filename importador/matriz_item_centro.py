"""Relatório 'Itens de Custo x Centros de Custos' (matriz de um único mês).

Layout: primeira célula do cabeçalho = 'DE/PARA'; itens nas colunas (só os com
movimento no mês) e centros nas linhas. Valores em formato brasileiro com
ponto de milhar ('3.180,00').

O nome do centro pode se repetir (ex.: dois 'Centro Obstétrico', um deles é
uma farmácia satélite). Por isso cada linha leva a 'ocorrencia' (1, 2, ...)
do nome dentro do arquivo.
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

from .comum import (ArquivoInvalido, Leitura, achar_instituicao, ler_tabela,
                    normalizar, parse_numero, primeiro_dia)

TIPO = "matriz_item_centro"


def ler(conteudo: bytes) -> Leitura:
    linhas = ler_tabela(conteudo)
    instituicao = achar_instituicao(linhas)

    periodo = None
    for l in linhas[:8]:
        m = re.search(r"(\d{2})/(\d{4})\s*a\s*(\d{2})/(\d{4})", " ".join(l))
        if m:
            periodo = (primeiro_dia(int(m[1]), int(m[2])), primeiro_dia(int(m[3]), int(m[4])))
            break
    if periodo is None:
        raise ArquivoInvalido("Período ('Periodo de : MM/AAAA a MM/AAAA') não encontrado.")
    inicio, fim = periodo
    if inicio != fim:
        raise ArquivoInvalido("A matriz Item x Centro deve conter um único mês; "
                              f"o arquivo cobre {inicio:%m/%Y} a {fim:%m/%Y}.")

    idx = next((i for i, l in enumerate(linhas) if l and normalizar(l[0]) == "de/para"), None)
    if idx is None:
        raise ArquivoInvalido("Cabeçalho 'DE/PARA' da matriz não encontrado.")
    itens = [c.strip() for c in linhas[idx][1:]]

    saida: list[dict] = []
    centros: list[dict] = []
    ocorrencias: Counter = Counter()
    total_arquivo: Decimal | None = None
    avisos: list[str] = []

    for l in linhas[idx + 1:]:
        nome = (l[0] if l else "").strip()
        if not nome:
            continue
        if normalizar(nome).startswith("total"):
            total_arquivo = sum((parse_numero(c) or Decimal(0) for c in l[1:]), Decimal(0))
            continue
        ocorrencias[nome] += 1
        custo_direto = Decimal(0)
        for j, item in enumerate(itens, start=1):
            v = parse_numero(l[j]) if j < len(l) else None
            if v is None:
                continue
            custo_direto += v
            if v != 0:
                saida.append({"competencia": inicio, "centro_bruto": nome,
                              "ocorrencia": ocorrencias[nome], "item_bruto": item, "valor": v})
        centros.append({"centro_bruto": nome, "ocorrencia": ocorrencias[nome], "custo_direto": custo_direto})

    for nome, n in ocorrencias.items():
        if n > 1:
            avisos.append(f"Nome de centro repetido {n}x: '{nome}'. "
                          "A ocorrência do nome no arquivo será usada para diferenciá-los.")

    return Leitura(TIPO, instituicao, inicio, fim, saida, avisos, [],
                   {"centros": centros, "itens": itens,
                    "total": sum((c["custo_direto"] for c in centros), Decimal(0)),
                    "total_declarado_no_arquivo": total_arquivo})
