# Registro da implementação — Criar vídeo

Implementação local no checkout solicitado. A verificação final com
`python tests/run.py` terminou com `All tests passed.`, incluindo os 37 testes
específicos da criação. Scripts JavaScript e módulos novos passaram pela
verificação de sintaxe; `git diff --check` não apontou erros.

## Entrega implementada

- Aba `/app/create` e APIs autenticadas `/api/creation`.
- Confirmação do nicho, memória ativa por canal e usuário, favoritas e amostras de voz.
- Cinco etapas com releitura dos prompts e Markdown válidos, inclusive em retries
  HTTP internos dos provedores, e snapshots de instruções/hashes/revisões por envio.
- Pesquisa Google/YouTube e fontes públicas, com catálogo de procedência e fontes
  descobertas intercaladas com as sementes do usuário. Pesquisa parcial exige
  decisão explícita vinculada ao hash da versão documental.
- Síntese OpenAI Speech/ElevenLabs, reutilização de falas por parâmetros imutáveis,
  pausas, duração medida e direção global combinada com a direção de cada fala.
- Timeline validada, recortes, foco, timecodes, movimentos, fades, textos, legendas,
  trilha com redução sob a voz e efeitos. Piloto 540×960 e final 1080×1920 a 30 fps.
- Revisão Gemini de partes reais de áudio e vídeo, com cobertura registrada e uma
  tentativa automática de reparo do piloto. Falhas persistentes ficam visíveis.
- Feedback persistido antes da fila, retomada da operação após reinício, perguntas
  específicas e aplicação de mudanças visuais claras independentes das ambiguidades.
- Aprovação vinculada à versão/hash do piloto, documentos imutáveis, arquivos privados,
  envio de mídia pelo usuário e pacote ZIP com o projeto e materiais.

## Evidência de verificação

Antes de alterar `studio/`, `python tests/run.py` terminou com `All tests passed.`

Os testes específicos incluem fluxo integral offline com serviços simulados e
arquivos reais processados pelo FFmpeg, isolamento local/contas, decisões, retomada,
metadados confiáveis, redirecionamentos, segredos, bloqueios temporários do Windows,
documentos, voice cache, narração, mixagem, exportação e aprovação obsoleta.

No navegador, usando servidor descartável e sem chaves externas, foram conferidos:
tela responsiva de criação, cadastro de tema, canal, confirmação de nicho, persistência
do projeto e retomada após reinício até o estado correto de configuração pendente.
A interface registrou ausência de erros JavaScript nessa conferência. Sintaxe dos
scripts e dos módulos novos também foi verificada.

O transporte público com DNS fixado também foi exercitado em uma leitura HTTPS real
de `https://example.com/`, que retornou `text/html` e 577 bytes. Essa verificação não
usou credenciais nem serviços pagos.

A primeira suíte após a implementação apontou uma expectativa antiga sobre as chaves
de `/api/sync`. O contrato foi atualizado para incluir os projetos de criação e
acrescentado teste de que essa lista continua isolada por usuário.

## Revisão independente e correções

A revisão independente encontrou os casos abaixo. Foram reproduzidos quando
aplicável e corrigidos com verificações direcionadas:

| Achado | Correção e evidência |
|---|---|
| Refazer sobrescrevia a primeira revisão documental | Alocação por versões existentes e criação exclusiva; teste preserva os bytes de r0001 após invalidar |
| Reinício em roteiro pulava autorização de pesquisa parcial | Validação no coordenador e na API; decisão vinculada a hash e contagem; testes de bloqueio e autorização |
| YouTube escapava do transporte público validado | URLs canônicas, extratores restritos, único handler com DNS fixado, limites de destinos/tempo/bytes e testes de rejeição de redirecionadores |
| Retomada regenerava fala já válida | Fingerprint e contexto da revisão com campos imutáveis; teste falha na segunda fala e preserva a primeira |
| Feedback enfileirado se perdia após reinício | Operação/payload/versão/fase persistidos antes do agendamento; teste retoma como feedback |
| Resposta de IA podia sobrescrever metadados de fonte | Lista permitida de campos editoriais; teste injeta URL, autoria e hash falsos e preserva os reais |
| Sementes impediam consultar resultados independentes | Intercalação e deduplicação antes do limite; bloqueio quando só há fontes fornecidas |
| Upload selecionado pela IA era tratado como URL para baixar | Resolução local após qualquer seleção; teste proíbe download de upload:// |
| Schema perdia timecode e foco | Campos start/focus_x/focus_y no roteiro e na revisão, validados antes do render |
| Estilo global da voz não chegava à entrega | Configuração sem chave no planejamento e composição de direções na síntese; teste cobre ambas |
| Salvar opções copiava segredos do ambiente | Persistência mescla apenas JSON salvo; fallbacks só na leitura operacional; teste mantém segredo externo fora do arquivo |
| Preferências excluídas viajavam pelo histórico | Snapshot de memória ativa, sem history; teste cria projeto após exclusão |
| Retry interno reutilizava prompt antigo | Callback por envio HTTP; teste altera o arquivo entre HTTP 400 e nova tentativa |
| Quota impedia cancelar | Cancelamento antes da quota; teste API simula armazenamento cheio |
| Documentação dizia aguardar aprovação | Situação e mapa dos módulos atualizados |

