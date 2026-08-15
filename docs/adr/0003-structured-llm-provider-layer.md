# ADR 0003: revisão estruturada com Ollama local

Status: substituído em 2026-07-20.

Toda revisão usa a API HTTP nativa do Ollama e schema Pydantic estrito. Não há roteamento/fallback
para nuvem. O motor determinístico controla o score final e continua funcionando sem modelo.
