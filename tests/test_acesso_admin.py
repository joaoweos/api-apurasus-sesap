import os

import pytest
from fastapi.testclient import TestClient

os.environ["API_KEY"] = "chave-de-teste"
import main  # noqa: E402
from importador.repositorios import RepositorioMemoria  # noqa: E402

H = {"X-API-Key": "chave-de-teste"}
U1, U2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def _v(papel, uid=None):
    return {"papel": papel, "unidade_id": uid, "ativo": True}


@pytest.fixture
def api():
    repo = RepositorioMemoria(
        [{"id": U1, "nome": "HJPB"}, {"id": U2, "nome": "HGT"}], [], [],
        usuarios=[
            {"email": "m@s.rn", "nome": "M", "ativo": True, "vinculos": [_v("master_sesap")]},
            {"email": "a@s.rn", "nome": "A", "ativo": True, "vinculos": [_v("analista_sesap")]},
            {"email": "g@h.rn", "nome": "G", "ativo": True, "vinculos": [_v("gestor_unidade", U1)]},
            {"email": "l@h.rn", "nome": "L", "ativo": True, "vinculos": [_v("leitura_unidade", U1)]},
            {"email": "t@s.rn", "nome": "T", "ativo": True,
             "vinculos": [_v("leitura_unidade", U1), _v("gestor_unidade", U2)]},
        ])
    main.app.dependency_overrides[main.get_repo] = lambda: repo
    yield TestClient(main.app), repo
    main.app.dependency_overrides.clear()


def vinc(c, ator, email, papel, uid=None, ativo=True):
    return c.post("/admin/usuarios/vinculo", headers=H, json={"usuario_email": ator, "email": email, "papel": papel,
                                                              "unidade_id": uid, "ativo": ativo})


def test_analista_le_tudo_mas_nao_escreve(api):
    c, _ = api
    assert c.get("/painel/comparativo", headers=H, params={"usuario_email": "a@s.rn"}).status_code == 200
    assert c.get("/painel/total-mensal", headers=H, params={"usuario_email": "a@s.rn", "unidade_id": U2}).status_code == 200
    r = c.post("/painel/mes-status", headers=H, json={"usuario_email": "a@s.rn", "competencia": "2025-11-01",
                                                      "status": "parcial", "unidade_id": U1})
    assert r.status_code == 403
    assert vinc(c, "a@s.rn", "x@x.rn", "leitura_unidade", U1).status_code == 403


def test_leitura_e_gestor_nao_veem_comparativo_nem_outra_unidade(api):
    c, _ = api
    for e in ("l@h.rn", "g@h.rn"):
        assert c.get("/painel/comparativo", headers=H, params={"usuario_email": e}).status_code == 403
        assert c.get("/painel/total-mensal", headers=H, params={"usuario_email": e, "unidade_id": U2}).status_code == 403
        assert c.get("/painel/total-mensal", headers=H, params={"usuario_email": e}).status_code == 200


def test_multiunidade_exige_informar_unidade_e_respeita_papel_por_unidade(api):
    c, _ = api
    assert c.get("/painel/total-mensal", headers=H, params={"usuario_email": "t@s.rn"}).status_code == 403
    assert c.get("/painel/total-mensal", headers=H, params={"usuario_email": "t@s.rn", "unidade_id": U1}).status_code == 200
    mes = {"competencia": "2025-11-01", "status": "parcial", "usuario_email": "t@s.rn"}
    assert c.post("/painel/mes-status", headers=H, json={**mes, "unidade_id": U1}).status_code == 403  # leitura em U1
    assert c.post("/painel/mes-status", headers=H, json={**mes, "unidade_id": U2}).status_code == 200  # gestor em U2
    eu = c.get("/eu", headers=H, params={"usuario_email": "t@s.rn"}).json()
    assert len(eu["vinculos"]) == 2 and eu["unidade"] is None and eu["permissoes"]["gerir_usuarios"] is True


def test_master_cadastra_qualquer_perfil_e_audita(api):
    c, repo = api
    assert vinc(c, "m@s.rn", "novo@s.rn", "analista_sesap").status_code == 200
    assert vinc(c, "m@s.rn", "novo@h.rn", "gestor_unidade", U2).status_code == 200
    assert c.get("/eu", headers=H, params={"usuario_email": "Novo@S.rn "}).json()["papel"] == "analista_sesap"
    assert [a["acao"] for a in repo.listar_auditoria()][:2] == ["vinculo_salvo", "vinculo_salvo"]


