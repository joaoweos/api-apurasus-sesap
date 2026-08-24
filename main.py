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
        # 1. Puxa o Dicionário solicitando também a nova coluna 'item_producao_padrao'
        response = supabase.table('dim_setores_hjpb').select('id, nome_setor_pep, dim_cc_pngc(nome, item_producao_padrao)').execute()
        
        if not response.data:
            return {"sucesso": False, "erro": "Não foi possível carregar a tabela dim_setores_hjpb do Supabase."}
        
        df_depara = pd.DataFrame(response.data)
        
        # Função auxiliar para extrair dados aninhados do Supabase
        def extrair_dado_cc(x, chave):
            if isinstance(x, dict):
                return x.get(chave)
            elif isinstance(x, list) and len(x) > 0:
                return x[0].get(chave)
            return 'Não Informado'

        # Extrai o nome e o item de produção oficial do banco de dados
        df_depara['nome_cc_oficial'] = df_depara['dim_cc_pngc'].apply(lambda x: extrair_dado_cc(x, 'nome'))
        df_depara['item_producao'] = df_depara['dim_cc_pngc'].apply(lambda x: extrair_dado_cc(x, 'item_producao_padrao'))
        df_depara['nome_setor_pep'] = df_depara['nome_setor_pep'].astype(str).str.strip().str.upper()

        # 2. Leitura com Pandas
        conteudo = await file.read()
        df_bruto = pd.read_excel(io.BytesIO(conteudo), skiprows=4)

        col_setor = next((c for c in df_bruto.columns if 'SETOR' in str(c).upper() or 'UNIDADE' in str(c).upper()), df_bruto.columns[0])
        col_valor = df_bruto.columns[-1]

        df_limpo = pd.DataFrame({
            'Setor PEP': df_bruto[col_setor].astype(str).str.strip().str.upper(),
            'Quantidade': pd.to_numeric(df_bruto[col_valor], errors='coerce')
        }).dropna(subset=['Setor PEP', 'Quantidade'])

        # 3. Poka-Yoke: Identificação de Órfãos
        setores_planilha = df_limpo['Setor PEP'].unique()
        setores_banco = df_depara['nome_setor_pep'].unique()
        
        orfaos = [s for s in setores_planilha if s not in setores_banco and s not in ['TOTAIS', 'NAN', 'TOTAL', '']]

        # 4. Cruzamento e Agrupamento
        df_cruzado = pd.merge(df_limpo, df_depara, left_on='Setor PEP', right_on='nome_setor_pep', how='inner')
        
        # O Agrupamento agora leva em consideração o Item de Produção vindo do Supabase
        df_final = df_cruzado.groupby(['nome_cc_oficial', 'item_producao'], as_index=False)['Quantidade'].sum()

        resultados = df_final.rename(
            columns={'nome_cc_oficial': 'cc_pngc', 'Quantidade': 'quantidade'}
        ).to_dict(orient='records')

        return {"sucesso": True, "orfaos": orfaos, "resultados": resultados}

    except Exception as e:
        return {"sucesso": False, "erro": f"Erro interno no processamento: {str(e)}"}
