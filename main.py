"""
API Consolidador SESAP - relatórios do ApuraSUS
-----------------------------------------------
Recebe os relatórios brutos (CSV) do ApuraSUS, confere, casa os nomes com os
cadastros padrão e grava no Supabase. Quem pode fazer o quê é decidido aqui
(importador/acesso.py), a partir do e-mail do usuário e da tabela usuarios_unidade.

Variáveis de ambiente (Render > Environment):
  SUPABASE_URL, SUPABASE_SERVICE_KEY, API_KEY, CORS_ORIGINS
"""
import logging
import os
import secrets
import uuid
from urllib.parse import urljoin, urlparse
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from importador import ArquivoInvalido, ler_relatorio
from importador.acesso import AcessoNegado, exigir_master, unidade_permitida, usuario_ativo
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
    """Perfil do usuário e a unidade dele, para o app montar as telas."""
    try:
        u = usuario_ativo(repo, usuario_email)
    except AcessoNegado as e:
        _negar(e)
    unidade = repo.obter_unidade(u["unidade_id"]) if u.get("unidade_id") else None
    return {"email": u["email"], "nome": u.get("nome"), "papel": u["papel"],
            "unidade": ({"id": unidade["id"], "nome": unidade["nome"]} if unidade else None)}


@app.get("/unidades", dependencies=[Depends(exigir_chave)])
def listar_unidades(usuario_email: str = Query(...), repo=Depends(get_repo)):
    """Unidades que o usuário pode escolher: todas para o master, só a própria para os demais."""
    try:
        u = usuario_ativo(repo, usuario_email)
    except AcessoNegado as e:
        _negar(e)
    if u["papel"] == "master_sesap":
        return repo.listar_unidades()
    un = repo.obter_unidade(u["unidade_id"])
    return [{"id": un["id"], "nome": un["nome"]}] if un else []


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
async def ler_relatorio_apurasus(arquivo: UploadFile = File(...), tipo: Optional[str] = Form(default=None)):
    """Lê um CSV bruto e devolve o que foi entendido, sem gravar nada."""
    conteudo = await arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="Arquivo maior que 10 MB.")
    try:
        leitura = ler_relatorio(conteudo, tipo or None)
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
        r = importar(repo, uid, conteudo, nome, tipo or None, u["email"], reprocessar)
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