def test_gestor_so_cadastra_leitura_da_propria_unidade(api):
    c, _ = api
    assert vinc(c, "g@h.rn", "n@h.rn", "leitura_unidade", U1).status_code == 200
    assert vinc(c, "g@h.rn", "n@h.rn", "gestor_unidade", U1).status_code == 403
    assert vinc(c, "g@h.rn", "n@h.rn", "master_sesap").status_code == 403
    assert vinc(c, "g@h.rn", "n@h.rn", "leitura_unidade", U2).status_code == 403
    assert vinc(c, "l@h.rn", "n2@h.rn", "leitura_unidade", U1).status_code == 403
    # não mexe em quem tem outro papel na unidade
    assert vinc(c, "g@h.rn", "g@h.rn", "leitura_unidade", U1).status_code == 403
    # desativa leitura
    assert vinc(c, "g@h.rn", "l@h.rn", "leitura_unidade", U1, ativo=False).status_code == 200
    assert c.get("/eu", headers=H, params={"usuario_email": "l@h.rn"}).status_code == 403


def test_listagem_do_gestor_so_mostra_a_unidade_dele(api):
    c, _ = api
    r = c.get("/admin/usuarios", headers=H, params={"usuario_email": "g@h.rn"}).json()
    assert {x["email"] for x in r} == {"g@h.rn", "l@h.rn", "t@s.rn"}
    t = next(x for x in r if x["email"] == "t@s.rn")
    assert [v["unidade_id"] for v in t["vinculos"]] == [U1]   # não revela o vínculo dele com a outra unidade
    assert c.get("/admin/usuarios", headers=H, params={"usuario_email": "l@h.rn"}).status_code == 403
    assert len(c.get("/admin/usuarios", headers=H, params={"usuario_email": "m@s.rn"}).json()) == 5


def test_travas_do_master_e_bloqueio(api):
    c, _ = api
    assert vinc(c, "m@s.rn", "m@s.rn", "master_sesap", ativo=False).status_code == 403
    assert c.post("/admin/usuarios/ativo", headers=H, json={"usuario_email": "m@s.rn", "email": "m@s.rn", "ativo": False}).status_code == 403
    assert c.post("/admin/usuarios/ativo", headers=H, json={"usuario_email": "m@s.rn", "email": "l@h.rn", "ativo": False}).status_code == 200
    assert c.get("/eu", headers=H, params={"usuario_email": "l@h.rn"}).status_code == 403
    assert c.post("/admin/usuarios/ativo", headers=H, json={"usuario_email": "g@h.rn", "email": "l@h.rn", "ativo": True}).status_code == 403


def test_unidades_so_master(api):
    c, _ = api
    corpo = {"usuario_email": "m@s.rn", "nome": "Hospital X", "sigla": "HX"}
    assert c.post("/admin/unidades", headers=H, json=corpo).status_code == 200
    assert len(c.get("/admin/unidades", headers=H, params={"usuario_email": "m@s.rn"}).json()) == 3
    assert c.post("/admin/unidades", headers=H, json={**corpo, "usuario_email": "g@h.rn"}).status_code == 403
    assert c.get("/admin/auditoria", headers=H, params={"usuario_email": "a@s.rn"}).status_code == 403


def test_email_invalido_e_papel_invalido(api):
    c, _ = api
    assert vinc(c, "m@s.rn", "sem-arroba", "analista_sesap").status_code == 422
    assert vinc(c, "m@s.rn", "x@x.rn", "deus").status_code == 403
    assert vinc(c, "m@s.rn", "x@x.rn", "gestor_unidade").status_code == 403   # papel de unidade sem unidade


