# API Consolidador SESAP – relatórios do ApuraSUS

## Variáveis de ambiente (Render > Environment)
| Nome | Para quê |
| --- | --- |
| `SUPABASE_URL` | endereço do projeto no Supabase |
| `SUPABASE_SERVICE_KEY` | chave de serviço (service_role); nunca vai para o navegador nem para o GitHub |
| `API_KEY` | senha que o app (Base44/Make) envia no cabeçalho `X-API-Key` |
| `CORS_ORIGINS` | endereços do app, separados por vírgula (vazio = qualquer origem) |

## Endpoints
- `GET /` — verifica se está no ar.
- `POST /ler-relatorio` — lê um CSV bruto do ApuraSUS e devolve a prévia (tipo, instituição, período, meses, linhas, erros de conferência, avisos). **Ainda não grava no banco.**
  Campo `arquivo` (obrigatório) e `tipo` (opcional): `custo_total_unidade`, `matriz_item_centro`, `centro_produto`.
- `POST /importar` — lê, confere e **grava** o relatório na unidade (`unidade_id`). Com nomes sem correspondência, nada é gravado e a resposta traz as `pendencias` com sugestões; depois de resolvê-las, reenvie o mesmo arquivo.
- `GET /dimensoes` — centros e itens padrão (para as listas de escolha das pendências).
- `POST /de-para/centro` e `POST /alias/item` — salvam a resolução de uma pendência.

## Estrutura
- `importador/comum.py` — codificação, separador, números, datas, nomes.
- `importador/custo_total_unidade.py`, `matriz_item_centro.py`, `centro_produto.py` — um leitor por layout.
- `importador/registro.py` — detecta o tipo pelo cabeçalho e escolhe o leitor.
- `importador/resolver.py` — casa nomes com as dimensões e o de-para da unidade.
- `importador/gravar.py` — fluxo de importação (conferir, resolver, gravar, registrar).
- `importador/repositorios.py` — acesso a dados (Supabase e uma versão em memória para testes).

## Testes
Os testes usam arquivos reais do ApuraSUS, que não ficam no repositório:

    pip install -r requirements-dev.txt
    AMOSTRAS_DIR=/pasta/com/os/csv pytest -q
