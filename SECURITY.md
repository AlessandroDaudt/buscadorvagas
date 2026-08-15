# Segurança

## Limites de confiança

- perfil/currículo/configuração local são dados confiáveis do usuário;
- descrições, HTML, JSON-LD e respostas de ATS são não confiáveis;
- Ollama e serviços Compose são locais;
- páginas públicas de vagas são a única fronteira externa do scan.

`LOCAL_ONLY` bloqueia provedores externos, chaves, Telegram/WhatsApp e webhooks. Endpoints de modelo
só podem usar loopback ou nomes internos aprovados. Não há fallback de nuvem.

Todo acesso de conectores passa pelo cliente central: HTTPS, allowlist, validação DNS contra SSRF,
bloqueio de redes privadas/link-local/metadata, redirects revalidados, limite de tamanho,
`Content-Type`, timeout, retry limitado, rate limit, cache e auditoria. O fallback genérico respeita
`robots.txt` e abandona fontes com CAPTCHA ou login, sem tentar contorno.

Prompts delimitam a vaga como conteúdo não confiável. O modelo não escolhe URLs, executa código,
acessa arquivos ou controla o score final. O score determinístico existe independentemente do
Ollama; ajustes estruturados são validados e limitados. Drafts passam por guarda factual e sempre
exigem revisão humana.

Contêineres usam usuário não-root quando possível, `no-new-privileges`, capabilities removidas,
logs rotacionados e portas somente em `127.0.0.1`. Configuração/currículo entram por bind mount
somente leitura e não são incorporados à imagem final.

Relate vulnerabilidades privadamente aos mantenedores do repositório. Não inclua currículo, chaves,
logs integrais ou descrições de vagas no relato.
