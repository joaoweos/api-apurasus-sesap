"""Quem pode fazer o quê. A regra fica aqui (testável), e não espalhada no Make ou no app.

Papéis (cada vínculo liga um e-mail a um papel e, nos papéis de unidade, a uma unidade):
  master_sesap     - administra tudo: vê e importa em qualquer unidade, cadastra unidades e usuários,
                     cria apelidos de itens e pode fechar mês mesmo com relatório faltando.
  analista_sesap   - só consulta, em todas as unidades (inclusive o comparativo). Não altera nada.
  gestor_unidade   - importa, resolve pendências e fecha meses na(s) unidade(s) dele;
                     cadastra usuários de leitura da própria unidade.
  leitura_unidade  - só consulta a(s) unidade(s) dele.
Um e-mail pode ter vários vínculos (ex.: um técnico que acompanha três hospitais).
A unidade de quem não é da SESAP vem SEMPRE do cadastro, nunca do pedido.
"""
from __future__ import annotations

PAPEIS_SESAP = ("master_sesap", "analista_sesap")
PAPEIS_UNIDADE = ("gestor_unidade", "leitura_unidade")
PAPEIS = PAPEIS_SESAP + PAPEIS_UNIDADE
_FORCA = {p: i for i, p in enumerate(PAPEIS)}  # menor = mais poderoso

ROTULOS = {"master_sesap": "Master SESAP", "analista_sesap": "Analista SESAP",
           "gestor_unidade": "Gestor da unidade", "leitura_unidade": "Leitura da unidade"}


class AcessoNegado(Exception):
    """Pedido recusado por falta de permissão."""


def normalizar_email(email: str | None) -> str:
    return (email or "").strip().lower()


def _vinculos_ativos(u: dict) -> list[dict]:
    if "vinculos" in u:
        v = u["vinculos"]
    else:  # formato antigo (um papel, uma unidade)
        v = [{"papel": u["papel"], "unidade_id": u.get("unidade_id"), "ativo": True}]
    return [x for x in v if x.get("ativo", True)]


def usuario_ativo(repo, email: str | None) -> dict:
    """Devolve o usuário com `vinculos` (só os ativos), `papel` (o mais forte) e
    `unidade_id` (a única unidade, quando ele tem uma só)."""
    e = normalizar_email(email)
    if not e:
        raise AcessoNegado("E-mail do usuário não informado.")
    u = repo.obter_usuario(e)
    if not u or not u.get("ativo", True):
        raise AcessoNegado("Usuário sem permissão neste sistema.")
    v = _vinculos_ativos(u)
    if not v:
        raise AcessoNegado("Usuário sem permissão neste sistema.")
    unidades = {str(x["unidade_id"]) for x in v if x.get("unidade_id")}
    return {**u, "vinculos": v,
            "papel": min((x["papel"] for x in v), key=lambda p: _FORCA[p]),
            "unidade_id": next(iter(unidades)) if len(unidades) == 1 else None}


def eh_sesap(u: dict) -> bool:
    return u["papel"] in PAPEIS_SESAP


def papel_na_unidade(u: dict, unidade_id: str) -> str | None:
    """Papel efetivo do usuário numa unidade (None = sem acesso)."""
    if u["papel"] in PAPEIS_SESAP:
        return u["papel"]
    papeis = [x["papel"] for x in u["vinculos"] if str(x.get("unidade_id")) == str(unidade_id)]
    return min(papeis, key=lambda p: _FORCA[p]) if papeis else None


def unidade_permitida(usuario: dict, unidade_pedida: str | None, escrita: bool) -> str:
    papel = usuario["papel"]
    if papel in PAPEIS_SESAP:
        if escrita and papel != "master_sesap":
            raise AcessoNegado("Seu perfil permite apenas consultar.")
        if not unidade_pedida:
            raise AcessoNegado("Informe a unidade.")
        return str(unidade_pedida)
    minhas = {str(x["unidade_id"]): x["papel"] for x in usuario["vinculos"] if x.get("unidade_id")}
    if unidade_pedida:
        uid = str(unidade_pedida)
        if uid not in minhas:
            raise AcessoNegado("Sem permissão para esta unidade.")
    elif len(minhas) == 1:
        uid = next(iter(minhas))
    else:
        raise AcessoNegado("Informe a unidade.")
    if escrita and minhas[uid] == "leitura_unidade":
        raise AcessoNegado("Seu perfil permite apenas consultar.")
    return uid


def exigir_master(usuario: dict) -> None:
    if usuario["papel"] != "master_sesap":
        raise AcessoNegado("Ação restrita ao usuário master da SESAP.")


def exigir_sesap(usuario: dict) -> None:
    if usuario["papel"] not in PAPEIS_SESAP:
        raise AcessoNegado("Ação restrita à equipe da SESAP.")


def unidades_do_usuario(usuario: dict) -> list[str] | None:
    """None = todas (SESAP); senão, a lista de unidades dos vínculos."""
    if usuario["papel"] in PAPEIS_SESAP:
        return None
    return sorted({str(x["unidade_id"]) for x in usuario["vinculos"] if x.get("unidade_id")})


def exigir_gestao_de_vinculo(usuario: dict, papel_alvo: str, unidade_alvo: str | None) -> None:
    """Quem pode criar, alterar ou desativar um vínculo.
    Master: qualquer um. Gestor: só 'leitura_unidade' numa unidade em que ele é gestor."""
    if papel_alvo not in PAPEIS:
        raise AcessoNegado(f"Papel inválido: {papel_alvo}.")
    if papel_alvo in PAPEIS_SESAP and unidade_alvo:
        raise AcessoNegado("Papéis da SESAP não têm unidade.")
    if papel_alvo in PAPEIS_UNIDADE and not unidade_alvo:
        raise AcessoNegado("Informe a unidade deste vínculo.")
    if usuario["papel"] == "master_sesap":
        return
    if (papel_alvo == "leitura_unidade" and unidade_alvo
            and papel_na_unidade(usuario, unidade_alvo) == "gestor_unidade"):
        return
    raise AcessoNegado("Você só pode cadastrar usuários de leitura da sua própria unidade.")
