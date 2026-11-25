import os

import pytest
from fastapi.testclient import TestClient

os.environ["API_KEY"] = "chave-de-teste"
import main  # noqa: E402
from importador.repositorios import RepositorioMemoria  # noqa: E402

H = {"X-API-Key": "chave-de-teste"}
U1, U2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
CC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MES = "2026-01-01"


def _v(papel, uid=None):
    return {"papel": papel, "unidade_id": uid, "ativo": True}


@pytest.fixture
def api():
    repo = RepositorioMemoria(
        [{"id": U1, "nome": "HJPB"}, {"id": U2, "nome": "HGT"}],
        [{"id": CC, "nome": "Centro de Custos Externo", "tipo": "Externo", "item_producao_padrao": None}], [],
        usuarios=[
            {"email": "m@s.rn", "nome": "M", "ativo": True, "vinculos": [_v("master_sesap")]},
            {"email": "a@s.rn", "nome": "A", "ativo": True, "vinculos": [_v("analista_sesap")]},
            {"email": "g@h.rn", "nome": "G", "ativo": True, "vinculos": [_v("gestor_unidade", U1)]},
            {"email": "g2@h.rn", "nome": "G2", "ativo": True, "vinculos": [_v("gestor_unidade", U1)]},
            {"email": "l@h.rn", "nome": "L", "ativo": True, "vinculos": [_v("leitura_unidade", U1)]},
            {"email": "x@h.rn", "nome": "X", "ativo": True, "vinculos": [_v("gestor_unidade", U2)]},
        ])
    main.app.dependency_overrides[main.get_repo] = lambda: repo
    yield TestClient(main.app), repo
    main.app.dependency_overrides.clear()


def enviar(c, email="g@h.rn", **extra):
    dados = {"usuario_email": email, "competencia": MES, "tipo": "centro", "referencia": CC,
             "referencia_nome": "Centro de Custos Externo", "motivo": "servico_outro_hospital",
             "texto": "Lavanderia e refeições para outro hospital"}
    dados.update(extra)
    return c.post("/qualidade/justificativa", headers=H, data={k: v for k, v in dados.items() if v is not None})


def ler(c, email="g@h.rn", **params):
    return c.get("/painel/justificativas", headers=H, params={"usuario_email": email, **params})


def test_gestor_justifica_e_todos_da_unidade_leem(api):
    c, _ = api
    r = enviar(c)
    assert r.status_code == 200, r.text
    j = r.json()["justificativa"]
    assert j["motivo"] == "servico_outro_hospital" and j["criado_por"] == "g@h.rn" and j["unidade_id"] == U1
    for email in ("g@h.rn", "l@h.rn"):
        linhas = ler(c, email).json()
        assert len(linhas) == 1 and linhas[0]["referencia"] == CC
    assert len(ler(c, "a@s.rn", unidade_id=U1).json()) == 1       # analista da SESAP lê
    assert ler(c, "x@h.rn").json() == []                          # outra unidade não vê


def test_so_gestor_e_master_escrevem(api):
    c, _ = api
    assert enviar(c, "l@h.rn").status_code == 403                 # leitura
    assert enviar(c, "a@s.rn", unidade_id=U1).status_code == 403  # analista SESAP só consulta
    assert enviar(c, "x@h.rn", unidade_id=U1).status_code == 403  # gestor de outra unidade
    assert enviar(c, "m@s.rn", unidade_id=U1).status_code == 200  # master precisa informar a unidade
    assert enviar(c, "m@s.rn").status_code == 403
    assert ler(c, "g@h.rn").json()[0]["criado_por"] == "m@s.rn"


def test_gestor_nao_escolhe_outra_unidade(api):
    c, _ = api
    assert enviar(c, "g@h.rn", unidade_id=U2).status_code == 403
    assert enviar(c, "g@h.rn", unidade_id="").status_code == 200   # vazio vale a unidade do cadastro


def test_enviar_de_novo_substitui_e_mantem_quem_criou(api):
    c, _ = api
    enviar(c, "g@h.rn", motivo="contrato", texto="Contrato novo")
    enviar(c, "g2@h.rn", motivo="sazonalidade", texto=None)
    linhas = ler(c).json()
    assert len(linhas) == 1
    assert linhas[0]["motivo"] == "sazonalidade" and linhas[0]["texto"] is None
    assert linhas[0]["criado_por"] == "g@h.rn" and linhas[0]["atualizado_por"] == "g2@h.rn"


def test_item_usa_o_nome_como_referencia(api):
    c, _ = api
    r = enviar(c, tipo="item", referencia="Serviços Laboratoriais", referencia_nome=None, motivo="lancamento_fora_do_mes",
               texto=None)
    assert r.status_code == 200, r.text
    assert ler(c).json()[0]["referencia"] == "Serviços Laboratoriais"


def test_validacoes(api):
    c, _ = api
    assert enviar(c, motivo="inventado").status_code == 422
    assert enviar(c, motivo="outro", texto=None).status_code == 422
    assert enviar(c, motivo="outro", texto="   ").status_code == 422
    assert enviar(c, motivo="outro", texto="Obra pontual no setor").status_code == 200
    assert enviar(c, texto="x" * 1001).status_code == 422
    assert enviar(c, tipo="setor").status_code == 422
    assert enviar(c, referencia="nao-e-uuid").status_code == 422
    assert enviar(c, referencia="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb").status_code == 422   # centro que não existe
    assert enviar(c, referencia="").status_code == 422
    assert enviar(c, competencia="2026-01-15").status_code == 422
    assert enviar(c, motivo=None).status_code == 422


def test_remover(api):
    c, _ = api
    enviar(c)
    r = enviar(c, remover="true", motivo=None, texto=None)
    assert r.status_code == 200 and r.json()["removida"] is True
    assert ler(c).json() == []
    assert enviar(c, remover="true", motivo=None, texto=None).json()["removida"] is False   # repetir não dá erro
    enviar(c)
    assert enviar(c, "l@h.rn", remover="true").status_code == 403    # leitura não apaga
    assert len(ler(c).json()) == 1


def test_auditoria_registra_quem_justificou(api):
    c, repo = api
    enviar(c)
    enviar(c, remover="true", motivo=None, texto=None)
    acoes = [(a["acao"], a["ator_email"], a["unidade_id"]) for a in repo.auditoria]
    assert acoes == [("justificativa_salva", "g@h.rn", U1), ("justificativa_removida", "g@h.rn", U1)]


def test_lote_inclui_justificativas(api):
    c, _ = api
    enviar(c)
    r = c.get("/painel/lote:justificativas,completude", headers=H, params={"usuario_email": "g@h.rn"})
    assert r.status_code == 200 and len(r.json()["justificativas"]) == 1


def test_sem_chave_ou_sem_usuario(api):
    c, _ = api
    assert c.post("/qualidade/justificativa", data={"usuario_email": "g@h.rn"}).status_code in (401, 422)
    assert enviar(c, "desconhecido@x.rn").status_code == 403
