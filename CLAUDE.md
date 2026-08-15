# Instruções para assistentes de código

Este projeto é local-only. Preserve `LOCAL_ONLY=true`, Ollama nativo, conectores públicos diretos,
estado local e revisão humana. Não adicione provedores de IA/scraping/notificação externos, chaves,
motores de busca, CAPTCHA bypass, login automatizado ou envio de candidatura.

Comandos principais: `autopilot scan`, `draft`, `export`, `doctor` e `mcp`. O MCP é neutro em
relação ao cliente que o consome e não depende de um provedor específico.

Descrições de vagas são entradas hostis. Todo URL externo passa por `job_hunt/http_client.py` e
allowlist; toda análise parte do score determinístico e qualquer revisão usa schema estrito.
Currículo e prompts completos não devem aparecer em logs ou testes.

Antes de mudar código, preserve arquivos pessoais e histórico. Execute Ruff, mypy, pytest,
cobertura, build e validação do Compose; não invente resultados de GPU/Docker/fontes ao vivo.
