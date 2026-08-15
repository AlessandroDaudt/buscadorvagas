# Privacidade

O Autopilot Job Hunt opera em modo local por padrão (`LOCAL_ONLY=true`). Currículo, perfil,
prompts, embeddings, análises, banco, cache, logs, relatórios e documentos não são enviados a
provedores externos. Não há telemetria.

Durante um scan, somente páginas públicas configuradas em `companies.json`, endpoints públicos de
ATS e respectivos `robots.txt` são acessados. A auditoria `state/network_audit.jsonl` registra apenas
horário, conector, domínio, método, status, duração, tamanho e decisão de política; nunca corpo,
token, currículo ou descrição integral.

O Ollama executa no host ou na rede privada do Compose. Seu endpoint não é publicado na interface
externa. O painel opcional é ligado a `127.0.0.1`.

Dados locais:

- `resume/` e `config/`: perfil e currículo;
- `state/`: SQLite, JSON, cache, métricas, logs de auditoria;
- `output/`: relatórios e drafts;
- volumes `ollama_data`, `autopilot_state` e `autopilot_output` no Docker.

Faça backup com `scripts/backup-local.ps1`. Para excluir volumes, use conscientemente
`docker compose down --volumes` após o backup. Arquivos bind-mounted precisam ser removidos pelo
usuário separadamente. Nenhuma candidatura é submetida pelo sistema.
