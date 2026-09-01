"""
API Consolidador PEP - SESAP / HJPB
------------------------------------
Motor de consolidação de relatórios do PEP para o padrão ApuraSUS (PNGC).
"""

import io
import re
from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from supabase import Client, create_client

app = FastAPI(title="API Consolidador PEP - SESAP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Configuração Oficial do Supabase (Chaves de Acesso Integradas)
# ---------------------------------------------------------------------------
SUPABASE_URL = "https://eacnghcsrajvluiuoqvm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVhY25naGNzcmFqdmx1aXVvcXZtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODYwMTQxNDQsImV4cCI6MjEwMTU5MDE0NH0.U6lM5gB9um6VRuDDP04hvc74aSOB1_aIG0Nn4onomM8"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------------------------------------------------------------------
# Configuração de palavras-chave (fonte única da verdade para o discovery)
# ---------------------------------------------------------------------------

SETOR_KEYWORDS_EXATAS = {"SETOR", "UNIDADE", "LOCAL", "CENTRO DO LEITO", "CENTRO"}
SETOR_KEYWORDS_SUBSTRING = ["CENTRO DO LEITO", "SETOR", "UNIDADE", "LOCAL"]

JUNK_VALUES = {"TOTAIS", "TOTAL", "NAN", "NONE", ""}
PADRAO_DATA_HORA = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}")
PADRAO_DIA_PALAVRA = re.compile(r"\bDIAS?\b")


# ---------------------------------------------------------------------------
# 1. Descoberta de cabeçalho (suporta 1 ou 2 linhas de header)
# ---------------------------------------------------------------------------

def _linha_bate_keyword_exata(row) -> bool:
    """True se alguma célula da linha for IGUAL (não apenas contém) a uma
    keyword de setor. Isso evita cair em falsos positivos como
    'Data Admissão Setor (Atual)'."""
    for val in row.values:
        if pd.isna(val):
            continue
        if str(val).strip().upper() in SETOR_KEYWORDS_EXATAS:
            return True
    return False


def _linha_parece_subheader(row) -> bool:
    """Detecta uma segunda linha de cabeçalho (ex.: 'Pacientes atendidos',
    'Atendimentos realizados' embaixo de 'Fevereiro/2026')."""
    hints = ["REALIZADO", "ATENDIDO", "PERMANÊNCIA", "PACIENTE", "DIA"]
    cells = [str(v).strip().upper() for v in row.values if pd.notna(v)]
    if len(cells) < 2:
        return False
    hits = sum(1 for c in cells if any(h in c for h in hints))
    return hits >= 2


def descobrir_estrutura(df_raw: pd.DataFrame):
    """Varre a planilha crua (sem header) e devolve (df_bruto, header_idx).

    Não depende de skiprows fixo. Localiza a linha cujo cabeçalho bate
    exatamente com uma keyword de setor; se a linha seguinte parecer um
    sub-cabeçalho de métricas, combina as duas linhas em um único nome de
    coluna (categoria + métrica).
    """
    header_idx = None
    for idx, row in df_raw.iterrows():
        if _linha_bate_keyword_exata(row):
            header_idx = idx
            break

    if header_idx is None:
        raise ValueError(
            "Não foi possível localizar automaticamente a linha de "
            "cabeçalho (nenhuma célula igual a SETOR/UNIDADE/LOCAL/"
            "CENTRO DO LEITO foi encontrada)."
        )

    linha_topo = df_raw.iloc[header_idx]
    tem_subheader = (
        header_idx + 1 < len(df_raw)
        and _linha_parece_subheader(df_raw.iloc[header_idx + 1])
    )

    if tem_subheader:
        linha_topo_ffill = linha_topo.ffill()
        linha_sub = df_raw.iloc[header_idx + 1]
        colunas = []
        for topo, sub in zip(linha_topo_ffill, linha_sub):
            topo_s = "" if pd.isna(topo) else str(topo).strip()
            sub_s = "" if pd.isna(sub) else str(sub).strip()
            combinado = f"{topo_s} {sub_s}".strip()
            colunas.append(combinado if combinado else "coluna_sem_nome")
        inicio_dados = header_idx + 2
    else:
        colunas = [
            str(v).strip() if pd.notna(v) else f"coluna_{i}"
            for i, v in enumerate(linha_topo)
        ]
        inicio_dados = header_idx + 1

    df_bruto = df_raw.iloc[inicio_dados:].copy()
    colunas_unicas = []
    contagem = {}
    for c in colunas:
        contagem[c] = contagem.get(c, 0) + 1
        colunas_unicas.append(c if contagem[c] == 1 else f"{c}_{contagem[c]}")
    df_bruto.columns = colunas_unicas
    df_bruto = df_bruto.reset_index(drop=True)
    return df_bruto, header_idx


# ---------------------------------------------------------------------------
# 2. Seleção de colunas (setor / métrica)
# ---------------------------------------------------------------------------

def encontrar_coluna_setor(colunas) -> str:
    upcols = {c: str(c).strip().upper() for c in colunas}

    # 1º: match exato
    for c, u in upcols.items():
        if u in SETOR_KEYWORDS_EXATAS:
            return c

    # 2º: substring, mas NUNCA em colunas de data
    for c, u in upcols.items():
        if "DATA" in u or "DT " in u:
            continue
        if any(k in u for k in SETOR_KEYWORDS_SUBSTRING):
            return c

    return colunas[0]


