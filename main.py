"""
API Consolidador SESAP - relatórios do ApuraSUS
-----------------------------------------------
Recebe os relatórios brutos (CSV) do ApuraSUS, confere, casa os nomes com os
cadastros padrão e grava no Supabase. Quem pode fazer o quê é decidido aqui
(importador/acesso.py), a partir do e-mail do usuário e das tabelas usuarios / usuario_vinculos.

Variáveis de ambiente (Render > Environment):
  SUPABASE_URL, SUPABASE_SERVICE_KEY, API_KEY, CORS_ORIGINS
"""
import logging
import os
import secrets
import re
import uuid
from urllib.parse import urljoin, urlparse
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from importador import ArquivoInvalido, ler_relatorio
from importador.acesso import (AcessoNegado, PAPEIS, PAPEIS_SESAP, ROTULOS, eh_sesap, exigir_gestao_de_vinculo,
                               exigir_master, exigir_sesap, normalizar_email, papel_na_unidade, unidade_permitida,
                               unidades_do_usuario, usuario_ativo)
from importador.gravar import importar
from importador.repositorios import RepositorioSupabase

app = FastAPI(title="API Consolidador SESAP - ApuraSUS")

_origens = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origens or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB
# Só baixamos arquivos de endereços https destes hosts (evita que o servidor seja usado para acessar outros sites).
HOSTS_PERMITIDOS = {h.strip().lower() for h in os.environ.get("ARQUIVO_HOSTS_PERMITIDOS", "media.base44.com").split(",") if h.strip()}
_supabase = None


def get_supabase():
    global _supabase
    if _supabase is None:
        from supabase import create_client

        _supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _supabase


def get_repo():
    """Repositório de dados (trocado por um banco em memória nos testes)."""
    return RepositorioSupabase(get_supabase())


def exigir_chave(x_api_key: Optional[str] = Header(default=None)):
    """Só quem tem a chave do serviço (o Make) pode chamar os endpoints."""
    esperada = os.environ.get("API_KEY", "")
    if not esperada:
        raise HTTPException(status_code=500, detail="API_KEY não configurada no servidor.")
    if not x_api_key or not secrets.compare_digest(x_api_key, esperada):
        raise HTTPException(status_code=401, detail="Chave de acesso inválida.")


@app.exception_handler(Exception)
async def erro_inesperado(request, exc):
    """Qualquer falha não prevista vira JSON com o motivo (em vez de 'Internal Server Error' sem explicação)."""
    logging.exception("Erro inesperado em %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Erro interno: {type(exc).__name__}: {exc}"})


def baixar_arquivo(url: str) -> bytes:
    """Baixa o arquivo enviado pelo app, byte a byte (sem decodificar), só de hosts permitidos."""
    import httpx

    atual = url
    for _ in range(4):
        p = urlparse(atual)
        host = (p.hostname or "").lower()
        if p.scheme != "https" or host not in HOSTS_PERMITIDOS:
            raise HTTPException(status_code=422, detail=f"Endereço do arquivo não permitido (host: {host or 'inválido'}).")
        with httpx.stream("GET", atual, timeout=30.0, follow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308):
                atual = urljoin(atual, r.headers.get("location", ""))
                continue
            if r.status_code != 200:
                raise HTTPException(status_code=422, detail=f"Não foi possível baixar o arquivo (status {r.status_code}).")
            dados = bytearray()
            for pedaco in r.iter_bytes():
                dados += pedaco
                if len(dados) > TAMANHO_MAXIMO:
                    raise HTTPException(status_code=413, detail="Arquivo maior que 10 MB.")
            return bytes(dados)
    raise HTTPException(status_code=422, detail="Redirecionamentos demais ao baixar o arquivo.")


def _exigir_uuid(valor: str, campo: str):
    try:
        uuid.UUID(str(valor))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{campo} inválido: não é um identificador (uuid).")


def _negar(e: AcessoNegado):
    raise HTTPException(status_code=403, detail=str(e))


@app.get("/")
def home():
    return {"status": "online", "projeto": "SESAP ApuraSUS Engine"}


