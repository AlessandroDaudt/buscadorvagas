# Pesquisa periodica

Em cada heartbeat:

1. Leia `/opt/autopilot/context/manifest.json` e os recibos recentes.
2. Se o contexto ou as decisoes mudaram, atualize apenas os padroes generalizados em `memory/company-patterns.md`.
3. Consulte metricas e benchmark; escolha uma hipotese que melhore cobertura ou reduza erros recentes.
4. Pesquise no maximo cinco novos portais oficiais usando o pipeline da skill `company-research`.
5. Nao repita empresas monitoradas, aprovadas, rejeitadas ou pendentes.
6. Envie somente candidatos verificaveis; se nao houver nenhum, nao crie lote.
7. Registre data, resultado, metricas observadas e proxima hipotese em `memory/research-state.md`, sem dados pessoais.

Responda `HEARTBEAT_OK` ao concluir.
