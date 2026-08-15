# Importação local de currículo

O painel aceita `.pdf`, `.doc`, `.docx`, `.md` e `.txt`, com limite padrão de 15 MiB. O arquivo de
origem é processado em um diretório temporário gerado pelo servidor e removido ao fim da operação;
apenas o Markdown revisável, o hash SHA-256 e os metadados ficam persistidos no SQLite.

## Fluxo pela interface

1. Abra `http://127.0.0.1:8000/resume` e selecione **Enviar novo currículo**.
2. Revise a prévia Markdown e os avisos de extração.
3. Edite e salve; cada edição cria uma nova versão, sem sobrescrever a anterior.
4. Execute a validação, corrija o que for necessário e aprove explicitamente.
5. Ative a versão aprovada. Somente uma versão pode estar ativa por vez.
6. Use **Baixar Markdown** para guardar uma cópia fora do banco.

Importar, aprovar e ativar são operações separadas. Nenhum upload se torna ativo automaticamente.
A versão ativa anterior permanece no histórico quando outra versão é ativada.

## Validações e proteção

O backend compara extensão, MIME e assinatura; recusa executáveis, NUL em texto, DOCM, macros e
binários incorporados. Para DOCX também limita a quantidade de entradas, o tamanho expandido e a
razão de compressão do ZIP. PDF tem limite de 100 páginas e arquivos corrompidos, criptografados ou
sem texto pesquisável retornam mensagens específicas. O nome original é sanitizado e nunca é usado
como caminho no armazenamento.

A validação de conteúdo aponta texto vazio ou curto, seções ausentes/duplicadas, caracteres
corrompidos, ausência aparente de datas e instruções possivelmente maliciosas. Avisos subjetivos não
apagam nem reescrevem fatos.

## Formatos e limitações

- **PDF:** extraído localmente com `pypdf`; links não são seguidos, JavaScript e anexos não são
  executados. PDFs somente com imagem exigem OCR local, que não vem incluído.
- **DOCX:** extraído com `python-docx` e XML local, preservando títulos, parágrafos, listas, tabelas,
  cabeçalhos e texto de hyperlinks quando disponíveis.
- **DOC:** convertido apenas por LibreOffice/`soffice` local, com modo headless, perfil isolado e
  timeout. A imagem Docker mínima não instala LibreOffice; nesse caso envie DOCX, PDF, MD ou TXT.
- **Markdown/TXT:** exigem UTF-8 e passam por normalização determinística.

Nenhum conversor, OCR ou modelo externo é chamado. O conteúdo do currículo não é gravado em logs.

## Backup e restauração

As versões ficam no volume de estado do SQLite. Documentos derivados ficam no volume `output`; o
currículo estruturado legado em `resume/` continua montado somente para leitura. Antes de mudanças
maiores, execute `scripts/backup-local.ps1`. Para restaurar uma versão pela interface, abra seu item
no histórico, salve uma nova edição se necessário, aprove e ative explicitamente.
