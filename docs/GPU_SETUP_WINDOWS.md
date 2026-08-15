# GPU no Windows 11 / Docker Desktop / WSL2

1. Atualize o driver NVIDIA no Windows; não instale driver Linux separado dentro do WSL.
2. Confirme `wsl --status` com versão padrão 2.
3. No Docker Desktop, habilite o backend WSL2 e contêineres Linux.
4. Reinicie o Docker Desktop e execute `nvidia-smi` no PowerShell.
5. Execute `scripts/bootstrap-local.ps1`.

Validação:

```powershell
.\scripts\test-gpu.ps1
docker compose exec -T ollama ollama ps
```

Após uma inferência, a coluna `PROCESSOR` do `ollama ps` deve indicar GPU. O diagnóstico também
mostra nome e VRAM quando `nvidia-smi` está disponível. `qwen3:8b` ocupa cerca de 5,2 GB no catálogo
do Ollama e é adequado à RTX 3060 de 12 GB. CPU só é habilitada com `OLLAMA_CPU_ONLY=true`.
