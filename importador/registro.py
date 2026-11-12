"""Escolhe o leitor certo para cada tipo de relatório."""
from __future__ import annotations

from . import centro_produto, custo_total_unidade, matriz_custos_totais, matriz_item_centro
from .comum import ArquivoInvalido, Leitura, decodificar, normalizar, parse_competencia

_LEITORES = {
    custo_total_unidade.TIPO: custo_total_unidade.ler,
    matriz_item_centro.TIPO: matriz_item_centro.ler,
    centro_produto.TIPO: centro_produto.ler,
    matriz_custos_totais.TIPO: matriz_custos_totais.ler,
}
TIPOS_SUPORTADOS = tuple(_LEITORES)


def detectar_tipo(conteudo: bytes) -> str | None:
    """Identifica o relatório pelo cabeçalho (palavras ASCII, que sobrevivem
    à destruição dos acentos)."""
    amostra = normalizar(decodificar(conteudo)[:4000])
    if "matriz de resultados" in amostra and "custo indireto" in amostra and "custo total" in amostra:
        return matriz_custos_totais.TIPO
    if "de/para" in amostra:
        return matriz_item_centro.TIPO
    if "centro custo" in amostra and "item produ" in amostra:
        return centro_produto.TIPO
    if "item custo - sintetico" in amostra:
        return custo_total_unidade.TIPO
    if "custo transferido" in amostra:
        return "formacao_custo_centro"   # um centro por arquivo: não é entrada mensal
    return None


def ler_relatorio(conteudo: bytes, tipo: str | None = None, competencia=None) -> Leitura:
    detectado = detectar_tipo(conteudo)
    if not tipo:            # None ou texto vazio (formulários costumam enviar vazio)
        tipo = detectado
    if tipo not in _LEITORES:
        if tipo == "formacao_custo_centro":
            raise ArquivoInvalido("Este é o relatório 'Formação do Custo Total' (um centro por arquivo). "
                                  "Ele não é usado na importação mensal.")
        raise ArquivoInvalido("Tipo de relatório não reconhecido. Tipos aceitos: "
                              + ", ".join(TIPOS_SUPORTADOS) + ".")
    if detectado and detectado != tipo:
        raise ArquivoInvalido(f"O arquivo parece ser do tipo '{detectado}', mas foi enviado como '{tipo}'.")
    if tipo != custo_total_unidade.TIPO and "\ufffd" in decodificar(conteudo):
        raise ArquivoInvalido("O arquivo chegou com os acentos corrompidos (caracteres '\ufffd'). Isso acontece quando ele é aberto e "
                              "salvo em planilha ou alterado no caminho. Envie o arquivo exatamente como baixado do ApuraSUS.")
    if tipo == matriz_custos_totais.TIPO:   # o arquivo não traz o mês: vem do envio
        return matriz_custos_totais.ler(conteudo, parse_competencia(competencia))
    return _LEITORES[tipo](conteudo)
