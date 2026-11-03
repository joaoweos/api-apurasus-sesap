import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["API_KEY"] = "chave-de-teste"
import main  # noqa: E402

PASTA = Path(os.environ.get("AMOSTRAS_DIR", "/mnt/user-data/uploads"))
cliente = TestClient(main.app)
H = {"X-API-Key": "chave-de-teste"}


def test_health():
    assert cliente.get("/").json()["status"] == "online"


def test_sem_chave_e_bloqueado():
    r = cliente.post("/ler-relatorio", files={"arquivo": ("a.csv", b"x")})
    assert r.status_code == 401


@pytest.mark.skipif(not (PASTA / "Relatorio_CentroCusto_Produto.csv").exists(), reason="amostras não encontradas")
def test_ler_relatorio_detecta_tipo():
    conteudo = (PASTA / "Relatorio_CentroCusto_Produto.csv").read_bytes()
    r = cliente.post("/ler-relatorio", headers=H, files={"arquivo": ("p.csv", conteudo)})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["tipo"] == "centro_produto" and corpo["sucesso"] is True
    assert corpo["total_linhas"] == 777 and len(corpo["meses"]) == 12


def test_arquivo_estranho_devolve_422():
    r = cliente.post("/ler-relatorio", headers=H, files={"arquivo": ("x.csv", b"nada;a ver\n1;2\n")})
    assert r.status_code == 422
