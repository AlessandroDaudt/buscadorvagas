# Política de rede

Saídas permitidas durante descoberta: URLs HTTPS de carreira em `companies.json`, endpoints
públicos dos ATS implementados e `robots.txt` dos mesmos domínios. Ollama usa apenas loopback/rede
Compose. Não há busca externa, proxy rotativo, OCR/SaaS, webhook, telemetria ou notificação remota.

Controles: allowlist exata por empresa/conector, resolução DNS antes de cada redirect, bloqueio de
localhost/redes privadas/link-local/reservadas/metadata, portas 80/443 (com HTTPS obrigatório no
cliente de produção), timeout, tamanho máximo, tipos MIME, retry curto, backoff, cadência por domínio,
cache e User-Agent identificável.

O fallback genérico consulta robots. Respostas podem terminar em `blocked_by_robots`,
`captcha_detected`, `authentication_required`, `rate_limited`, `unsupported_source`,
`temporarily_unavailable`, `invalid_domain`, `unsafe_redirect` ou `response_too_large`.

`state/network_audit.jsonl` contém somente metadados mínimos. Descrições, bodies, currículo, tokens
e prompts nunca fazem parte da auditoria.
