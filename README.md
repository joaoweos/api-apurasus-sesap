# API Consolidador SESAP – relatórios do ApuraSUS

## Variáveis de ambiente (Render > Environment)
| Nome | Para quê |
| --- | --- |
| `SUPABASE_URL` | endereço do projeto no Supabase |
| `SUPABASE_SERVICE_KEY` | chave de serviço (service_role); nunca vai para o navegador nem para o GitHub |
| `API_KEY` | senha que o app (Base44/Make) envia no cabeçalho `X-API-Key` |
| `CORS_ORIGINS` | endereços do app, separados por vírgula (vazio = qualquer origem) |

## Endpoints (todos exigem o cabeçalho `X-API-Key`; os de dados exigem também `usuario_email`)
- `GET /` — verifica se está no ar.
- `GET /eu` — perfil do usuário e a unidade dele.
- `GET /unidades` — unidades que o usuário pode escolher (todas para o master).
- `GET /dimensoes` — centros e itens padrão (listas de escolha das pendências).
- `GET /importacoes` — histórico das últimas importações da unidade.
- `POST /ler-relatorio` — prévia de um CSV do ApuraSUS, sem gravar.
- `POST /importar` — lê, confere e **grava** o relatório. O gestor sempre importa na própria unidade; só o master informa `unidade_id`. Com nomes sem correspondência nada é gravado e a resposta traz `pendencias`.
- `POST /de-para/centro` — resolve uma pendência de centro (na unidade do usuário).
- `POST /alias/item` — resolve uma pendência de item (vale para todas as unidades: só o master).

Perfis (tabela `usuarios_unidade`): `master_sesap`, `gestor_unidade`, `leitura_unidade`.

## Estrutura
- `importador/comum.py` — codificação, separador, números, datas, nomes.
- `importador/custo_total_unidade.py`, `matriz_item_centro.py`, `centro_produto.py` — um leitor por layout.
- `importador/registro.py` — detecta o tipo pelo cabeçalho e escolhe o leitor.
- `importador/resolver.py` — casa nomes com as dimensões e o de-para da unidade.
- `importador/gravar.py` — fluxo de importação (conferir, resolver, gravar, registrar).
- `importador/acesso.py` — regras de permissão por perfil.
- `importador/repositorios.py` — acesso a dados (Supabase e uma versão em memória para testes).

## Testes
Os testes usam arquivos reais do ApuraSUS, que não ficam no repositório:

    pip install -r requirements-dev.txt
    AMOSTRAS_DIR=/pasta/com/os/csv pytest -q
