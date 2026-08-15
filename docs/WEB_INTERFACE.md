# Interface web local

A interface é uma camada completa sobre os mesmos serviços Python usados pela CLI e pelo MCP. Ela
não possui login: o limite de confiança é a própria máquina. O Compose publica o painel somente em
`http://127.0.0.1:8000`; não o exponha na rede, não crie túnel público e não coloque um proxy público
à frente dele.

## Iniciar e acessar

```powershell
docker compose up -d ollama autopilot scheduler
docker compose ps
```

Abra `http://127.0.0.1:8000`. O processo dentro do contêiner escuta na interface do contêiner para
que o redirecionamento Docker funcione, mas a publicação no host está fixada em `127.0.0.1`. Para
Python no host, `autopilot web` usa `127.0.0.1` por padrão.

As áreas disponíveis são:

- **Visão geral:** totais reais, oportunidades, última busca e saúde local;
- **Vagas:** filtros combináveis, paginação, detalhe, fontes, descrição, score, salário, pipeline,
  notas, documentos e estado salva/descartada;
- **Buscas:** execução assíncrona, progresso, conflito de busca única e histórico persistido;
- **Empresas:** CRUD, duplicação e teste do conector sob as mesmas regras SSRF/allowlist/robots;
- **Currículo:** importação, revisão Markdown, versões, validação, aprovação explícita e ativação;
- **Documentos:** listagem, versão, edição textual, regeneração, exclusão confirmada e download;
- **Exportações:** CSV, JSON e HTML em tarefa persistida, com link temporário assinado;
- **Agendamento:** fuso, dias, horário, habilitação e execução imediata;
- **Sistema:** SQLite, Ollama, modelos, GPU/VRAM, disco, auditoria, doctor, warmup e embeddings;
- **Configurações:** preferências tipadas e somente os parâmetros seguros do Ollama local.

## Segurança do navegador

- `TrustedHostMiddleware` aceita somente hosts locais configurados;
- toda mutação exige token CSRF ligado à sessão e rejeita `Origin`/`Referer` de outro host;
- cookies são `HttpOnly`, `SameSite=Strict` e não representam identidade/autenticação;
- CSP não permite script ou estilo inline, frames, objetos ou origem externa;
- corpos têm limite global e uploads de currículo têm limite adicional de 15 MiB;
- downloads de artefatos usam tokens assinados com validade curta e validação contra traversal;
- exclusão e limpeza de cache exigem confirmação explícita além do CSRF;
- erros de tarefas e logs passam por redação de senhas, chaves e tokens.

`PANEL_SESSION_SECRET` é opcional. Defini-lo mantém estáveis entre reinícios o cookie CSRF e os links
temporários já emitidos; ele não habilita autenticação. Variáveis antigas de usuário/hash são ignoradas.

## Currículo e arquivos

Formatos aceitos: PDF, DOC, DOCX, Markdown e TXT. O servidor verifica extensão, MIME, assinatura,
tamanho, UTF-8, executáveis, macros/binários em DOCX, quantidade e expansão do ZIP e razão de
compressão. Arquivos fonte vivem apenas em diretório temporário aleatório e são removidos ao final;
o Markdown, hash, metadados e histórico ficam no SQLite.

- PDF sem camada de texto é recusado com indicação de OCR local; OCR não é feito silenciosamente.
- DOC legado requer `soffice`/LibreOffice local. A imagem Docker mínima não inclui esse componente e
  retorna uma mensagem clara; converter para DOCX é a alternativa recomendada.
- DOCX é lido estruturalmente, sem executar macros. Títulos, listas, tabelas, cabeçalhos e texto de
  hyperlinks são preservados quando representados no XML.
- A extração é determinística e não reescreve fatos com IA. Uma versão editada é sempre nova; apenas
  uma versão validada e aprovada explicitamente pode se tornar ativa.

O currículo Markdown importado é usado pelos documentos auxiliares do Ollama. O pacote legado de
currículo direcionado/carta continua usando o `MasterResume` estruturado aprovado, preservando a
compatibilidade da CLI; o manifesto registra também a versão Markdown ativa quando existente.

## Tarefas e agendamento

Buscas, documentos, exportações e diagnósticos longos são registros em `web_tasks`. Reiniciar o
painel marca tarefas interrompidas como falhas recuperáveis. Busca e geração já iniciadas não são
canceladas no meio de uma escrita não segura; tarefas cooperativas só cancelam em checkpoints.

O scheduler recarrega `config/search_preferences.json` no intervalo de polling. Assim, uma alteração
salva na interface passa a valer sem recriar contêineres. O lock em `state/scan.lock` impede busca web,
CLI e scheduler de executarem simultaneamente.

## Persistência e backup

- SQLite, tarefas, versões e auditoria: volume `autopilot_state`;
- documentos, relatórios e exportações: volume `autopilot_output`;
- modelos: `ollama_data`;
- `companies.json`, `config.json` e `config/`: binds graváveis com backup `.bak` e troca atômica;
- `resume/`: bind somente leitura, para preservar o currículo estruturado original.

Use `scripts/backup-local.ps1` antes de mudanças maiores. A interface não envia candidatura, não
preenche sites e não faz upload do currículo a terceiros.

## Diagnóstico rápido

```powershell
docker compose config
docker compose ps
docker compose logs --tail 100 autopilot
docker compose run --rm autopilot autopilot doctor
```

Se a página abrir mas o Ollama estiver indisponível, filtros, histórico, currículo, exportações e
documentos existentes continuam funcionando. Warmup e documentos auxiliares retornam erro de tarefa
sem fallback para nuvem.

## Descoberta de fontes e alertas manuais

A área **Descoberta e alertas** propõe somente páginas públicas oficiais de carreira. As propostas
passam por HTTPS, DNS público, SSRF, `robots.txt` e validação de conector antes de aguardarem sua
aprovação explícita. O LinkedIn é deliberadamente excluído da automação: o painel cria apenas links
de busca para que você, em uma aba normal do navegador, possa ativar o alerta da própria plataforma.

Veja [SAFE_PUBLIC_DISCOVERY.md](SAFE_PUBLIC_DISCOVERY.md) para o fluxo, os limites e o perfil
OpenClaw opcional sem credenciais, cookies ou acesso aos dados pessoais.