# ---------------------------------------------------------------------------
# Quem sou eu / o que posso ver
# ---------------------------------------------------------------------------
@app.get("/eu", dependencies=[Depends(exigir_chave)])
def quem_sou_eu(usuario_email: str = Query(...), repo=Depends(get_repo)):
    """Perfil, vínculos e permissões do usuário, para o app montar o menu e as telas."""
    try:
        u = usuario_ativo(repo, usuario_email)
    except AcessoNegado as e:
        _negar(e)
    repo.tocar_acesso(u["email"])
    todas = repo.listar_unidades()
    nomes = {x["id"]: x["nome"] for x in todas}
    vinculos = [{"papel": v["papel"], "papel_rotulo": ROTULOS[v["papel"]], "unidade_id": v.get("unidade_id"),
                 "unidade_nome": nomes.get(v.get("unidade_id"))} for v in u["vinculos"]]
    gestor = any(v["papel"] == "gestor_unidade" for v in u["vinculos"])
    unidade = repo.obter_unidade(u["unidade_id"]) if u.get("unidade_id") else None
    return {"email": u["email"], "nome": u.get("nome"), "papel": u["papel"], "papel_rotulo": ROTULOS[u["papel"]],
            "unidade": ({"id": unidade["id"], "nome": unidade["nome"]} if unidade else None),  # só quando tem uma unidade
            "vinculos": vinculos,
            # SESAP escolhe entre todas as unidades: já vai aqui para o app não precisar de outra chamada
            "unidades": ([{"id": x["id"], "nome": x["nome"]} for x in todas] if eh_sesap(u) else None),
            "permissoes": {
                "ver_todas_unidades": eh_sesap(u),
                "importar": u["papel"] == "master_sesap" or gestor,
                "comparativo": eh_sesap(u),
                "gerir_usuarios": u["papel"] == "master_sesap" or gestor,
                "gerir_unidades": u["papel"] == "master_sesap",
            }}


@app.get("/unidades", dependencies=[Depends(exigir_chave)])
def listar_unidades(usuario_email: str = Query(...), repo=Depends(get_repo)):
    """Unidades que o usuário pode escolher: todas para a SESAP, só as dos vínculos para os demais."""
    try:
        u = usuario_ativo(repo, usuario_email)
    except AcessoNegado as e:
        _negar(e)
    todas = repo.listar_unidades()
    ids = unidades_do_usuario(u)
    return todas if ids is None else [x for x in todas if str(x["id"]) in ids]


@app.get("/dimensoes", dependencies=[Depends(exigir_chave)])
def listar_dimensoes(usuario_email: str = Query(...), repo=Depends(get_repo)):
    """Centros e itens padrão, para as listas de escolha das pendências."""
    try:
        usuario_ativo(repo, usuario_email)
    except AcessoNegado as e:
        _negar(e)
    return repo.listar_dimensoes()


@app.get("/importacoes", dependencies=[Depends(exigir_chave)])
def listar_importacoes(usuario_email: str = Query(...), unidade_id: Optional[str] = Query(default=None),
                       repo=Depends(get_repo)):
    """Histórico das últimas importações da unidade."""
    try:
        u = usuario_ativo(repo, usuario_email)
        uid = unidade_permitida(u, unidade_id, escrita=False)
    except AcessoNegado as e:
        _negar(e)
    return jsonable_encoder(repo.listar_importacoes(uid))


# ---------------------------------------------------------------------------
# Leitura (prévia, sem gravar) e importação
# ---------------------------------------------------------------------------
@app.post("/ler-relatorio", dependencies=[Depends(exigir_chave)])
async def ler_relatorio_apurasus(arquivo: UploadFile = File(...), tipo: Optional[str] = Form(default=None),
                                 competencia: Optional[str] = Form(default=None)):
    """Lê um CSV bruto e devolve o que foi entendido, sem gravar nada."""
    conteudo = await arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="Arquivo maior que 10 MB.")
    try:
        leitura = ler_relatorio(conteudo, tipo or None, competencia or None)
    except ArquivoInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    return jsonable_encoder({
        "sucesso": leitura.ok, "tipo": leitura.tipo, "instituicao": leitura.instituicao,
        "periodo": {"inicio": leitura.inicio, "fim": leitura.fim},
        "meses": [d.strftime("%Y-%m") for d in leitura.competencias],
        "total_linhas": len(leitura.linhas), "erros": leitura.erros, "avisos": leitura.avisos,
        "amostra": leitura.linhas[:5],
    })


