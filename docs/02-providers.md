# Backend de modelo

O único backend de produção é Ollama local. Configure modelos e endpoint em `config.json`; consulte
[arquitetura local](LOCAL_ARCHITECTURE.md). Provedores e endpoints externos são recusados quando
`LOCAL_ONLY=true`, que é o padrão.
