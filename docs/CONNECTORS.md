# Conectores

| Conector | Descoberta | Estado |
|---|---|---|
| Greenhouse | API pública `boards-api.greenhouse.io` | implementado e testado |
| Lever | API pública global/EU | implementado e testado |
| Ashby | job-board posting API pública | implementado e testado |
| SmartRecruiters | postings API pública | implementado e testado |
| Workable | job-board API pública | implementado e testado |
| JSON-LD | `JobPosting` em HTML estático | implementado e testado |
| HTML genérico | links estáticos permitidos por robots | implementado e testado |

O registry respeita `connector` explícito ou detecta pelo host. Entradas antigas continuam válidas;
para ATS hospedado em domínio da empresa, informe `connector` e o token (`board_token`, `site`,
`company_id` ou `account`). `allowed_domains` precisa conter qualquer host que possa ser acessado.

O genérico não executa JavaScript, autentica, resolve CAPTCHA nem acessa motores de busca. Uma fonte
dinâmica sem API/JSON-LD é registrada como `unsupported_source`. Workday, Gupy, SAP SuccessFactors,
iCIMS e Teamtailor não são declarados como suportados nesta versão.
