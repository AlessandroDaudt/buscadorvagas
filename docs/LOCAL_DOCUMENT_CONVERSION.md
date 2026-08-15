# Conversão local de documentos

Toda extração e conversão ocorre na máquina do usuário. O painel não envia currículos para APIs,
conversores online ou serviços de OCR. As saídas são gravadas nos volumes locais de estado e
`output`, e downloads usam tokens assinados de curta duração com validação de caminho.

## Pipeline

```text
upload validado -> diretório temporário aleatório -> extração local
                 -> Markdown determinístico -> revisão -> versão no SQLite
```

O serviço dedicado está em `job_hunt/resume_import/`: `validation.py` protege a entrada,
`pdf.py` usa `pypdf`, `docx.py` usa `python-docx`/XML, `legacy_doc.py` controla o LibreOffice e
`markdown.py` normaliza a saída. As rotas FastAPI não implementam essas regras diretamente.

## PDF

São extraídos texto, contagem de páginas, título, autor e indicação das páginas sem texto. URLs não
são acessadas. PDF criptografado ou corrompido é recusado; PDF apenas com imagens informa que OCR
local é necessário. OCR/Tesseract não faz parte da imagem padrão.

## DOCX

O DOCX é validado como ZIP antes da leitura. Arquivos com macros, entradas `.bin`, expansão
excessiva, muitas entradas ou compressão suspeita são recusados. Imagens decorativas são ignoradas;
títulos, listas, tabelas, cabeçalhos e hyperlinks textuais são convertidos para Markdown sem executar
conteúdo ativo.

## DOC legado

Arquivos `.doc` só são aceitos quando possuem assinatura OLE válida. A conversão usa
`soffice --headless --convert-to docx` com perfil isolado, timeout, saída em diretório temporário e
validação do DOCX resultante. Os temporários são removidos mesmo quando a conversão falha.

A imagem Docker mínima deliberadamente não instala LibreOffice para não aumentar significativamente
o tamanho e a superfície de ataque. Sem `soffice`, a interface exibe uma orientação explícita para
usar DOCX, PDF ou Markdown. Não há fallback online.

## Geração de documentos

O pacote determinístico já existente de currículo direcionado e cover letter continua usando o
`MasterResume` estruturado e aprovado, preservando CLI/MCP. A versão Markdown ativa é registrada no
manifesto. Preparação para entrevista, análise de lacunas, perguntas e plano de estudo exigem uma
versão Markdown aprovada e usam exclusivamente o Ollama local.

Formatos textuais disponíveis na web incluem Markdown, TXT e HTML; o gerador existente também cria
DOCX e tenta PDF somente quando o conversor local necessário está presente. Nenhuma candidatura é
enviada e nenhum formulário externo é preenchido.
