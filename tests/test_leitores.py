"""Testes dos leitores. Usam arquivos reais do ApuraSUS, que NÃO devem ir para o
GitHub: aponte AMOSTRAS_DIR para a pasta onde eles estão.

    AMOSTRAS_DIR=/caminho/das/amostras pytest -q
"""
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from importador import ArquivoInvalido, detectar_tipo, ler_relatorio
from importador.comum import decodificar, parse_numero

PASTA = Path(os.environ.get("AMOSTRAS_DIR", "/mnt/user-data/uploads"))
CUSTO_TOTAL = PASTA / "Relatorio_Custo_Total_da_Unidade.csv"
MATRIZ = PASTA / "Relatorio_Matriz_Item_Custo_CentroCusto__2_.csv"
PRODUTO = PASTA / "Relatorio_CentroCusto_Produto.csv"
FORMACAO_BRUTO = PASTA / "Relatorio_Formacao_do_Custo_Total_dos_Centros_de_Custo__1__1.csv"

precisa_amostras = pytest.mark.skipif(not CUSTO_TOTAL.exists(), reason="amostras não encontradas")


# --- números -----------------------------------------------------------------
@pytest.mark.parametrize("texto,esperado", [
    ("9053073,58", "9053073.58"),
    ("3.180,00", "3180.00"),
    ("1037049,529999999952319", "1037049.53"),       # ruído de ponto flutuante
    ("50,069,160,000,000,000,000", "50069.16"),      # formato estourado
    ("56,626,560,000,000,000,000", "56626.56"),
    ("0,00", "0.00"),
    ("-12,5", "-12.50"),
    ("10321,01", "10321.01"),
])
def test_parse_numero(texto, esperado):
    assert parse_numero(texto) == Decimal(esperado)


@pytest.mark.parametrize("vazio", ["", "  ", "-", None])
def test_parse_numero_vazio(vazio):
    assert parse_numero(vazio) is None


def test_parse_numero_invalido():
    with pytest.raises(ArquivoInvalido):
        parse_numero("abc")


# --- codificação ---------------------------------------------------------------
def test_decodifica_latin1_e_utf8():
    assert "Serviços" in decodificar("Serviços;1".encode("latin-1"))
    assert "Serviços" in decodificar("Serviços;1".encode("utf-8"))


@precisa_amostras
def test_formacao_bruto_e_rejeitado_com_mensagem_clara():
    conteudo = FORMACAO_BRUTO.read_bytes()
    assert detectar_tipo(conteudo) == "formacao_custo_centro"
    with pytest.raises(ArquivoInvalido, match="um centro por arquivo"):
        ler_relatorio(conteudo)


# --- Custo Total da Unidade ----------------------------------------------------
@precisa_amostras
def test_custo_total_unidade():
    r = ler_relatorio(CUSTO_TOTAL.read_bytes())
    assert r.instituicao == "HOSPITAL DR JOSE PEDRO BEZERRA"
    assert (r.inicio, r.fim) == (date(2025, 1, 1), date(2025, 12, 1))
    assert len(r.competencias) == 12
    assert r.ok, r.erros                     # itens = grupos = total geral nos 12 meses
    assert r.extras["total_geral"][date(2025, 1, 1)] == Decimal("11691451.83")
    assert len(r.linhas) == 53 * 12


@precisa_amostras
def test_custo_total_recupera_celulas_estouradas_da_agua():
    r = ler_relatorio(CUSTO_TOTAL.read_bytes())
    agua = {x["competencia"].month: x["valor"] for x in r.linhas if x["item_esqueleto"].endswith("ua e esgoto")}
    assert agua[3] == Decimal("50069.16")
    assert agua[5] == Decimal("56626.56")
    assert agua[6] == Decimal("55721.29")


@precisa_amostras
def test_custo_total_detecta_divergencia_de_subtotal():
    # adultera uma célula: a conferência precisa acusar
    texto = decodificar(CUSTO_TOTAL.read_bytes()).replace("9053073,58", "9053099,58", 1)
    r = ler_relatorio(texto.encode("utf-8"))
    assert not r.ok and any("Pessoal" in e for e in r.erros)


# --- Matriz Item x Centro ------------------------------------------------------
@precisa_amostras
def test_matriz_item_centro():
    r = ler_relatorio(MATRIZ.read_bytes())
    assert (r.inicio, r.fim) == (date(2025, 1, 1), date(2025, 1, 1))
    assert len(r.extras["centros"]) == 77 and len(r.extras["itens"]) == 46
    assert r.extras["total"] == Decimal("11691451.66")
    obst = [c for c in r.extras["centros"] if c["centro_bruto"] == "Centro Obstétrico"]
    assert [c["ocorrencia"] for c in obst] == [1, 2]      # nome duplicado desambiguado
    assert any("repetido" in a for a in r.avisos)


@precisa_amostras
def test_matriz_fecha_com_custo_total_da_unidade():
    mat = ler_relatorio(MATRIZ.read_bytes())
    ct = ler_relatorio(CUSTO_TOTAL.read_bytes())
    assert abs(mat.extras["total"] - ct.extras["total_geral"][date(2025, 1, 1)]) <= Decimal("1.00")


@precisa_amostras
def test_matriz_custo_direto_do_almoxarifado():
    r = ler_relatorio(MATRIZ.read_bytes())
    alm = next(c for c in r.extras["centros"] if c["centro_bruto"] == "Almoxarifado")
    assert alm["custo_direto"] == Decimal("21016.91")      # relatório de Formação: 21016,90


# --- Centro x Produto ----------------------------------------------------------
@precisa_amostras
def test_centro_produto():
    r = ler_relatorio(PRODUTO.read_bytes())
    assert r.ok, r.erros
    assert len(r.linhas) == 777 and len(r.competencias) == 12
    jan = [x for x in r.linhas if x["competencia"] == date(2025, 1, 1)]
    alm = next(x for x in jan if x["centro_bruto"] == "Almoxarifado")
    assert (alm["tipo_secao"], alm["item_producao"], alm["quantidade"], alm["custo_unitario"]) == \
           ("administrativo", "Item Atendido", Decimal("21006.00"), Decimal("2.12"))


@precisa_amostras
def test_centro_produto_desambigua_centro_obstetrico_pela_secao():
    r = ler_relatorio(PRODUTO.read_bytes())
    jan = [x for x in r.linhas if x["competencia"] == date(2025, 1, 1) and x["centro_bruto"] == "Centro Obstétrico"]
    assert {(x["tipo_secao"], x["item_producao"]) for x in jan} == {
        ("administrativo", "Item Dispensado"), ("final", "Parto")}


# --- tipo errado -----------------------------------------------------------------
@precisa_amostras
def test_tipo_declarado_diferente_do_arquivo():
    with pytest.raises(ArquivoInvalido, match="parece ser do tipo"):
        ler_relatorio(MATRIZ.read_bytes(), tipo="centro_produto")
