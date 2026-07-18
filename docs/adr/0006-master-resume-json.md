# ADR 0006 — Currículo mestre factual em JSON

- Status: aceito
- Data: 2026-07-18

## Contexto

O currículo original é Markdown livre. O sistema precisa reorganizar conteúdo para cada
vaga sem inventar experiências, datas, tecnologias, certificações ou métricas.

## Decisão

O currículo mestre terá um documento JSON validado por Pydantic como fonte factual.

- Campos desconhecidos serão `null` ou listas vazias, nunca inferidos.
- Cada experiência terá responsabilidades, resultados e tecnologias separadas.
- Conteúdo aprovado poderá ser marcado como tal e receber versão/hash.
- Markdown, DOCX e PDF serão formatos derivados, não fontes primárias.
- Importações de PDF/DOCX serão rascunhos que exigem confirmação antes de substituir a
  fonte factual.

## Consequências

- O JSON é mais verboso que Markdown, mas não exige parser adicional e permite validação
  rigorosa.
- A renderização de documentos será separada do armazenamento dos fatos.
- Alterações no schema exigirão versão e migration do documento.