@app.post("/importar", dependencies=[Depends(exigir_chave)])
async def importar_relatorio(
    usuario_email: str = Form(...),
    arquivo: Optional[UploadFile] = File(default=None),        # envio direto (testes no /docs)
    arquivo_url: Optional[str] = Form(default=None),           # envio pelo app: o Render baixa o arquivo
    arquivo_nome: Optional[str] = Form(default=None),
    unidade_id: Optional[str] = Form(default=None),   # só vale para o master; o gestor usa a unidade do cadastro
    tipo: Optional[str] = Form(default=None),
    reprocessar: bool = Form(default=False),
    competencia: Optional[str] = Form(default=None),   # só para a Matriz Custos Totais (o arquivo não traz o mês): AAAA-MM
    repo=Depends(get_repo),
):
    """Lê, confere e grava um relatório na unidade do usuário.

    Com nomes sem correspondência nada é gravado: a resposta traz `pendencias`
    (com sugestões). Depois de resolvê-las, reenvie o mesmo arquivo.
    """
    try:
        u = usuario_ativo(repo, usuario_email)
        uid = unidade_permitida(u, unidade_id, escrita=True)
    except AcessoNegado as e:
        _negar(e)
    if bool(arquivo) == bool(arquivo_url):
        raise HTTPException(status_code=422, detail="Envie o arquivo OU o arquivo_url (um dos dois).")
    if arquivo_url:
        conteudo, nome = baixar_arquivo(arquivo_url), (arquivo_nome or "relatorio.csv")
    else:
        conteudo, nome = await arquivo.read(), (arquivo_nome or arquivo.filename or "relatorio.csv")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="Arquivo maior que 10 MB.")
    try:
        r = importar(repo, uid, conteudo, nome, tipo or None, u["email"], reprocessar, competencia or None)
    except Exception as e:  # falha inesperada (ex.: o banco recusou algum dado)
        logging.exception("Falha em /importar")
        raise HTTPException(status_code=500, detail=f"Erro ao importar: {type(e).__name__}: {e}")
    return jsonable_encoder(r)


# ---------------------------------------------------------------------------
# Resolução de pendências
# ---------------------------------------------------------------------------
class DeParaCentro(BaseModel):
    usuario_email: str
    nome_no_apurasus: str
    cc_pngc_id: str
    unidade_id: Optional[str] = None   # só vale para o master
    tipo_secao: Optional[str] = None   # administrativo | intermediario | final | externo ('' vale como vazio)
    ocorrencia: Optional[int] = 1


@app.post("/de-para/centro", dependencies=[Depends(exigir_chave)])
def salvar_de_para_centro(corpo: DeParaCentro, repo=Depends(get_repo)):
    try:
        u = usuario_ativo(repo, corpo.usuario_email)
        uid = unidade_permitida(u, corpo.unidade_id, escrita=True)
    except AcessoNegado as e:
        _negar(e)
    _exigir_uuid(corpo.cc_pngc_id, "cc_pngc_id")
    if not repo.centro_existe(corpo.cc_pngc_id):
        raise HTTPException(status_code=422, detail="Centro padrão não encontrado: confira se o id é de um centro (e não de um item).")
    repo.salvar_depara_centro({"unidade_id": uid, "nome_no_apurasus": corpo.nome_no_apurasus,
                               "cc_pngc_id": corpo.cc_pngc_id, "tipo_secao": (corpo.tipo_secao or None),
                               "ocorrencia": (corpo.ocorrencia or 1)})
    return {"sucesso": True}


class AliasItem(BaseModel):
    usuario_email: str
    nome: str
    ic_id: str


@app.post("/alias/item", dependencies=[Depends(exigir_chave)])
def salvar_alias_item(corpo: AliasItem, repo=Depends(get_repo)):
    """Apelido de item vale para TODAS as unidades; por isso só o master cria."""
    try:
        u = usuario_ativo(repo, corpo.usuario_email)
        exigir_master(u)
    except AcessoNegado as e:
        _negar(e)
    _exigir_uuid(corpo.ic_id, "ic_id")
    if not repo.item_existe(corpo.ic_id):
        raise HTTPException(status_code=422, detail="Item padrão não encontrado: confira se o id é de um item (e não de um centro).")
    repo.salvar_alias_item(corpo.nome, corpo.ic_id, u["email"])
    return {"sucesso": True}


