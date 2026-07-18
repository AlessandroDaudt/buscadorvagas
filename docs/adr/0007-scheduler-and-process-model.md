# ADR 0007: Scheduler e modelo de processos

- Status: aceito
- Data: 2026-07-18

## Contexto

As buscas precisam executar manualmente ou em dias e horários configuráveis, sem duas
execuções simultâneas e com limite de duração. O projeto ainda não possui uma fila de tarefas
nem volume de trabalho que justifique Redis, Celery ou outro broker.

## Decisão

Usar um processo de scheduler separado que calcula o próximo horário com `zoneinfo`, tendo
`America/Sao_Paulo` como padrão. Cada disparo cria um subprocesso do comando normal
`autopilot scan`, com timeout e retry limitado. Uma trava atômica em `state/scan.lock`, com
token de propriedade e recuperação por expiração, coordena execuções manuais e agendadas em
Windows e Linux.

O scanner registra cada execução em `SearchRun`, emite um relatório estruturado e atualiza um
snapshot de métricas. No Compose, painel e scheduler são processos independentes que usam o
mesmo PostgreSQL e volumes persistentes. Não há worker de fila: o subprocesso de scan é o
worker atual.

## Consequências

- instalação local continua simples e sem broker;
- falhas ou timeout do scanner não derrubam o painel;
- apenas uma busca pode executar por volume de estado compartilhado;
- múltiplos hosts exigirão no futuro um lock distribuído no PostgreSQL;
- uma fila deverá ser reconsiderada se geração de documentos e conectores passarem a exigir
  paralelismo horizontal ou reprocessamento individual.
