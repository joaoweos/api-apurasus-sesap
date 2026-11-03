"""Relatório 'Centro de Custos x Produto' (até 12 meses por arquivo).

Layout: blocos mensais. Cada bloco tem uma linha 'MM/AAAA', o cabeçalho
'Centro Custo, Item Produção, Quantidade, Valor' e seções por tipo de centro
(Administrativo, Intermediário, Final, Externo). 'Valor' é o custo unitário
médio (custo total / quantidade, arredondado em 2 casas).
"""
from __future__ import annotations

import re
from collections import Counter

from .comum import (ArquivoInvalido, Leitura, achar_instituicao, ler_tabela,
                    normalizar, parse_numero, primeiro_dia)

TIPO = "centro_produto"
_SECOES = {"administrativo", "intermediario", "final", "externo"}


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

    saida: list[dict] = []
    avisos: list[str] = []
    erros: list[str] = []
    comp = secao = None

    for l in linhas:
        c0 = (l[0] if l else "").strip()
        resto_vazio = not any(x.strip() for x in l[1:])
        if re.fullmatch(r"\d{2}/\d{4}", c0) and resto_vazio:
            comp, secao = primeiro_dia(int(c0[:2]), int(c0[3:])), None
            continue
        if normalizar(c0) in ("centro custo", ""):
            continue
        if normalizar(c0) in _SECOES and resto_vazio:
            secao = normalizar(c0)
            continue
        if comp is None or len(l) < 4:
            continue
        item = l[1].strip()
        if not item:
            continue
        if secao is None:
            erros.append(f"Linha '{c0}' em {comp:%m/%Y} sem seção de tipo de centro.")
            continue
        saida.append({"competencia": comp, "tipo_secao": secao, "centro_bruto": c0,
                      "item_producao": item, "quantidade": parse_numero(l[2]),
                      "custo_unitario": parse_numero(l[3])})

    chaves = Counter((x["competencia"], x["tipo_secao"], x["centro_bruto"], x["item_producao"]) for x in saida)
    for (c, s, n, i), k in chaves.items():
        if k > 1:
            erros.append(f"Linha repetida {k}x: {c:%m/%Y} / {s} / {n} / {i}.")
    if not saida:
        raise ArquivoInvalido("Nenhuma linha de produção encontrada.")

    por_mes = Counter(x["competencia"] for x in saida)
    return Leitura(TIPO, instituicao, periodo[0], periodo[1], saida, avisos, erros,
                   {"linhas_por_mes": dict(por_mes)})
