# ADR 0002 — Identidade, normalização e deduplicação de vagas

- Status: aceito
- Data: 2026-07-18

## Contexto

Uma mesma vaga pode aparecer na página oficial, em um ATS e em um feed. URLs recebem
parâmetros de tracking, descrições mudam e vagas podem ser republicadas. O controle
original por URL vista perde essas relações e impede atualização.

## Decisão

A ingestão aplicará, em ordem:

1. `source_name + external_id`;
2. URL canônica;
3. empresa + hash da descrição;
4. empresa + título + localização normalizados;
5. similaridade textual somente entre candidatos já reduzidos pelos itens anteriores.

Cada encontro produzirá `new`, `updated`, `republished`, `duplicate` ou `unchanged`.
Toda descrição nova terá hash SHA-256 e snapshot. URLs de fontes secundárias serão
preservadas em `job_sources`; não substituirão silenciosamente a URL oficial.

## Consequências

- Falsos positivos de similaridade serão limitados pelo mesmo empregador e título.
- Alterações de descrição continuam auditáveis.
- O algoritmo será determinístico e testado com fixtures, sem embeddings obrigatórios.
- A tabela principal representa a vaga; aparições individuais ficam em `job_sources`.

