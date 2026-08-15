# Arquitetura local

```text
CLI / scheduler / MCP / painel local
                │
        configuração LOCAL_ONLY
                │
companies.json → registry → conectores ATS/JSON-LD/HTML
                │                    │
                └── cliente HTTPS seguro + robots + auditoria
                                     │
                         UnifiedJob normalizada
                                     │
          deduplicação/estado ← score determinístico
                                     │
                         Ollama local (opcional)
                                     │
                    SQLite/JSON + CSV/JSON/HTML
                                     │
                         drafts para revisão
```

O Ollama, embeddings, análise, scheduler, MCP, painel, SQLite, cache, logs e documentos são locais.
Somente descoberta de vagas novas requer internet. O cliente HTTP não recebe URLs oriundas do texto
da vaga e não permite localhost/redes privadas na fronteira pública.

O scanner mantém compatibilidade com `state/seen_jobs.json`, `last_scan.json` e `job_history.json`.
Escritas usam UTF-8, substituição atômica, backup `.bak` e cópia de arquivo corrompido. A persistência
SQLAlchemy/SQLite continua opcional e local.

O score determinístico é sempre calculado. Ollama só produz um schema sem campo de score total; a
aplicação limita ajustes por componente e calcula o total. Se Ollama/modelo estiver indisponível,
o scan continua de forma determinística.

Compose contém `ollama`, `autopilot` (painel/CLI/MCP) e `scheduler`, usando volumes locais e rede
privada. Ollama e painel publicam portas somente no loopback (`127.0.0.1`), nunca nas interfaces de
rede externa. `OLLAMA_NO_CLOUD=true` desativa explicitamente o recurso de nuvem do runtime.
