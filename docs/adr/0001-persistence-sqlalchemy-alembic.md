# ADR 0001 — Persistência com SQLAlchemy, Alembic, SQLite e PostgreSQL

- Status: aceito
- Data: 2026-07-18

## Contexto

O projeto original persiste estado em arquivos JSON e CSV. Esse formato não oferece
transações, migrations, relacionamentos, concorrência segura nem histórico consistente.
O produto precisa continuar simples para uso pessoal no Windows e no Linux, mas também
deve ter uma opção de produção.

## Decisão

Usaremos SQLAlchemy 2 como camada relacional e Alembic para migrations.

- SQLite será o padrão local, com foreign keys habilitadas e WAL quando suportado.
- PostgreSQL será a opção recomendada para produção.
- IDs serão UUIDs armazenados como strings para manter portabilidade entre bancos.
- Timestamps serão armazenados em UTC.
- A aplicação acessará dados por repositories/sessões, não por SQL montado em strings.
- JSON/CSV continuarão disponíveis como importação, exportação e backup durante a
  migração.

## Consequências

- Entram duas dependências centrais: SQLAlchemy e Alembic.
- Toda mudança de schema exigirá migration e teste de upgrade.
- SQLite não será usado como mecanismo de lock distribuído em produção.
- O suporte PostgreSQL poderá ser ativado instalando o extra correspondente e definindo
  `DATABASE_URL` no ambiente.

