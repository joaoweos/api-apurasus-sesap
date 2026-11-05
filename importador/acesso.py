"""Quem pode fazer o quê. A regra fica aqui (testável), e não espalhada no Make ou no app.

Papéis:
  master_sesap    - vê e importa em qualquer unidade; é o único que cria apelidos de itens.
  gestor_unidade  - importa e resolve pendências só na própria unidade.
  leitura_unidade - só consulta a própria unidade.
A unidade do gestor vem SEMPRE do cadastro (usuarios_unidade), nunca do pedido.
"""
from __future__ import annotations


class AcessoNegado(Exception):
    """Pedido recusado por falta de permissão."""


def normalizar_email(email: str | None) -> str:
    return (email or "").strip().lower()


def usuario_ativo(repo, email: str | None) -> dict:
    e = normalizar_email(email)
    if not e:
        raise AcessoNegado("E-mail do usuário não informado.")
    u = repo.obter_usuario(e)
    if not u or not u.get("ativo", True):
        raise AcessoNegado("Usuário sem permissão neste sistema.")
    return u


def unidade_permitida(usuario: dict, unidade_pedida: str | None, escrita: bool) -> str:
    papel = usuario["papel"]
    if escrita and papel == "leitura_unidade":
        raise AcessoNegado("Seu perfil permite apenas consultar.")
    if papel == "master_sesap":
        if not unidade_pedida:
            raise AcessoNegado("Informe a unidade.")
        return str(unidade_pedida)
    if unidade_pedida and str(unidade_pedida) != str(usuario["unidade_id"]):
        raise AcessoNegado("Sem permissão para esta unidade.")
    return str(usuario["unidade_id"])


def exigir_master(usuario: dict) -> None:
    if usuario["papel"] != "master_sesap":
        raise AcessoNegado("Ação restrita ao usuário master da SESAP.")
