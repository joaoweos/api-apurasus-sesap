"""Casa os nomes que vêm nos relatórios com os cadastros padrão (dimensões).

Itens:   nome exato -> apelido confirmado pelo usuário -> (só no Custo Total da
         Unidade, cujos acentos chegam destruídos) correspondência aproximada.
Centros: de-para da unidade -> nome exato do cadastro padrão. O nome exato só é
         aceito se o tipo da seção (quando o relatório informa) for o mesmo do
         cadastro e se o nome aparecer uma única vez no arquivo.
Sem correspondência segura, o nome vira pendência e NADA é gravado.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from .comum import esqueleto, normalizar


def nome_hospital(texto: str) -> str:
    """'HOSPITAL DR JOSE PEDRO BEZERRA' == 'Hospital Dr. José Pedro Bezerra'."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", normalizar(texto))).strip()


@dataclass
class Resolucao:
    id: str | None
    metodo: str | None = None            # exato | alias | depara | aproximado
    sugestoes: list[tuple[str, float]] = field(default_factory=list)


class Dimensoes:
    def __init__(self, centros, itens, alias_itens=(), depara_centros=()):
        """centros: {id, nome, tipo, item_producao_padrao}; itens: {id, nome, grupo};
        alias_itens: {nome_normalizado, ic_id};
        depara_centros: {nome_no_apurasus, tipo_secao, ocorrencia, cc_pngc_id}."""
        self.centros = list(centros)
        self.itens = list(itens)
        self._cc = {normalizar(c["nome"]): c for c in self.centros}
        self._cc_id = {c["id"]: c for c in self.centros}
        self._ic = {normalizar(i["nome"]): i for i in self.itens}
        self._alias = {a["nome_normalizado"]: a["ic_id"] for a in alias_itens}
        self._depara = {
            (normalizar(d["nome_no_apurasus"]), (d.get("tipo_secao") or None), int(d.get("ocorrencia") or 1)): d["cc_pngc_id"]
            for d in depara_centros
        }

    # ------------------------------------------------------------------ itens
    def resolver_item(self, nome_bruto: str, aproximado: bool = False) -> Resolucao:
        n = normalizar(nome_bruto)
        if n in self._ic:
            return Resolucao(self._ic[n]["id"], "exato")
        if n in self._alias:
            return Resolucao(self._alias[n], "alias")
        if aproximado:
            sk = esqueleto(nome_bruto)
            pontos = sorted(
                ((difflib.SequenceMatcher(None, sk, esqueleto(i["nome"])).ratio(), i) for i in self.itens),
                key=lambda x: -x[0],
            )
            if pontos and pontos[0][0] >= 0.92 and (len(pontos) == 1 or pontos[0][0] - pontos[1][0] >= 0.03):
                return Resolucao(pontos[0][1]["id"], "aproximado")
            return Resolucao(None, None, [(i["nome"], round(s, 3)) for s, i in pontos[:3] if s >= 0.5])
        pontos = sorted(
            ((difflib.SequenceMatcher(None, n, normalizar(i["nome"])).ratio(), i) for i in self.itens),
            key=lambda x: -x[0],
        )
        return Resolucao(None, None, [(i["nome"], round(s, 3)) for s, i in pontos[:3] if s >= 0.6])

    # ---------------------------------------------------------------- centros
    def resolver_centro(self, nome_bruto: str, tipo_secao: str | None = None, ocorrencia: int = 1) -> Resolucao:
        n = normalizar(nome_bruto)
        for chave in ((n, tipo_secao, ocorrencia), (n, None, ocorrencia)):
            if chave in self._depara:
                return Resolucao(self._depara[chave], "depara")
        c = self._cc.get(n)
        if c is not None and ocorrencia == 1 and (tipo_secao is None or normalizar(c["tipo"]) == tipo_secao):
            return Resolucao(c["id"], "exato")
        pontos = sorted(
            ((difflib.SequenceMatcher(None, n, normalizar(x["nome"])).ratio()
              + (0.05 if tipo_secao and normalizar(x["tipo"]) == tipo_secao else 0), x) for x in self.centros),
            key=lambda x: -x[0],
        )
        return Resolucao(None, None, [(x["nome"], round(min(s, 1.0), 3)) for s, x in pontos[:3] if s >= 0.5])

    def centro(self, cc_id: str) -> dict:
        return self._cc_id[cc_id]
