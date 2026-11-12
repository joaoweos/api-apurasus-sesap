"""Matriz 'Custos Totais' (Movimentação > Executar Alocação Recíproca > Custos Totais).

Layout: 'Matriz de resultados' / 'Centro de Custo;Custo Direto;Custo Indireto;Custo Total'.
Uma linha por centro de custo, na ordem do ApuraSUS: administrativos, intermediários,
'Centro de Custos Externo' e finais. Termina com uma linha 'TOTAIS'.

O arquivo NÃO informa o hospital nem o mês. O mês vem do envio (parâmetro) e o hospital
da unidade escolhida; a conferência com os itens do mês (em gravar.py) protege contra engano.

Conferência do manual do ApuraSUS: soma do Custo Direto de todos os centros = soma do
Custo Total dos centros finais e externos (tolerância de R$ 1,00).

Nomes repetidos (ex.: dois 'Centro Obstétrico', um administrativo e um final) são
diferenciados pela posição: antes do centro externo, no externo ou depois dele (finais).
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal

from .comum import ArquivoInvalido, Leitura, ler_tabela, normalizar, parse_numero

TIPO = "matriz_custos_totais"
TOLERANCIA_REGRA_MANUAL = Decimal("1.00")
TOLERANCIA_LINHA = Decimal("0.02")


def ler(conteudo: bytes, competencia: date | None = None) -> Leitura:
    if competencia is None:
        raise ArquivoInvalido("Este relatório não traz o mês no arquivo. Informe o mês de referência (AAAA-MM).")
    linhas = ler_tabela(conteudo)
    idx = next((i for i, l in enumerate(linhas)
                if l and normalizar(l[0]).replace("  ", " ") == "centro de custo"
                and len(l) > 3 and normalizar(l[1]) == "custo direto"), None)
    if idx is None:
        raise ArquivoInvalido("Cabeçalho 'Centro de Custo; Custo Direto; Custo Indireto; Custo Total' não encontrado.")

    brutos: list[tuple[str, Decimal, Decimal, Decimal]] = []
    totais: Decimal | None = None
    erros: list[str] = []
    avisos: list[str] = []
    for l in linhas[idx + 1:]:
        nome = (l[0] if l else "").strip()
        if not nome:
            continue
        if normalizar(nome).startswith("totais"):
            totais = parse_numero(l[3]) if len(l) > 3 else None
            continue
        if len(l) < 4:
            raise ArquivoInvalido(f"Linha incompleta para o centro '{nome}'.")
        cd, ci, ct = (parse_numero(l[j]) or Decimal(0) for j in (1, 2, 3))
        if abs(cd + ci - ct) > TOLERANCIA_LINHA:
            erros.append(f"{nome}: Direto {cd} + Indireto {ci} diferente do Total {ct}.")
        brutos.append((nome, cd, ci, ct))
    if not brutos:
        raise ArquivoInvalido("Nenhum centro de custo encontrado na matriz.")

    # posição de cada linha em relação ao 'Centro de Custos Externo'
    i_ext = next((i for i, b in enumerate(brutos) if normalizar(b[0]).startswith("centro de custos externo")), None)
    contagem_nome = Counter(normalizar(b[0]) for b in brutos)
    ocorr: Counter = Counter()
    saida, centros = [], []
    for i, (nome, cd, ci, ct) in enumerate(brutos):
        if i_ext is None:
            posicao = None
        else:
            posicao = "antes_externo" if i < i_ext else ("externo" if i == i_ext else "final")
        ocorr[(normalizar(nome), posicao)] += 1
        saida.append({"competencia": competencia, "centro_bruto": nome, "posicao": posicao,
                      "repetido": contagem_nome[normalizar(nome)] > 1,
                      "ocorrencia": ocorr[(normalizar(nome), posicao)],
                      "custo_direto": cd, "custo_indireto": ci, "custo_total": ct})
        centros.append(nome)

    soma_cd = sum((b[1] for b in brutos), Decimal(0))
    soma_ct = sum((b[3] for b in brutos), Decimal(0))
    soma_ct_finais = None
    if i_ext is None:
        avisos.append("Centro de Custos Externo não encontrado: a conferência do manual (Direto = Total dos finais e externo) não foi feita.")
    else:
        soma_ct_finais = sum((b[3] for b in brutos[i_ext:]), Decimal(0))
        if abs(soma_cd - soma_ct_finais) > TOLERANCIA_REGRA_MANUAL:
            erros.append(f"Conferência do manual falhou: soma do Custo Direto ({soma_cd}) difere da soma do Custo Total dos "
                         f"centros finais e externo ({soma_ct_finais}) em {soma_cd - soma_ct_finais}. "
                         "A alocação recíproca pode não ter sido executada ou o arquivo está incompleto.")
    if totais is not None and soma_ct_finais is not None and abs(totais - soma_ct_finais) > TOLERANCIA_REGRA_MANUAL:
        avisos.append(f"A linha TOTAIS do arquivo ({totais}) difere da soma calculada dos finais e externo ({soma_ct_finais}).")
    for nome, n in Counter(b[0] for b in brutos).items():
        if n > 1:
            avisos.append(f"Nome de centro repetido {n}x: '{nome}' (diferenciados pela posição no arquivo).")

    return Leitura(TIPO, "", competencia, competencia, saida, avisos, erros,
                   {"centros": centros, "soma_custo_direto": soma_cd, "soma_custo_total": soma_ct,
                    "soma_ct_finais_externo": soma_ct_finais, "total_declarado_no_arquivo": totais,
                    "sem_instituicao_no_arquivo": True})
