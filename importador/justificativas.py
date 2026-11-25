"""Justificativas dos alertas da Qualidade dos dados.

A gestora da unidade explica por que um item ou centro variou muito. A regra de quem pode escrever
fica em importador/acesso.py (gestor da unidade e master); aqui fica só a validação do conteúdo.
"""
from __future__ import annotations

import uuid

# motivo (chave gravada no banco) -> rótulo mostrado no app
MOTIVOS = {
    "sazonalidade": "Variação sazonal ou normal do período",
    "contrato": "Contrato novo, encerrado ou reajustado",
    "lancamento_fora_do_mes": "Lançamento feito fora do mês (competência)",
    "correcao_apropriacao": "Correção ou erro de apropriação",
    "servico_outro_hospital": "Serviço prestado a outro hospital",
    "outro": "Outro motivo (descreva)",
}
TIPOS = ("item", "centro")
TEXTO_MAXIMO = 1000


class JustificativaInvalida(ValueError):
    """Conteúdo recusado; a mensagem diz o que corrigir."""


def validar(tipo: str, referencia: str, motivo: str | None, texto: str | None, remover: bool = False) -> dict:
    """Confere o pedido e devolve os campos já limpos (referencia, motivo, texto)."""
    if tipo not in TIPOS:
        raise JustificativaInvalida("tipo deve ser 'item' ou 'centro'.")
    ref = (referencia or "").strip()
    if not ref or len(ref) > 200:
        raise JustificativaInvalida("Informe o item ou o centro a justificar.")
    if tipo == "centro":
        try:
            ref = str(uuid.UUID(ref))
        except ValueError:
            raise JustificativaInvalida("referencia inválida: para centro, informe o id do centro (uuid).")
    if remover:
        return {"referencia": ref, "motivo": None, "texto": None}
    if motivo not in MOTIVOS:
        raise JustificativaInvalida("Motivo inválido. Use um destes: " + ", ".join(MOTIVOS) + ".")
    t = (texto or "").strip() or None
    if t and len(t) > TEXTO_MAXIMO:
        raise JustificativaInvalida(f"O texto passa de {TEXTO_MAXIMO} caracteres.")
    if motivo == "outro" and not t:
        raise JustificativaInvalida("Para 'Outro motivo', descreva a justificativa no texto.")
    return {"referencia": ref, "motivo": motivo, "texto": t}
