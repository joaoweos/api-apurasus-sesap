"""
API Consolidador SESAP - relatórios do ApuraSUS
-----------------------------------------------
Lê os relatórios brutos (CSV) exportados do ApuraSUS e, nas próximas etapas,
grava os dados tratados no Supabase.

Configuração por variáveis de ambiente (Render > Environment):
  SUPABASE_URL, SUPABASE_SERVICE_KEY, API_KEY, CORS_ORIGINS
"""
import os
import secrets
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel

from importador import ArquivoInvalido, ler_relatorio
from importador.gravar import importar
from importador.repositorios import RepositorioSupabase

app = FastAPI(title="API Consolidador SESAP - ApuraSUS")

# CORS restrito às origens do app (CORS_ORIGINS, separadas por vírgula).
_origens = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origens or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB

_supabase = None


def get_supabase():
    """Cria o cliente do Supabase na primeira necessidade (usado na etapa de gravação)."""
    global _supabase
    if _supabase is None:
        from supabase import create_client

        _supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _supabase


def get_repo():
    """Repositório de dados (trocado por um banco em memória nos testes)."""
    return RepositorioSupabase(get_supabase())


def exigir_chave(x_api_key: Optional[str] = Header(default=None)):
    """Só quem tem a chave do serviço (o app) pode chamar os endpoints."""
    esperada = os.environ.get("API_KEY", "")
    if not esperada:
        raise HTTPException(status_code=500, detail="API_KEY não configurada no servidor.")
    if not x_api_key or not secrets.compare_digest(x_api_key, esperada):
        raise HTTPException(status_code=401, detail="Chave de acesso inválida.")


@app.get("/")
def home():
    return {"status": "online", "projeto": "SESAP ApuraSUS Engine"}


@app.post("/ler-relatorio", dependencies=[Depends(exigir_chave)])
async def ler_relatorio_apurasus(
    arquivo: UploadFile = File(...),
    tipo: Optional[str] = Form(default=None),
):
    """Lê um CSV bruto do ApuraSUS e devolve o que foi entendido, sem gravar nada.

    `tipo` é opcional (custo_total_unidade, matriz_item_centro, centro_produto);
    sem ele o tipo é detectado pelo cabeçalho.
    """
    conteudo = await arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="Arquivo maior que 10 MB.")
    try:
        leitura = ler_relatorio(conteudo, tipo)
    except ArquivoInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))

    return jsonable_encoder(
        {
            "sucesso": leitura.ok,
            "tipo": leitura.tipo,
            "instituicao": leitura.instituicao,
            "periodo": {"inicio": leitura.inicio, "fim": leitura.fim},
            "meses": [d.strftime("%Y-%m") for d in leitura.competencias],
            "total_linhas": len(leitura.linhas),
            "erros": leitura.erros,
            "avisos": leitura.avisos,
            "amostra": leitura.linhas[:5],
        }
    )


# ---------------------------------------------------------------------------
# Importação (grava no Supabase) e apoio às pendências
# ---------------------------------------------------------------------------
@app.post("/importar", dependencies=[Depends(exigir_chave)])
async def importar_relatorio(
    arquivo: UploadFile = File(...),
    unidade_id: str = Form(...),
    tipo: Optional[str] = Form(default=None),
    usuario: Optional[str] = Form(default=None),
    reprocessar: bool = Form(default=False),
    repo=Depends(get_repo),
):
    """Lê, confere e grava um relatório do ApuraSUS na unidade informada.

    Se houver nomes sem correspondência, nada é gravado: o retorno traz `pendencias`
    (com sugestões). Depois de resolvê-las, reenvie o mesmo arquivo.
    """
    conteudo = await arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(status_code=413, detail="Arquivo maior que 10 MB.")
    r = importar(repo, unidade_id, conteudo, arquivo.filename or "relatorio.csv", tipo or None, usuario, reprocessar)
    return jsonable_encoder(r)


@app.get("/dimensoes", dependencies=[Depends(exigir_chave)])
def listar_dimensoes(repo=Depends(get_repo)):
    """Centros e itens padrão, para o app montar as listas de escolha das pendências."""
    return repo.listar_dimensoes()


class DeParaCentro(BaseModel):
    unidade_id: str
    nome_no_apurasus: str
    cc_pngc_id: str
    tipo_secao: Optional[str] = None   # administrativo | intermediario | final | externo
    ocorrencia: int = 1


@app.post("/de-para/centro", dependencies=[Depends(exigir_chave)])
def salvar_de_para_centro(corpo: DeParaCentro, repo=Depends(get_repo)):
    repo.salvar_depara_centro(corpo.model_dump())
    return {"sucesso": True}


class AliasItem(BaseModel):
    nome: str
    ic_id: str
    usuario: Optional[str] = None


@app.post("/alias/item", dependencies=[Depends(exigir_chave)])
def salvar_alias_item(corpo: AliasItem, repo=Depends(get_repo)):
    repo.salvar_alias_item(corpo.nome, corpo.ic_id, corpo.usuario)
    return {"sucesso": True}