def test_centro_produto_dividido_por_tipo_nao_apaga_os_outros_arquivos():
    """Três arquivos (administrativo, intermediário, final) dos mesmos meses devem somar, não se substituir."""
    from importador.gravar import importar
    U = "33333333-3333-3333-3333-333333333333"
    centros = [{"id": "c1", "nome": "Almoxarifado", "tipo": "administrativo", "item_producao_padrao": "Item Atendido"},
               {"id": "c1b", "nome": "Compras", "tipo": "administrativo", "item_producao_padrao": "Processo de Compras"},
               {"id": "c2", "nome": "Radiologia", "tipo": "intermediario", "item_producao_padrao": "Exame"},
               {"id": "c2b", "nome": "Hemodiálise", "tipo": "intermediario", "item_producao_padrao": "Sessão"},
               {"id": "c3", "nome": "UTI Adulto", "tipo": "final", "item_producao_padrao": "Paciente/Dia"},
               {"id": "c3b", "nome": "Centro Cirúrgico", "tipo": "final", "item_producao_padrao": "Cirurgia"}]
    repo = RepositorioMemoria([{"id": U, "nome": "HJPB", "nome_no_apurasus": "HOSPITAL TESTE"}], centros, [])

    def arq(tipo, linhas):   # sem linhas em branco: o leitor detecta o separador pela proporção de ';'
        txt = (f"Instituição: HOSPITAL TESTE\nTipo Centro Custo: {tipo}\nPeriodo de : 01/2026 a 01/2026\n01/2026\n"
               f"Centro Custo;Item Produção;Quantidade;Valor\n{tipo}\n" + "\n".join(linhas) + "\n")
        return txt.encode("latin-1")

    envios = (("Administrativo", ["Almoxarifado;Item Atendido;10;4,38", "Compras;Processo de Compras;8;1244,44"]),
              ("Intermediário", ["Radiologia;Exame;475;409,02", "Hemodiálise;Sessão;222;1121,90"]),
              ("Final", ["UTI Adulto;Paciente/Dia;8,26;107257,49", "Centro Cirúrgico;Cirurgia;321;4749,66"]))
    for tipo, linhas in envios:
        r = importar(repo, U, arq(tipo, linhas), f"{tipo}.csv", None, "x@x.rn", False, None)
        assert r.status == "concluido", r
    assert len(repo.linhas("fato_apurasus_custo_unitario")) == 6
    # reprocessar só o final substitui só os centros do final
    r = importar(repo, U, arq("Final", ["UTI Adulto;Paciente/Dia;9;100000,00", "Centro Cirúrgico;Cirurgia;300;5000,00"]),
                 "Final2.csv", None, "x@x.rn", True, None)
    assert r.status == "concluido", r
    linhas = repo.linhas("fato_apurasus_custo_unitario")
    assert len(linhas) == 6
    assert sorted(float(l["quantidade"]) for l in linhas) == [8.0, 9.0, 10.0, 222.0, 300.0, 475.0]


def test_painel_em_lote_e_eu_com_unidades(api):
    c, repo = api
    repo.views = {"v_total_mensal": [{"unidade_id": U1, "competencia": "2025-01-01", "total": 1}],
                  "v_grupo_mensal": [{"unidade_id": U1, "competencia": "2025-01-01", "grupo": "x"}],
                  "v_completude_mes": []}
    lote = "lote:total-mensal,grupos,completude"
    r = c.get("/painel/" + lote, headers=H, params={"usuario_email": "l@h.rn"})
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"total-mensal", "grupos", "completude"} and len(j["total-mensal"]) == 1 and j["completude"] == []
    # visão simples continua devolvendo a lista direto
    r = c.get("/painel/total-mensal", headers=H, params={"usuario_email": "l@h.rn"})
    assert isinstance(r.json(), list)
    # direitos valem para cada visão do lote: leitura da unidade não pede comparativo nem outra unidade
    r = c.get("/painel/lote:total-mensal,comparativo", headers=H, params={"usuario_email": "l@h.rn"})
    assert r.status_code == 403
    r = c.get("/painel/lote:total-mensal", headers=H, params={"usuario_email": "l@h.rn", "unidade_id": U2})
    assert r.status_code == 403
    # lote inválido
    assert c.get("/painel/lote:", headers=H, params={"usuario_email": "l@h.rn"}).status_code == 422
    assert c.get("/painel/lote:grupos,grupos", headers=H, params={"usuario_email": "l@h.rn"}).status_code == 422
    assert c.get("/painel/lote:grupos,nada", headers=H, params={"usuario_email": "l@h.rn"}).status_code == 404
    # /eu: SESAP recebe a lista de unidades; quem é de unidade, não
    assert {u["id"] for u in c.get("/eu", headers=H, params={"usuario_email": "m@s.rn"}).json()["unidades"]} == {U1, U2}
    assert c.get("/eu", headers=H, params={"usuario_email": "l@h.rn"}).json()["unidades"] is None
