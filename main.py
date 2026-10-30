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

from importador import ArquivoInvalido, ler_relatorio

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
