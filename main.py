from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import pandas as pd
import io
from supabase import create_client, Client

app = FastAPI(title="API Consolidador PEP - SESAP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Coloque as suas chaves do Supabase aqui novamente
SUPABASE_URL = "https://eacnghcsrajvluiuoqvm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVhY25naGNzcmFqdmx1aXVvcXZtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODYwMTQxNDQsImV4cCI6MjEwMTU5MDE0NH0.U6lM5gB9um6VRuDDP04hvc74aSOB1_aIG0Nn4onomM8"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/")
def home():
    return {"status": "online", "projeto": "SESAP ApuraSUS Engine"}

@app.post("/processar-pep")
async def processar_pep(files: List[UploadFile] = File(...)):
    try:
        response = supabase.table('dim_setores_hjpb').select('id, nome_setor_pep, dim_cc_pngc(nome, item_producao_padrao)').execute()
        
        if not response.data:
            return {"sucesso": False, "erro": "Não foi possível carregar o dicionário do Supabase."}
        
        df_depara = pd.DataFrame(response.data)
        
        def extrair_dado_cc(x, chave):
            if isinstance(x, dict):
                return x.get(chave)
            elif isinstance(x, list) and len(x) > 0:
                return x[0].get(chave)
            return 'Não Informado'

        df_depara['nome_cc_oficial'] = df_depara['dim_cc_pngc'].apply(lambda x: extrair_dado_cc(x, 'nome'))
        df_depara['item_producao'] = df_depara['dim_cc_pngc'].apply(lambda x: extrair_dado_cc(x, 'item_producao_padrao'))
        df_depara['nome_setor_pep'] = df_depara['nome_setor_pep'].astype(str).str.strip().str.upper()

        dfs_limpos = []

        # 1. Leitura Inteligente (Ignorando o nome do arquivo enviado)
        for file in files:
            conteudo = await file.read()
            df_temp = pd.read_excel(io.BytesIO(conteudo), header=None)
            
            header_idx = 4
            for idx, row in df_temp.iterrows():
                row_str = ' '.join(str(val).upper() for val in row.values)
                if 'SETOR' in row_str or 'UNIDADE' in row_str:
                    header_idx = idx
                    break
                    
            df_bruto = pd.read_excel(io.BytesIO(conteudo), skiprows=header_idx)
            col_setor = next((c for c in df_bruto.columns if 'SETOR' in str(c).upper() or 'UNIDADE' in str(c).upper()), df_bruto.columns[0])
            
            # BLINDAGEM: Lê as colunas para descobrir se é Internados ou Atendidos
            colunas_str = ' '.join(str(c).upper() for c in df_bruto.columns)
            
            if 'PERMANÊNCIA' in colunas_str or 'DIA' in colunas_str:
                origem = 'INTERNADOS'
                col_valor = next((c for c in df_bruto.columns if 'PERMANÊNCIA' in str(c).upper() or 'DIA' in str(c).upper()), df_bruto.columns[-1])
            else:
                origem = 'ATENDIDOS'
                # Força a busca por "Realizado" para evitar pegar "Pacientes atendidos"
                col_valor = next((c for c in df_bruto.columns if 'REALIZADO' in str(c).upper()), df_bruto.columns[-1])

            df_parcial = pd.DataFrame({
                'Setor PEP': df_bruto[col_setor].astype(str).str.strip().str.upper(),
                'Quantidade': pd.to_numeric(df_bruto[col_valor], errors='coerce'),
                'Origem': origem
            }).dropna(subset=['Setor PEP', 'Quantidade'])
            
            dfs_limpos.append(df_parcial)

        df_limpo_total = pd.concat(dfs_limpos, ignore_index=True)

        setores_planilha = df_limpo_total['Setor PEP'].unique()
        setores_banco = df_depara['nome_setor_pep'].unique()
        orfaos = [s for s in setores_planilha if s not in setores_banco and s not in ['TOTAIS', 'NAN', 'TOTAL', '']]

        # 2. Cruzamento Inicial
        df_cruzado = pd.merge(df_limpo_total, df_depara, left_on='Setor PEP', right_on='nome_setor_pep', how='inner')
        
        # 3. 🚨 FILTRO POKA-YOKE (Bloqueio de Soma Falsa) 🚨
        mask_internados = (df_cruzado['Origem'] == 'INTERNADOS') & (df_cruzado['item_producao'].str.contains('dia', case=False, na=False))
        mask_atendidos = (df_cruzado['Origem'] == 'ATENDIDOS') & (~df_cruzado['item_producao'].str.contains('dia', case=False, na=False))
        
        df_cruzado_filtrado = df_cruzado[mask_internados | mask_atendidos]

        # 4. Agrupamento Seguro
        df_final = df_cruzado_filtrado.groupby(['nome_cc_oficial', 'item_producao'], as_index=False)['Quantidade'].sum()

        resultados = df_final.rename(
            columns={'nome_cc_oficial': 'cc_pngc', 'Quantidade': 'quantidade'}
        ).to_dict(orient='records')

        return {"sucesso": True, "orfaos": orfaos, "resultados": resultados}

    except Exception as e:
        return {"sucesso": False, "erro": f"Erro interno: {str(e)}"}