# ---------------------------------------------------------------------------
# Painel: leitura das views do Supabase (os cálculos já vêm prontos de lá)
# ---------------------------------------------------------------------------
# nome na URL -> (view, colunas ordenadas por)
VISOES = {
    "total-mensal":   ("v_total_mensal", ["competencia"]),
    "grupos":         ("v_grupo_mensal", ["competencia", "grupo"]),
    "itens":          ("v_item_estatistica", ["competencia", "grupo", "item"]),
    "centros":        ("v_centro_estatistica", ["competencia", "centro"]),
    "unitario":       ("v_unitario_estatistica", ["competencia", "centro"]),
    "completude":     ("v_completude_mes", ["competencia"]),
    "sem-lancamento": ("v_item_sem_lancamento", ["competencia", "item"]),
    "conciliacao":    ("v_conciliacao_mes", ["competencia"]),
    "qualidade":      ("v_qualidade_mes", ["competencia"]),
    "centros-mensal": ("v_centro_mensal", ["competencia", "tipo", "centro"]),
    "tipos-centro":   ("v_tipo_centro_mensal", ["competencia", "tipo"]),
}
VISOES_MASTER = {"comparativo": ("v_comparativo_unidades", ["competencia", "centro"])}
_DATA = re.compile(r"^\d{4}-\d{2}-01$")


def _exigir_data(valor: Optional[str], campo: str):
    if valor and not _DATA.match(valor):
        raise HTTPException(status_code=422, detail=f"{campo} deve ser o primeiro dia do mês, no formato AAAA-MM-01.")


def _resolver_visao(u, visao, unidade_id):
    """Devolve (view, ordem, unidade) da visão pedida, já conferindo o direito do usuário."""
    if visao in VISOES_MASTER:
        exigir_sesap(u)   # comparativo: master e analista da SESAP
        view, ordem = VISOES_MASTER[visao]
        uid = str(unidade_id) if unidade_id else None   # opcional: filtra uma unidade
    elif visao in VISOES:
        view, ordem = VISOES[visao]
        uid = unidade_permitida(u, unidade_id, escrita=False)
    else:
        raise HTTPException(status_code=404, detail=f"Visão desconhecida: {visao}.")
    return view, ordem, uid


MAX_LOTE = 6


@app.get("/painel/{visao}", dependencies=[Depends(exigir_chave)])
def painel(visao: str, usuario_email: str = Query(...), unidade_id: Optional[str] = Query(default=None),
           competencia: Optional[str] = Query(default=None), de: Optional[str] = Query(default=None),
           ate: Optional[str] = Query(default=None), repo=Depends(get_repo)):
    """Dados prontos para as telas. Qualquer perfil lê, mas só a unidade a que tem direito.
    `comparativo` (todas as unidades) é exclusivo da equipe da SESAP (master e analista).

    Lote (economiza chamadas): `lote:total-mensal,grupos,completude` devolve um objeto
    {visão: linhas} com todas as visões pedidas, em uma única requisição."""
    for v, c in ((competencia, "competencia"), (de, "de"), (ate, "ate")):
        _exigir_data(v, c)
    nomes = None
    if visao.startswith("lote:"):
        nomes = [n for n in visao[5:].split(",") if n]
        if not nomes or len(nomes) > MAX_LOTE or len(set(nomes)) != len(nomes):
            raise HTTPException(status_code=422, detail=f"Informe de 1 a {MAX_LOTE} visões diferentes no lote.")
    try:
        u = usuario_ativo(repo, usuario_email)
        planos = {n: _resolver_visao(u, n, unidade_id) for n in (nomes or [visao])}
    except AcessoNegado as e:
        _negar(e)
    for _, _, uid in planos.values():
        if uid:
            _exigir_uuid(uid, "unidade_id")
    consulta = {n: repo.consultar_view(view, uid, "*", competencia, de, ate, ordem)
                for n, (view, ordem, uid) in planos.items()}
    return jsonable_encoder(consulta if nomes else consulta[visao])


class MesStatus(BaseModel):
    usuario_email: str
    competencia: str            # AAAA-MM-01
    status: str                 # fechado | parcial
    unidade_id: Optional[str] = None
    observacao: Optional[str] = None
    forcar: bool = False        # só o master pode fechar um mês com relatórios obrigatórios faltando