def detectar_origem_e_coluna_valor(colunas) -> Optional[tuple]:
    upcols = {c: str(c).strip().upper() for c in colunas}

    tem_permanencia = any("PERMANÊNCIA" in u for u in upcols.values())
    tem_dia = any(PADRAO_DIA_PALAVRA.search(u) for u in upcols.values())
    tem_realizado = any("REALIZADO" in u for u in upcols.values())

    if tem_permanencia or (tem_dia and not tem_realizado):
        origem = "INTERNADOS"
        for c, u in upcols.items():
            if "PERMANÊNCIA" in u and "SETOR" in u:
                return origem, c
        for c, u in upcols.items():
            if "PERMANÊNCIA" in u:
                return origem, c
        for c, u in upcols.items():
            if PADRAO_DIA_PALAVRA.search(u):
                return origem, c
        return origem, colunas[-1]

    if tem_realizado:
        origem = "ATENDIDOS"
        for c, u in upcols.items():
            if "TOTA" in u and "REALIZADO" in u:
                return origem, c
        for c, u in upcols.items():
            if "REALIZADO" in u:
                return origem, c
        return origem, colunas[-1]

    return None


# ---------------------------------------------------------------------------
# 3. Filtro anti-lixo
# ---------------------------------------------------------------------------

def limpar_valor_setor(valor) -> Optional[str]:
    v = str(valor).strip().upper()
    if v in JUNK_VALUES:
        return None
    if PADRAO_DATA_HORA.match(v):
        return None
    if "IMPRESSO EM" in v or v.startswith("RELATÓRIO"):
        return None
    if len(v) < 3:
        return None
    return v


# ---------------------------------------------------------------------------
# 4. Endpoint
# ---------------------------------------------------------------------------

@app.get("/")
def home():
    return {"status": "online", "projeto": "SESAP ApuraSUS Engine"}


@app.post("/processar-pep")
async def processar_pep(files: List[UploadFile] = File(...)):
    try:
        response = (
            supabase.table("dim_setores_hjpb")
            .select("id, nome_setor_pep, dim_cc_pngc(nome, item_producao_padrao)")
            .execute()
        )

        if not response.data:
            return {
                "sucesso": False,
                "erro": "Não foi possível carregar o dicionário do Supabase.",
            }

        df_depara = pd.DataFrame(response.data)

        def extrair_dado_cc(x, chave):
            if isinstance(x, dict):
                return x.get(chave)
            elif isinstance(x, list) and len(x) > 0:
                return x[0].get(chave)
            return "Não Informado"

        df_depara["nome_cc_oficial"] = df_depara["dim_cc_pngc"].apply(
            lambda x: extrair_dado_cc(x, "nome")
        )
        df_depara["item_producao"] = df_depara["dim_cc_pngc"].apply(
            lambda x: extrair_dado_cc(x, "item_producao_padrao")
        )
        df_depara["nome_setor_pep"] = (
            df_depara["nome_setor_pep"].astype(str).str.strip().str.upper()
        )

        dfs_limpos = []
        avisos = []

        for file in files:
            conteudo = await file.read()
            df_raw = pd.read_excel(io.BytesIO(conteudo), header=None)

            try:
                df_bruto, _ = descobrir_estrutura(df_raw)
            except ValueError as e:
                avisos.append(f"{file.filename}: {e}")
                continue

            col_setor = encontrar_coluna_setor(df_bruto.columns)
            deteccao = detectar_origem_e_coluna_valor(df_bruto.columns)

            if deteccao is None:
                avisos.append(
                    f"{file.filename}: não foi possível identificar se é "
                    "relatório de Internados ou Atendidos."
                )
                continue

            origem, col_valor = deteccao
            setores_limpos = df_bruto[col_setor].apply(limpar_valor_setor)

            df_parcial = pd.DataFrame(
                {
                    "Setor PEP": setores_limpos,
                    "Quantidade": pd.to_numeric(df_bruto[col_valor], errors="coerce"),
                    "Origem": origem,
                }
            ).dropna(subset=["Setor PEP", "Quantidade"])

            dfs_limpos.append(df_parcial)

        if not dfs_limpos:
            return {
                "sucesso": False,
                "erro": "Nenhum arquivo pôde ser processado.",
                "avisos": avisos,
            }

        df_limpo_total = pd.concat(dfs_limpos, ignore_index=True)

        setores_planilha = df_limpo_total["Setor PEP"].unique()
        setores_banco = df_depara["nome_setor_pep"].unique()
        orfaos = [s for s in setores_planilha if s not in setores_banco]

        # Cruzamento com o De-Para
        df_cruzado = pd.merge(
            df_limpo_total,
            df_depara,
            left_on="Setor PEP",
            right_on="nome_setor_pep",
            how="inner",
        )

        # 🚨 FILTRO POKA-YOKE (Bloqueio de Soma Falsa) 🚨
        mask_internados = (df_cruzado["Origem"] == "INTERNADOS") & (
            df_cruzado["item_producao"].str.contains("dia", case=False, na=False)
        )
        mask_atendidos = (df_cruzado["Origem"] == "ATENDIDOS") & (
            ~df_cruzado["item_producao"].str.contains("dia", case=False, na=False)
        )
        df_cruzado_filtrado = df_cruzado[mask_internados | mask_atendidos]

        df_final = df_cruzado_filtrado.groupby(
            ["nome_cc_oficial", "item_producao"], as_index=False
        )["Quantidade"].sum()

        resultados = df_final.rename(
            columns={"nome_cc_oficial": "cc_pngc", "Quantidade": "quantidade"}
        ).to_dict(orient="records")

        return {
            "sucesso": True,
            "orfaos": orfaos,
            "resultados": resultados,
            "avisos": avisos,
        }

    except Exception as e:
        return {"sucesso": False, "erro": f"Erro interno: {str(e)}"}