A revisão não executou serviços pagos nem julgou a qualidade editorial ou a voz de
contas reais. Isso permanece como limite de validação, não como aprovação presumida.

## Desempenho e decisão sobre Rust

Medição local com arquivos sintéticos, na mesma máquina da implementação:

| Operação | Trabalho | Tempo observado |
|---|---|---|
| Ler regras/prompt e compor contexto | 100 execuções, documento de cerca de 10 mil caracteres | 0,0658 s no total |
| Renderizar piloto | 2 s, 540×960, zoom, fade, texto, legenda e áudio | 1,437 s; CPU da thread Python 0,062 s |
| Renderizar final | 2 s, 1080×1920, mesmos elementos | 2,314 s; CPU da thread Python 0,016 s |

As saídas foram verificadas com FFprobe e decodificação integral. Esses números são
amostras, não previsão de duração para vídeos longos ou provedores externos. A CPU
da thread Python não inclui o processo FFmpeg.

Ruling: manter o backend em Python e o processamento audiovisual no FFmpeg nativo,
sem acrescentar Rust neste momento — não foi identificado gargalo de processamento
Python que justifique a migração. O custo de estar errado é desempenho menor em
cargas ainda não medidas; os tempos por etapa ficam registrados para orientar uma
função dedicada em Rust quando houver evidência.

Outra otimização evita recalcular o hash do vídeo inteiro para cada parte da revisão:
o hash é calculado uma vez por execução de análise audiovisual.

## Limites e operação

- Não foram usadas chaves reais nem executadas cobranças de pesquisa, síntese ou
  revisão. Os protocolos foram verificados com respostas simuladas e documentação
  oficial. Uma produção real exige serviços configurados e pode encontrar restrições
  ou mudanças nas contas/provedores.
- O adaptador OpenAI usa Speech, cujos modelos legados têm encerramento anunciado
  para 6/1/2027. A tela e o guia registram essa condição; Realtime não é tratado como
  se fosse compatível com `/v1/audio/speech`.
- A análise de modelo não garante encontrar todo defeito de imagem ou pronúncia;
  ela se soma à revisão humana obrigatória do piloto.
- Páginas sem texto legível e fontes inacessíveis não contam como consultadas.
  Consultas parciais têm cobertura registrada, sem inventar o conteúdo não lido.
- Mudanças foram mantidas na pasta solicitada; não foi movida a instalação nem
  reiniciado um servidor de produção do usuário. O servidor de UI usado era descartável.

## Ajuste das configurações de narração

Após os comentários na interface, a chave específica de narração passou para
Configurações → IA, na seção Narração do vídeo, com provedor, modelo e voz.
Criar vídeo aponta para essa seção e reutiliza a conexão de texto existente,
por assinatura ou API. Salvar narração envia somente as opções de voz; deixar
a chave vazia preserva a chave salva. Contas comuns acessam suas próprias opções
de narração sem receber acesso à configuração administrativa da IA de texto.

A suíte completa `python tests/run.py` terminou novamente com `All tests passed.`
O teste direcionado `python tests/test_creation_api.py` passou nos três casos,
incluindo o formulário JavaScript, preservação da chave e envio separado da voz.
`node --check web/creation.js` e `git diff --check` não apontaram erros.

No navegador, o formulário foi salvo sem chave real e recarregado para verificar
a persistência de uma voz temporária; esse valor foi depois removido. Também foram
conferidos o campo de senha, os links entre as telas e a ausência do aviso duplicado
de configuração na criação. O servidor de prévia foi mantido ativo na porta 8766,
com dados descartáveis. Nenhum serviço pago foi acionado.

Este último ajuste recebeu revisão local do código. A nova tentativa de revisão
independente não foi concluída porque o revisor atingiu o limite de uso; a revisão
independente descrita acima se refere à implementação anterior a este ajuste.