@app.post("/painel/mes-status", dependencies=[Depends(exigir_chave)])
def definir_mes_status(corpo: MesStatus, repo=Depends(get_repo)):
    """Fecha ou reabre um mês. Só 'fechado' entra no cálculo do custo padrão."""
    _exigir_data(corpo.competencia, "competencia")
    if corpo.status not in ("fechado", "parcial"):
        raise HTTPException(status_code=422, detail="status deve ser 'fechado' ou 'parcial'.")
    try:
        u = usuario_ativo(repo, corpo.usuario_email)
        uid = unidade_permitida(u, corpo.unidade_id, escrita=True)
    except AcessoNegado as e:
        _negar(e)
    if corpo.status == "fechado":
        linhas = repo.consultar_view("v_qualidade_mes", uid, "*", corpo.competencia, None, None, None)
        q = linhas[0] if linhas else None
        if q is None or not q.get("pode_fechar"):
            if corpo.forcar and u["papel"] == "master_sesap":
                pass
            else:
                falta = (q or {}).get("faltando_obrigatorios") or []
                motivo = ("faltam os relatórios: " + ", ".join(falta)) if falta else (
                    "a conciliação não está OK" if q else "não há dados nesse mês")
                raise HTTPException(status_code=409, detail=(
                    f"Este mês ainda não pode ser fechado: {motivo}. "
                    "Só o master da SESAP pode fechar mesmo assim."))
    repo.definir_mes_status(uid, corpo.competencia, corpo.status, corpo.observacao, u["email"])
    return {"sucesso": True}


# ---------------------------------------------------------------------------
# Administração: unidades, usuários e vínculos
# ---------------------------------------------------------------------------
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email_valido(email: str) -> str:
    e = normalizar_email(email)
    if not _EMAIL.match(e):
        raise HTTPException(status_code=422, detail="E-mail inválido.")
    return e


@app.get("/admin/usuarios", dependencies=[Depends(exigir_chave)])
def admin_listar_usuarios(usuario_email: str = Query(...), repo=Depends(get_repo)):
    """Master vê todos; gestor vê só os usuários que têm vínculo nas unidades dele."""
    try:
        u = usuario_ativo(repo, usuario_email)
        if u["papel"] == "master_sesap":
            unidades = None
        else:
            unidades = sorted({str(v["unidade_id"]) for v in u["vinculos"] if v["papel"] == "gestor_unidade"})
            if not unidades:
                raise AcessoNegado("Seu perfil não permite gerir usuários.")
    except AcessoNegado as e:
        _negar(e)
    return jsonable_encoder(repo.listar_usuarios(unidades))


class VinculoIn(BaseModel):
    usuario_email: str          # quem está fazendo a alteração
    email: str                  # usuário que recebe o vínculo
    papel: str                  # master_sesap | analista_sesap | gestor_unidade | leitura_unidade
    unidade_id: Optional[str] = None   # obrigatório nos papéis de unidade; vazio nos da SESAP
    nome: Optional[str] = None
    ativo: bool = True          # false = retira o acesso àquela unidade/papel (mantém o histórico)


@app.post("/admin/usuarios/vinculo", dependencies=[Depends(exigir_chave)])
def admin_salvar_vinculo(corpo: VinculoIn, repo=Depends(get_repo)):
    """Cria, altera ou desativa o vínculo (e-mail + papel + unidade). Cria o usuário se ainda não existe."""
    alvo = _email_valido(corpo.email)
    uid = corpo.unidade_id or None
    if uid:
        _exigir_uuid(uid, "unidade_id")
    try:
        u = usuario_ativo(repo, corpo.usuario_email)
        exigir_gestao_de_vinculo(u, corpo.papel, uid)
        if uid and not repo.obter_unidade(uid):
            raise HTTPException(status_code=422, detail="Unidade não encontrada.")
        # gestor só mexe em vínculo que já seja de leitura (não rebaixa/derruba outros papéis)
        if u["papel"] != "master_sesap":
            atual = repo.obter_usuario(alvo)
            existente = next((v for v in (atual or {}).get("vinculos", []) if str(v.get("unidade_id")) == uid), None)
            if existente and existente["papel"] != "leitura_unidade":
                raise AcessoNegado("Este usuário tem outro perfil nessa unidade; só o master da SESAP pode alterá-lo.")
        # trava de segurança: o master não pode tirar o próprio acesso de master
        if alvo == u["email"] and corpo.papel == "master_sesap" and not corpo.ativo:
            raise AcessoNegado("Você não pode desativar o seu próprio acesso de master.")
    except AcessoNegado as e:
        _negar(e)
    repo.salvar_usuario(alvo, (corpo.nome or "").strip() or None, u["email"])
    repo.salvar_vinculo(alvo, uid, corpo.papel, corpo.ativo, u["email"])
    repo.registrar_auditoria(u["email"], "vinculo_salvo" if corpo.ativo else "vinculo_desativado",
                             alvo, uid, corpo.papel)
    return {"sucesso": True}


