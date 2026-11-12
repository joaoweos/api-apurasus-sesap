"""Fluxo de importação: ler, conferir, resolver nomes e gravar.

Regra de ouro: se houver QUALQUER nome sem correspondência ou conferência
divergente, nada é gravado; a importação fica 'pendente' ou 'erro', com o motivo.
Isso evita totais parciais que pareçam corretos.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from .comum import ArquivoInvalido, normalizar
from .registro import ler_relatorio
from .resolver import Dimensoes, nome_hospital

TABELAS = {
    "custo_total_unidade": ("fato_apurasus_custo_item", "unidade_id,competencia,ic_id"),
    "matriz_item_centro": ("fato_apurasus_item_centro", "unidade_id,competencia,ic_id,cc_id"),
    "centro_produto": ("fato_apurasus_custo_unitario", "unidade_id,competencia,cc_id,item_producao"),
    "matriz_custos_totais": ("fato_apurasus_formacao_custo", "unidade_id,competencia,cc_id"),
}
APAGAR_ANTES = {"matriz_item_centro", "centro_produto", "matriz_custos_totais"}
TOLERANCIA_ITENS = Decimal("1.00")   # soma do Custo Direto x total dos itens do mês   # arquivos completos para os meses: substituem o que havia


class Repositorio(Protocol):
    def obter_unidade(self, unidade_id: str) -> dict | None: ...
    def carregar_dimensoes(self, unidade_id: str) -> Dimensoes: ...
    def buscar_importacao(self, unidade_id: str, arquivo_hash: str) -> dict | None: ...
    def criar_importacao(self, dados: dict) -> str: ...
    def atualizar_importacao(self, importacao_id: str, campos: dict) -> None: ...
    def substituir_pendencias(self, importacao_id: str, pendencias: list[dict]) -> None: ...
    def apagar(self, tabela: str, unidade_id: str, competencias: list) -> None: ...
    def upsert(self, tabela: str, linhas: list[dict], conflito: str) -> None: ...
    def garantir_mes_status(self, unidade_id: str, competencias: list) -> None: ...
    def total_itens_mes(self, unidade_id: str, competencia) -> Decimal | None: ...


@dataclass
class ResultadoImportacao:
    status: str                       # concluido | pendente | erro | duplicado
    mensagem: str
    importacao_id: str | None = None
    tipo: str | None = None
    instituicao: str | None = None
    periodo: dict | None = None
    linhas_lidas: int = 0
    linhas_gravadas: int = 0
    pendencias: list[dict] = field(default_factory=list)
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _agregar(chaves_valores):
    soma = defaultdict(Decimal)
    for k, v in chaves_valores:
        soma[k] += v
    return soma


def importar(repo: Repositorio, unidade_id: str, conteudo: bytes, nome_arquivo: str,
             tipo: str | None = None, usuario: str | None = None, reprocessar: bool = False,
             competencia: str | None = None) -> ResultadoImportacao:
    unidade = repo.obter_unidade(unidade_id)
    if unidade is None:
        return ResultadoImportacao("erro", "Unidade não encontrada.")

    try:
        leitura = ler_relatorio(conteudo, tipo, competencia)
    except ArquivoInvalido as e:
        return ResultadoImportacao("erro", str(e), erros=[str(e)])

    base = dict(tipo=leitura.tipo, instituicao=leitura.instituicao,
                periodo={"inicio": leitura.inicio, "fim": leitura.fim},
                linhas_lidas=len(leitura.linhas), avisos=list(leitura.avisos))

    # 1. O arquivo é do hospital da unidade?
    esperado = unidade.get("nome_no_apurasus")
    if leitura.extras.get("sem_instituicao_no_arquivo"):
        # a Matriz Custos Totais não informa o hospital: a conferência com os itens do mês (passo 4b) protege
        pass
    elif not esperado:
        msg = "Cadastre na unidade o nome do hospital como aparece nos relatórios do ApuraSUS."
        return ResultadoImportacao("erro", msg, erros=[msg], **base)
    elif nome_hospital(esperado) != nome_hospital(leitura.instituicao):
        msg = (f"O arquivo é de '{leitura.instituicao}', mas a unidade selecionada é '{unidade['nome']}'. "
               "Confira se escolheu a unidade e o arquivo corretos.")
        return ResultadoImportacao("erro", msg, erros=[msg], **base)

    # 2. Mesmo arquivo já importado?
    arquivo_hash = hashlib.sha256(conteudo).hexdigest()
    anterior = repo.buscar_importacao(unidade_id, arquivo_hash)
    if anterior and anterior["status"] == "concluido" and not reprocessar:
        return ResultadoImportacao("duplicado", "Este arquivo já foi importado.", importacao_id=anterior["id"], **base)

    campos = dict(tipo_relatorio=leitura.tipo, competencia_inicio=leitura.inicio, competencia_fim=leitura.fim,
                  arquivo_nome=nome_arquivo, status="processando", mensagem=None,
                  linhas_lidas=len(leitura.linhas), linhas_gravadas=None, linhas_pendentes=None)
    if anterior:
        importacao_id = anterior["id"]
        repo.atualizar_importacao(importacao_id, campos)
    else:
        importacao_id = repo.criar_importacao(dict(campos, unidade_id=unidade_id, arquivo_hash=arquivo_hash, criado_por=usuario))

    def encerrar(status, mensagem, **extra):
        repo.atualizar_importacao(importacao_id, dict(status=status, mensagem=mensagem,
                                  linhas_gravadas=extra.get("gravadas"), linhas_pendentes=extra.get("pendentes")))
        return ResultadoImportacao(status, mensagem, importacao_id=importacao_id,
                                   linhas_gravadas=extra.get("gravadas") or 0, pendencias=extra.get("pend", []),
                                   erros=extra.get("erros", []), **base)

    # 3. Conferências de totais do próprio arquivo
    if leitura.erros:
        return encerrar("erro", "O arquivo não passou nas conferências de totais.", erros=leitura.erros)

    # 4. Resolver nomes
    dims = repo.carregar_dimensoes(unidade_id)
    pend: dict[tuple, dict] = {}

    def pendencia(tp, nome, tipo_secao=None, ocorrencia=1, sugestoes=()):
        pend.setdefault((tp, nome, tipo_secao, ocorrencia), dict(
            tipo=tp, nome_original=nome, tipo_secao=tipo_secao, ocorrencia=ocorrencia,
            sugestoes=[{"nome": n, "similaridade": s} for n, s in sugestoes]))

    cache_item, cache_centro = {}, {}

    def item(nome):
        if nome not in cache_item:
            r = dims.resolver_item(nome, aproximado=(leitura.tipo == "custo_total_unidade"))
            cache_item[nome] = r.id
            if r.id is None:
                pendencia("item", nome, sugestoes=r.sugestoes)
        return cache_item[nome]

    def centro(nome, tipo_secao=None, ocorrencia=1):
        k = (nome, tipo_secao, ocorrencia)
        if k not in cache_centro:
            r = dims.resolver_centro(nome, tipo_secao, ocorrencia)
            cache_centro[k] = r.id
            if r.id is None:
                pendencia("centro", nome, tipo_secao, ocorrencia, r.sugestoes)
        return cache_centro[k]

    def centro_por_posicao(x):
        """Centros da Matriz Custos Totais. Nome único: resolve direto. Nome repetido: a posição em relação ao
        'Centro de Custos Externo' diz a seção (depois dele = final; antes = administrativo ou intermediário)."""
        nome, pos = x["centro_bruto"], x["posicao"]
        if not x["repetido"] or pos is None:
            return centro(nome, None, x["ocorrencia"])
        if pos in ("final", "externo"):
            return centro(nome, pos, x["ocorrencia"])
        for secao in ("administrativo", "intermediario"):
            r = dims.resolver_centro(nome, secao, x["ocorrencia"])
            if r.id is not None:
                cache_centro[(nome, secao, x["ocorrencia"])] = r.id
                return r.id
        return centro(nome, "administrativo", x["ocorrencia"])   # gera a pendência (tipo_secao administrativo)

    uid = unidade_id
    if leitura.tipo == "custo_total_unidade":
        brutas = [((x["competencia"], item(x["item_bruto"])), x["valor"]) for x in leitura.linhas]
    elif leitura.tipo == "matriz_item_centro":
        brutas = [((x["competencia"], item(x["item_bruto"]), centro(x["centro_bruto"], None, x["ocorrencia"])), x["valor"])
                  for x in leitura.linhas]
    elif leitura.tipo == "matriz_custos_totais":
        brutas = []
        for x in leitura.linhas:
            cc = centro_por_posicao(x)
            brutas.append(((x["competencia"], cc), (x["custo_direto"], x["custo_indireto"], x["custo_total"])))
    else:
        brutas = [((x["competencia"], centro(x["centro_bruto"], x["tipo_secao"]), x["item_producao"]),
                   (x["quantidade"], x["custo_unitario"])) for x in leitura.linhas]

    if pend:
        repo.substituir_pendencias(importacao_id, list(pend.values()))
        n = len(pend)
        return encerrar("pendente", f"{n} nome(s) sem correspondência. Resolva as pendências e reenvie o arquivo; nada foi gravado.",
                        pendentes=n, pend=list(pend.values()))
    repo.substituir_pendencias(importacao_id, [])

    # 5. Montar linhas
    tabela, conflito = TABELAS[leitura.tipo]
    if leitura.tipo == "custo_total_unidade":
        soma = _agregar(brutas)
        linhas = [dict(unidade_id=uid, competencia=c, ic_id=i, valor=v, importacao_id=importacao_id) for (c, i), v in soma.items()]
    elif leitura.tipo == "matriz_item_centro":
        soma = _agregar(brutas)
        linhas = [dict(unidade_id=uid, competencia=c, ic_id=i, cc_id=cc, valor=v, importacao_id=importacao_id)
                  for (c, i, cc), v in soma.items()]
    elif leitura.tipo == "matriz_custos_totais":
        acum: dict = {}
        for (c, cc), (cd, ci, ct) in brutas:      # dois centros locais para o mesmo centro padrão: somam
            a = acum.setdefault((c, cc), [Decimal(0)] * 3)
            a[0] += cd; a[1] += ci; a[2] += ct
        linhas = [dict(unidade_id=uid, competencia=c, cc_id=cc, custo_direto=a[0], custo_indireto=a[1],
                       custo_total=a[2], importacao_id=importacao_id) for (c, cc), a in acum.items()]
        # 4b. O Custo Direto total da matriz deve bater com o total dos itens do mês (protege contra mês/unidade errados)
        comp = leitura.inicio
        total_itens = repo.total_itens_mes(unidade_id, comp)
        soma_cd = sum((l["custo_direto"] for l in linhas), Decimal(0))
        if total_itens is None:
            base["avisos"].append(f"O Custo Total da Unidade de {comp:%m/%Y} ainda não foi importado: não foi possível "
                                  "conferir se o mês e a unidade deste arquivo estão corretos.")
        elif abs(soma_cd - total_itens) > TOLERANCIA_ITENS:
            msg = (f"O Custo Direto total da matriz ({soma_cd}) não bate com o total dos itens de {comp:%m/%Y} "
                   f"({total_itens}), diferença de {soma_cd - total_itens}. Confira se o mês informado e a unidade estão certos; nada foi gravado.")
            return encerrar("erro", msg, erros=[msg])
    else:
        grupos = defaultdict(list)
        for (c, cc, ip), (q, cu) in brutas:
            grupos[(c, cc, ip)].append((q, cu))
        linhas, diverg = [], []
        for (c, cc, ip), lst in grupos.items():
            q = sum((a or Decimal(0) for a, _ in lst), Decimal(0))
            if len(lst) == 1:
                cu = lst[0][1]
            else:   # dois centros locais para o mesmo centro padrão: custo unitário ponderado pela quantidade
                cu = (sum(((a or 0) * (b or 0) for a, b in lst), Decimal(0)) / q).quantize(Decimal("0.01")) if q else None
            linhas.append(dict(unidade_id=uid, competencia=c, cc_id=cc, item_producao=ip, quantidade=q,
                               custo_unitario=cu, importacao_id=importacao_id))
            padrao = dims.centro(cc).get("item_producao_padrao")
            if padrao and normalizar(padrao) != normalizar(ip):
                diverg.append(f"{dims.centro(cc)['nome']} ('{ip}' em vez de '{padrao}')")
        if diverg:
            unicos = sorted(set(diverg))
            base["avisos"].append(f"{len(unicos)} centro(s) com item de produção diferente do padrão da SESAP: "
                                  + "; ".join(unicos[:5]) + ("..." if len(unicos) > 5 else ""))

    # 6. Gravar
    competencias = sorted({l["competencia"] for l in linhas})
    if leitura.tipo in APAGAR_ANTES:
        repo.apagar(tabela, unidade_id, competencias)
    repo.upsert(tabela, linhas, conflito)
    repo.garantir_mes_status(unidade_id, competencias)
    return encerrar("concluido", f"{len(linhas)} linhas gravadas em {len(competencias)} mês(es).", gravadas=len(linhas))
