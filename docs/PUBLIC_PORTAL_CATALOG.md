# Catálogo público de portais de carreira

O catálogo é uma camada incremental em `config/portal_catalog.json`. Ele preserva a origem, a licença,
o ATS, o slug/token e o URL canônico de cada entrada, sem substituir `companies.json` manualmente.
Quando um portal é validado e tem conector suportado, uma nova entrada é adicionada ao
`companies.json`; registros existentes nunca são editados ou removidos.

## Fontes e atribuição

| Fonte | Dados aceitos | Licença registrada |
| --- | --- | --- |
| `Feashliaa/job-board-aggregator` | Slugs Greenhouse, Lever e Ashby | MIT |
| `edwarddgao/openapply` | Slugs Greenhouse, Lever e Ashby | `NOASSERTION` (o repositório não declara licença) |
| `State of ATS 2026` | Nome, slug e atribuição de ATS | MIT |
| `tech-jobs-with-relocation` | Tabela de empresas e URLs de carreira | CC0-1.0 |
| Listas públicas SimplifyJobs | Apenas links ATS que permitem derivar com segurança o portal | `NOASSERTION` |

O `State of ATS 2026` fornece atribuição de ATS, mas não uma URL oficial de carreira. Essas entradas
ficam catalogadas e desativadas até que outra fonte forneça uma URL oficial verificável. As listas da
Simplify contêm links de vagas individuais; o importador descarta esses links e guarda somente a URL
canônica do board ATS quando o token pode ser extraído com certeza.

## Segurança e ativação

Antes de ativar, o importador exige HTTPS, DNS público e a política SSRF existente. Ele segue redirects
com o cliente HTTP seguro, verifica `robots.txt` e bloqueia LinkedIn, agregadores, redes sociais,
páginas de login e URLs de vaga individuais. Apenas Greenhouse, Lever, Ashby, SmartRecruiters,
Workable e os caminhos atuais de HTML/JSON-LD são ativáveis. ATS não suportados permanecem
desativados no catálogo e não entram no scheduler.

Para manter a execução local razoável, cada lote valida e ativa no máximo 120 novos portais. O painel
mantém uma fila automática: enquanto houver `pending_validation`, executa um único lote por vez,
aguarda 60 segundos e inicia o próximo. A fila é persistente, retoma tarefas interrompidas após
reinício e encerra quando a pendência chega a zero. Toda escrita usa a rotina atômica existente e
cria `.bak` antes de modificar um arquivo já existente.

O comportamento pode ser ajustado por `AUTO_IMPORT_PORTAL_CATALOG`,
`PORTAL_CATALOG_RECUR_INTERVAL_SECONDS` e `PORTAL_CATALOG_ACTIVATION_LIMIT`.

Abra **Descoberta e alertas** no painel e clique em **Importar e atualizar catálogo**. O resumo da
tarefa informa adicionados, atualizados, duplicados, inválidos, incompatíveis e ativados, com o
detalhamento por fonte e ATS no resultado persistido da tarefa.
