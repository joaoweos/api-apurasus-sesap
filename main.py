from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
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

SUPABASE_URL = "https://eacnghcsrajvluiuoqvm.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVhY25naGNzcmFqdmx1aXVvcXZtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODYwMTQxNDQsImV4cCI6MjEwMTU5MDE0NH0.U6lM5gB9um6VRuDDP04hvc74aSOB1_aIG0Nn4onomM8"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.get("/")
def home():
    return {"status": "online", "projeto": "SESAP ApuraSUS Engine"}

@app.post("/processar-pep")
async def processar_pep(file: UploadFile = File(...)):
    try:
        # 1. Puxa Dicionário do Supabase
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

        # 2. Leitura Inteligente do Arquivo
        conteudo = await file.read()
        
        # Lemos primeiro sem pular linhas para DESCOBRIR onde o cabeçalho está
        df_temp = pd.read_excel(io.BytesIO(conteudo), header=None)
        
        header_idx = 4 # Fallback padrão
        for idx, row in df_temp.iterrows():
            row_str = ' '.join(str(val).upper() for val in row.values)
            if 'SETOR' in row_str or 'UNIDADE' in row_str:
                header_idx = idx
                break
                
        # Agora lemos pulando a quantidade exata de lixo do cabeçalho
        df_bruto = pd.read_excel(io.BytesIO(conteudo), skiprows=header_idx)

        # Acha a coluna do Setor
        col_setor = next((c for c in df_bruto.columns if 'SETOR' in str(c).upper() or 'UNIDADE' in str(c).upper()), df_bruto.columns[0])
        
        # Acha a coluna do Valor com Fuzzy Match (busca elástica)
        col_valor = None
        for c in df_bruto.columns:
            c_upper = str(c).upper()
            if any(palavra in c_upper for palavra in ['PERMANÊNCIA', 'PERMANENCIA', 'PACIENTE', 'DIA', 'MÉDIA']):
                col_valor = c
                break
            elif any(palavra in c_upper for palavra in ['REALIZADO', 'ATENDIMENTO', 'ATENDIDOS']):
                col_valor = c
                break
                
        # Se mesmo assim não achar a palavra exata, pega a última coluna com valores numéricos
        if not col_valor:
            col_valor = df_bruto.columns[-1]

        # 3. Limpeza
        df_limpo = pd.DataFrame({
            'Setor PEP': df_bruto[col_setor].astype(str).str.strip().str.upper(),
            'Quantidade': pd.to_numeric(df_bruto[col_valor], errors='coerce')
        }).dropna(subset=['Setor PEP', 'Quantidade'])

        # 4. Cruzamento Poka-Yoke
        setores_planilha = df_limpo['Setor PEP'].unique()
        setores_banco = df_depara['nome_setor_pep'].unique()
        orfaos = [s for s in setores_planilha if s not in setores_banco and s not in ['TOTAIS', 'NAN', 'TOTAL', '']]

        df_cruzado = pd.merge(df_limpo, df_depara, left_on='Setor PEP', right_on='nome_setor_pep', how='inner')
        df_final = df_cruzado.groupby(['nome_cc_oficial', 'item_producao'], as_index=False)['Quantidade'].sum()

        resultados = df_final.rename(
            columns={'nome_cc_oficial': 'cc_pngc', 'Quantidade': 'quantidade'}
        ).to_dict(orient='records')

        return {"sucesso": True, "orfaos": orfaos, "resultados": resultados}

    except Exception as e:
        return {"sucesso": False, "erro": f"Erro interno no processamento: {str(e)}"}
