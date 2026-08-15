# Aprendizado local do OpenClaw

O pesquisador aprende apenas com dados locais e decisoes explicitas. Nao existe treinamento oculto
nem alteracao dos pesos do modelo. O aprendizado e feito por contexto versionado, memoria semantica,
regras deterministicas e exemplos rotulados.

## Ordem de autoridade

1. Restricoes rigidas do perfil e da busca.
2. Preferencias fortes declaradas pelo usuario.
3. Sinais aprendidos de vagas e empresas aprovadas ou recusadas.

Cada feedback pode incluir motivos estruturados e uma observacao. Um sinal aprendido registra peso,
confianca e numero de exemplos; poucos exemplos nunca viram uma regra rigida.

## Ciclo de pesquisa

1. Recuperar os trechos relevantes do curriculo e contexto pelo indice local de embeddings.
2. Gerar consultas sem informacoes pessoais.
3. Pesquisar no SearXNG privado, com busca web embutida como fallback.
4. Confirmar portal oficial e enriquecer o perfil da empresa.
5. Aplicar filtros rigidos, remover repeticoes e ranquear por aderencia.
6. Enviar ate 12 candidatos para verificacao e aprovacao humana.
7. Medir verificacao e aprovacao e comparar com o benchmark rotulado.

O painel em **Descoberta e alertas** mostra metricas, cobertura do benchmark e perguntas curtas de
aprendizado ativo. Os artefatos publicados para o pesquisador ficam em `state/openclaw/context/` e
sao recriados automaticamente pelo bridge.
