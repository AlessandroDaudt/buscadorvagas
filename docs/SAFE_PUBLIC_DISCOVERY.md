# Descoberta segura de portais e alertas LinkedIn

Esta funcionalidade descobre **somente portais públicos oficiais de carreira**. Ela não entra no
LinkedIn, não usa cookies, não usa sua senha, não tenta resolver CAPTCHA e não coleta resultados de
busca da plataforma.

## Fluxo de descoberta

Na página **Descoberta e alertas**, o botão de descoberta usa o Ollama local para sugerir um lote
pequeno de empresas e URLs de carreira que pareçam adequadas às preferências já configuradas. Cada
sugestão é então verificada sem navegador autenticado:

1. a URL precisa ser HTTPS e ter DNS público;
2. `linkedin.com`, redes sociais, agregadores e páginas de login são recusados;
3. a política SSRF, o limite de resposta e os redirects seguros são reaplicados;
4. `robots.txt` precisa permitir a coleta;
5. a página precisa ter sinal público de carreira/vaga;
6. o conector ATS é detectado quando possível.

O resultado é uma **proposta pendente**. Ela nunca entra em `companies.json` nem participa do scan
até que você clique em **Aprovar empresa**. Recusar uma proposta não altera nenhuma empresa existente.

O modelo local só sugere hipóteses; a validação de rede e sua aprovação são a fonte de confiança.
Não assuma que uma sugestão significa que existe uma vaga aberta.

Os scans agendados executam o mesmo ciclo antes de consultar as fontes aprovadas. Para desativar
somente essa descoberta automÃ¡tica, defina `AUTOPILOT_DISCOVERY_ON_SCHEDULE=false` no `.env`.

## Alertas manuais do LinkedIn

O formulário cria um link localmente para uma busca do LinkedIn usando as palavras-chave e a
localização escolhidas. Ao clicar em **Abrir busca manual**, o painel apenas registra a revisão e abre
o navegador em uma nova aba. Na página aberta, use a funcionalidade normal do LinkedIn para ativar
o alerta da plataforma, se quiser receber suas notificações.

O painel não faz requisições ao LinkedIn. A cadência configurada é um lembrete local de revisão; ela
não tenta criar ou ler alertas da plataforma.

## OpenClaw opcional

Há um perfil Docker opcional, isolado e sem porta publicada:

```powershell
docker compose --profile research up -d openclaw-research
```

Ele usa somente `http://ollama:11434`, não recebe currículo, banco SQLite, `companies.json`, cookies
de navegador ou socket Docker. O perfil desabilita a busca web do OpenClaw porque o provedor oficial
de busca requer uma sessão `ollama signin`, que não faz parte do modo local-only deste projeto.

O worker integrado do painel é o caminho suportado para a automação. O contêiner OpenClaw é um
companheiro opcional para experimentação local e não possui permissão para alterar fontes ou enviar
candidaturas.

### Aprendizado e pesquisa atuais

O pesquisador recebe uma copia curada, somente leitura, do perfil, curriculo aprovado, preferencias e
feedback. Nao recebe banco SQLite, credenciais, cookies de navegador nem socket Docker. A pesquisa
usa um SearXNG privado na rede `research` e conserva a busca web embutida como fallback; nenhum dos
servicos publica porta no host.

Motivos estruturados de aprovacao e rejeicao formam sinais auditaveis com peso e confianca.
Restricoes explicitas sempre prevalecem. O contexto inclui perfis enriquecidos, metricas, perguntas
de aprendizado ativo, benchmark rotulado e um indice local de embeddings. Toda sugestao continua
pendente ate passar pelo verificador do Autopilot e pela aprovacao humana.

## Limites e segurança

- Não use esse recurso para automatizar LinkedIn ou burlar seus controles.
- Revise toda empresa antes de aprovar.
- Mantenha o painel em `127.0.0.1`.
- Se uma fonte bloquear `robots.txt`, ela é ignorada.
- A auditoria de rede local registra apenas metadados de acesso, não currículo ou cookies.