class UsuarioAtivo(BaseModel):
    usuario_email: str
    email: str
    ativo: bool


@app.post("/admin/usuarios/ativo", dependencies=[Depends(exigir_chave)])
def admin_usuario_ativo(corpo: UsuarioAtivo, repo=Depends(get_repo)):
    """Bloqueia ou libera o usuário em todo o sistema (só o master)."""
    alvo = _email_valido(corpo.email)
    try:
        u = usuario_ativo(repo, corpo.usuario_email)
        exigir_master(u)
        if alvo == u["email"] and not corpo.ativo:
            raise AcessoNegado("Você não pode bloquear o seu próprio usuário.")
    except AcessoNegado as e:
        _negar(e)
    if not repo.obter_usuario(alvo):
        raise HTTPException(status_code=404, detail="Usuário não encontrado.")
    repo.definir_usuario_ativo(alvo, corpo.ativo)
    repo.registrar_auditoria(u["email"], "usuario_liberado" if corpo.ativo else "usuario_bloqueado", alvo)
    return {"sucesso": True}


@app.get("/admin/unidades", dependencies=[Depends(exigir_chave)])
def admin_listar_unidades(usuario_email: str = Query(...), repo=Depends(get_repo)):
    """Cadastro completo das unidades, inclusive as inativas (só o master)."""
    try:
        exigir_master(usuario_ativo(repo, usuario_email))
    except AcessoNegado as e:
        _negar(e)
    return jsonable_encoder(repo.listar_unidades_todas())


class UnidadeIn(BaseModel):
    usuario_email: str
    id: Optional[str] = None            # vazio = nova unidade
    nome: str
    nome_no_apurasus: Optional[str] = None   # como o hospital aparece no cabeçalho do ApuraSUS (confere o arquivo enviado)
    sigla: Optional[str] = None
    cnes: Optional[str] = None
    ativa: bool = True


@app.post("/admin/unidades", dependencies=[Depends(exigir_chave)])
def admin_salvar_unidade(corpo: UnidadeIn, repo=Depends(get_repo)):
    try:
        u = usuario_ativo(repo, corpo.usuario_email)
        exigir_master(u)
    except AcessoNegado as e:
        _negar(e)
    if corpo.id:
        _exigir_uuid(corpo.id, "id")
    if not corpo.nome.strip():
        raise HTTPException(status_code=422, detail="Informe o nome da unidade.")
    un = repo.salvar_unidade({"id": corpo.id or None, "nome": corpo.nome.strip(),
                              "nome_no_apurasus": (corpo.nome_no_apurasus or "").strip() or None,
                              "sigla": (corpo.sigla or "").strip() or None, "cnes": (corpo.cnes or "").strip() or None,
                              "ativa": corpo.ativa})
    repo.registrar_auditoria(u["email"], "unidade_salva", None, str(un["id"]), None, {"nome": un.get("nome")})
    return jsonable_encoder({"sucesso": True, "unidade": un})


@app.get("/admin/auditoria", dependencies=[Depends(exigir_chave)])
def admin_auditoria(usuario_email: str = Query(...), limite: int = Query(default=100, le=500), repo=Depends(get_repo)):
    """Quem mudou o quê nos acessos (só o master)."""
    try:
        exigir_master(usuario_ativo(repo, usuario_email))
    except AcessoNegado as e:
        _negar(e)
    return jsonable_encoder(repo.listar_auditoria(limite))
