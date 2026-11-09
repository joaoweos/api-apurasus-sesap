"""Acesso a dados: uma versão em memória (testes) e uma para o Supabase."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from .comum import normalizar
from .resolver import Dimensoes


def _json(valor):
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    return valor


def _linha_json(linha: dict) -> dict:
    return {k: _json(v) for k, v in linha.items()}


# ----------------------------------------------------------------------------
class RepositorioMemoria:
    """Banco falso, só para testes."""

    def __init__(self, unidades, centros, itens, alias_itens=(), depara_centros=(), usuarios=()):
        self.unidades = {u["id"]: u for u in unidades}
        self.usuarios = {u["email"]: u for u in usuarios}
        self.centros, self.itens = list(centros), list(itens)
        self.alias_itens, self.depara_centros = list(alias_itens), list(depara_centros)
        self.importacoes: dict[str, dict] = {}
        self.pendencias: dict[str, list] = {}
        self.tabelas: dict[str, dict] = {}
        self.mes_status: dict[tuple, str] = {}

    def obter_unidade(self, unidade_id):
        return self.unidades.get(unidade_id)

    def obter_usuario(self, email):
        return self.usuarios.get(email)

    def listar_unidades(self):
        return [{"id": u["id"], "nome": u["nome"]} for u in self.unidades.values()]

    def centro_existe(self, cc_id):
        return any(c["id"] == cc_id for c in self.centros)

    def item_existe(self, ic_id):
        return any(i["id"] == ic_id for i in self.itens)

    def listar_importacoes(self, unidade_id, limite=20):
        itens = [i for i in self.importacoes.values() if i["unidade_id"] == unidade_id]
        return itens[-limite:][::-1]

    def carregar_dimensoes(self, unidade_id):
        dep = [d for d in self.depara_centros if d["unidade_id"] == unidade_id]
        return Dimensoes(self.centros, self.itens, self.alias_itens, dep)

    def buscar_importacao(self, unidade_id, arquivo_hash):
        return next((i for i in self.importacoes.values()
                     if i["unidade_id"] == unidade_id and i["arquivo_hash"] == arquivo_hash), None)

    def criar_importacao(self, dados):
        i = str(uuid.uuid4())
        self.importacoes[i] = dict(dados, id=i)
        return i

    def atualizar_importacao(self, importacao_id, campos):
        self.importacoes[importacao_id].update(campos)

    def substituir_pendencias(self, importacao_id, pendencias):
        self.pendencias[importacao_id] = pendencias

    def apagar(self, tabela, unidade_id, competencias):
        t = self.tabelas.setdefault(tabela, {})
        for k in [k for k, l in t.items() if l["unidade_id"] == unidade_id and l["competencia"] in competencias]:
            del t[k]

    def upsert(self, tabela, linhas, conflito):
        cols = conflito.split(",")
        t = self.tabelas.setdefault(tabela, {})
        for l in linhas:
            t[tuple(l[c] for c in cols)] = l

    def garantir_mes_status(self, unidade_id, competencias):
        for c in competencias:
            self.mes_status.setdefault((unidade_id, c), "parcial")

    def linhas(self, tabela):
        return list(self.tabelas.get(tabela, {}).values())

    # de-para / alias (usados pelos endpoints)
    def salvar_depara_centro(self, d):
        self.depara_centros = [x for x in self.depara_centros
                               if not (x["unidade_id"] == d["unidade_id"] and normalizar(x["nome_no_apurasus"]) == normalizar(d["nome_no_apurasus"])
                                       and (x.get("tipo_secao") or None) == (d.get("tipo_secao") or None)
                                       and int(x.get("ocorrencia") or 1) == int(d.get("ocorrencia") or 1))]
        self.depara_centros.append(d)

    def salvar_alias_item(self, nome, ic_id, usuario=None):
        self.alias_itens = [a for a in self.alias_itens if a["nome_normalizado"] != normalizar(nome)]
        self.alias_itens.append({"nome_normalizado": normalizar(nome), "ic_id": ic_id})


# ----------------------------------------------------------------------------
class RepositorioSupabase:
    """Implementação para o Supabase (usa a chave de serviço). Ainda não foi
    exercitada contra o banco real: teste com um arquivo pequeno primeiro."""

    LOTE = 500

    def __init__(self, cliente):
        self.sb = cliente

    def _todas(self, tabela, colunas="*", **filtros):
        q = self.sb.table(tabela).select(colunas)
        for k, v in filtros.items():
            q = q.eq(k, v)
        return q.limit(5000).execute().data

    def obter_unidade(self, unidade_id):
        dados = self.sb.table("unidades").select("id,nome,nome_no_apurasus").eq("id", unidade_id).limit(1).execute().data
        return dados[0] if dados else None

    def centro_existe(self, cc_id):
        return bool(self.sb.table("dim_cc_pngc").select("id").eq("id", cc_id).limit(1).execute().data)

    def item_existe(self, ic_id):
        return bool(self.sb.table("dim_ic_pngc").select("id").eq("id", ic_id).limit(1).execute().data)

    def listar_unidades(self):
        return self.sb.table("unidades").select("id,nome").eq("ativa", True).order("nome").execute().data

    def obter_usuario(self, email):
        d = self.sb.table("usuarios_unidade").select("email,nome,unidade_id,papel,ativo").eq("email", email).limit(1).execute().data
        return d[0] if d else None

    def listar_importacoes(self, unidade_id, limite=20):
        return (self.sb.table("importacoes")
                .select("id,tipo_relatorio,competencia_inicio,competencia_fim,arquivo_nome,status,linhas_gravadas,linhas_pendentes,mensagem,criado_por,created_at")
                .eq("unidade_id", unidade_id).order("created_at", desc=True).limit(limite).execute().data)

    def carregar_dimensoes(self, unidade_id):
        return Dimensoes(
            self._todas("dim_cc_pngc", "id,nome,tipo,item_producao_padrao"),
            self._todas("dim_ic_pngc", "id,nome,grupo"),
            self._todas("dim_ic_alias", "nome_normalizado,ic_id"),
            self._todas("dim_cc_unidade", "nome_no_apurasus,tipo_secao,ocorrencia,cc_pngc_id", unidade_id=unidade_id),
        )

    def buscar_importacao(self, unidade_id, arquivo_hash):
        d = (self.sb.table("importacoes").select("id,status").eq("unidade_id", unidade_id)
             .eq("arquivo_hash", arquivo_hash).limit(1).execute().data)
        return d[0] if d else None

    def criar_importacao(self, dados):
        return self.sb.table("importacoes").insert(_linha_json(dados)).execute().data[0]["id"]

    def atualizar_importacao(self, importacao_id, campos):
        self.sb.table("importacoes").update(_linha_json(campos)).eq("id", importacao_id).execute()

    def substituir_pendencias(self, importacao_id, pendencias):
        self.sb.table("importacao_pendencias").delete().eq("importacao_id", importacao_id).execute()
        if pendencias:
            self.sb.table("importacao_pendencias").insert(
                [dict(importacao_id=importacao_id, tipo=p["tipo"], nome_original=p["nome_original"],
                      tipo_secao=p.get("tipo_secao"), ocorrencia=p.get("ocorrencia", 1), sugestoes=p.get("sugestoes", []))
                 for p in pendencias]).execute()

    def apagar(self, tabela, unidade_id, competencias):
        self.sb.table(tabela).delete().eq("unidade_id", unidade_id).in_("competencia", [c.isoformat() for c in competencias]).execute()

    def upsert(self, tabela, linhas, conflito):
        for i in range(0, len(linhas), self.LOTE):
            self.sb.table(tabela).upsert([_linha_json(l) for l in linhas[i:i + self.LOTE]], on_conflict=conflito).execute()

    def garantir_mes_status(self, unidade_id, competencias):
        self.sb.table("mes_status").upsert(
            [dict(unidade_id=unidade_id, competencia=c.isoformat(), status="parcial") for c in competencias],
            on_conflict="unidade_id,competencia", ignore_duplicates=True).execute()

    def salvar_depara_centro(self, d):
        q = (self.sb.table("dim_cc_unidade").select("id").eq("unidade_id", d["unidade_id"])
             .eq("nome_no_apurasus", d["nome_no_apurasus"]).eq("ocorrencia", int(d.get("ocorrencia") or 1)))
        q = q.eq("tipo_secao", d["tipo_secao"]) if d.get("tipo_secao") else q.is_("tipo_secao", "null")
        existente = q.limit(1).execute().data
        if existente:
            self.sb.table("dim_cc_unidade").update({"cc_pngc_id": d["cc_pngc_id"], "ativo": True}).eq("id", existente[0]["id"]).execute()
        else:
            self.sb.table("dim_cc_unidade").insert(d).execute()

    def salvar_alias_item(self, nome, ic_id, usuario=None):
        self.sb.table("dim_ic_alias").upsert(
            {"nome_normalizado": normalizar(nome), "ic_id": ic_id, "criado_por": usuario}, on_conflict="nome_normalizado").execute()

    def listar_dimensoes(self):
        return {"centros": self._todas("dim_cc_pngc", "id,codigo,nome,tipo,item_producao_padrao"),
                "itens": self._todas("dim_ic_pngc", "id,codigo,nome,grupo")}
