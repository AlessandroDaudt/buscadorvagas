# 01 — Instalação local

O caminho suportado para esta cópia é Docker Desktop com WSL2 e GPU NVIDIA. Não execute
`autopilot init` sobre a pasta existente: ela já contém perfil, currículo, histórico e
preferências que os scripts preservam.

## Pré-requisitos

- Windows 11, Docker Desktop com backend WSL2 e `docker compose`;
- driver NVIDIA com `nvidia-smi` funcional;
- PowerShell 7 recomendado;
- cerca de 8 GB livres para imagens e modelos.

## Instalação

Na raiz do projeto:

```powershell
.\scripts\bootstrap.ps1
```

O script valida os pré-requisitos, cria somente arquivos ausentes, constrói a imagem,
sobe o Ollama privado, baixa os modelos configurados e executa diagnóstico e inferência.
Ele nunca substitui o currículo existente.

Para executar diretamente no host, use Python 3.11+ e:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\autopilot doctor
```

O Compose publica Ollama e o painel somente em `127.0.0.1`; nenhuma chave de provedor
externo é necessária ou aceita.

## Próximos passos

- [Configuração local](02-providers.md)
- [Conectores diretos](04-companies-and-scanning.md)
- [GPU no Windows](GPU_SETUP_WINDOWS.md)
