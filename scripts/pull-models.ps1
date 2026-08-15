. (Join-Path $PSScriptRoot 'common.ps1')
$chatModel = if ($env:OLLAMA_CHAT_MODEL) { $env:OLLAMA_CHAT_MODEL } else { 'qwen3:8b' }
$embeddingModel = if ($env:OLLAMA_EMBEDDING_MODEL) { $env:OLLAMA_EMBEDDING_MODEL } else { 'qwen3-embedding:0.6b' }
Invoke-Compose up -d ollama
Invoke-Compose exec -T ollama ollama pull $chatModel
Invoke-Compose exec -T ollama ollama pull $embeddingModel
Invoke-Compose exec -T ollama ollama list
