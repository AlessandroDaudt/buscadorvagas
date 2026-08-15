# Contrato do contexto e da troca

O diretorio `/opt/autopilot/context` e somente leitura e e atualizado pelo Autopilot:

- `manifest.json`: data da geracao e contagens.
- `candidate_profile.json`: experiencia, competencias, idiomas e restricoes declaradas.
- `search_preferences.json`: cargos, localidades, senioridade, modalidade e filtros explicitos.
- `resume.json`: versao ativa aprovada em Markdown ou curriculo estruturado de reserva.
- `company_feedback.json`: empresas monitoradas e propostas aprovadas, rejeitadas ou pendentes.
- `job_feedback.json`: vagas salvas e descartadas; e um sinal fraco de preferencia.
- `learned_preferences.json`: restricoes, preferencias fortes e sinais aprendidos com peso e confianca.
- `company_profiles.json`: perfis enriquecidos das empresas conhecidas.
- `active_learning.json`: perguntas curtas que ainda podem melhorar o ranking.
- `research_metrics.json`: taxas de verificacao, aprovacao e decisao.
- `benchmark.json`: conjunto de exemplos rotulados para avaliar o ranking.
- `semantic_index.json`: indice local de embeddings consumido por `retrieve_context.py`.

## Arquivo de envio

O script aceita JSON com uma lista direta ou um objeto `{"candidates": [...]}`. Cada item usa:

```json
{
  "company_name": "Empresa",
  "careers_url": "https://empresa.example/careers",
  "rationale": "Motivo concreto do alinhamento com o perfil",
  "confidence": 0.82,
  "search_sources": ["https://empresa.example/about"],
  "matched_profile_signals": ["role_match", "remote_brazil", "Microsoft Sentinel"],
  "company_profile": {
    "industry": "cybersecurity",
    "company_size": "enterprise",
    "hiring_countries": ["Brazil"],
    "accepts_brazil_remote": true,
    "modalities": ["remote"],
    "tech_signals": ["Microsoft Sentinel", "SIEM"],
    "languages": ["English", "Portuguese"],
    "open_roles_count": 4,
    "source_urls": ["https://empresa.example/careers"]
  }
}
```

Regras: de 1 a 12 itens, HTTPS, nome e justificativa nao vazios, confianca entre 0 e 1. O Autopilot refaz as verificacoes de rede, robots, seguranca e tipo de portal. Itens aceitos entram como `pending`; o OpenClaw nao aprova nem rejeita propostas.
