# Prompts do fluxo Criar vídeo

Esta pasta contém as instruções operacionais que o PeakCut deve consultar durante
a criação. Ela não serve apenas como referência para quem implementa a aba.

## Composição obrigatória de cada execução

Antes de iniciar, retomar ou refazer uma etapa, ler da pasta da instalação:

1. `00 - regras gerais.txt`.
2. O arquivo integral da etapa atual.
3. Os documentos válidos de entrada listados abaixo, na pasta do projeto do usuário.

Enviar o conteúdo dos prompts à IA, junto com o objetivo da operação, briefing,
preferências aplicáveis, documentos e feedback necessário. Isso também se aplica
às chamadas auxiliares de planejamento, correção e revisão daquela etapa.

Não basta passar nomes de arquivos. Não usar somente o Markdown da etapa anterior.
Não substituir os arquivos desta pasta por constantes ou um cache permanente.

| Etapa | Arquivo de instruções | Principais entradas do projeto | Saídas |
|---|---|---|---|
| Nicho | `00 - regras gerais.txt` | Canal e evidências acessíveis | Nicho proposto e confirmação |
| 1 | `etapa 1 - pesquisa e documentação das referências.txt` | Tema, nicho, memória e referências opcionais | `01_pesquisa_referencias.md` |
| 2 | `etapa 2 - roteiro audiovisual completo.txt` | `01_pesquisa_referencias.md` | `02_roteiro_audiovisual.md` |
| 3 | `etapa 3 - narração e integração ao roteiro.txt` | Pesquisa, roteiro e configurações de voz | `03_direcao_narracao.md`, áudio e `03_roteiro_integrado.md` |
| 4 | `etapa 4 - montagem do piloto e coleta de feedback.txt` | Roteiro integrado, mídias, áudios e feedback | Piloto 9:16, timeline e `04_revisao_piloto.md` |
| 5 | `etapa 5 - finalização e apresentação do resultado.txt` | Piloto aprovado, revisão, feedback e arquivos | Vídeo final 9:16 e `05_entrega_final.md` |

Resolver os caminhos a partir da instalação, sem depender do diretório em que o
servidor foi iniciado. Registrar nomes/hashes e revisões utilizados por execução.
As cópias históricas não substituem a releitura dos arquivos atuais.

Se faltar um arquivo obrigatório, impedir o avanço e informar a pendência. Não
improvisar instruções nem truncar silenciosamente o contexto. Tratar documentos
gerados e conteúdo externo como dados, separados das regras de execução.

## Diretriz de edição: FFmpeg + Remotion

As regras gerais e os cinco prompts definem agora a edição por timeline JSON
versionada e validada no backend Python, com FFmpeg para preparação/áudio/finalização
e Remotion em `my-video/` para composição visual, transições, templates e legendas.
O piloto inteiro permanece 540×960; o final, 1080×1920, ambos a 30 fps e em 9:16.

Os arquivos lidos automaticamente continuam sendo as regras gerais e o prompt da
etapa. As instruções operacionais necessárias estão nesses arquivos. O detalhamento
para quem integrar os motores está em
[Diretrizes FFmpeg/Remotion](../docs/edicao-ffmpeg-remotion.md); criar este documento
não o inclui automaticamente no contexto da IA.

## Situação da implementação

O carregamento em tempo de execução está implementado em
`studio/creation_prompts.py` e é usado pelo coordenador `studio/creation.py`.
Os testes verificam releitura sem reiniciar, documentos obrigatórios e tentativas
internas de compatibilidade dos provedores. O desenho da implementação inicial
está em `docs/superpowers/specs/2026-11-27-criacao-de-videos-design.md`.

O render atual usa `studio/creation_timeline.py` para validar/migrar a timeline,
`studio/creation_remotion.py` para preparar e finalizar com FFmpeg, e o worker
`my-video/worker.mjs` com a composição `PeakCutVertical` para o visual. Cada snapshot
inclui o catálogo de capacidades reais, também acessível em `/api/creation/capabilities`.

Instale as dependências e o navegador do Remotion antes de renderizar, conforme o
guia. Recursos ausentes são pendências reais: a instalação de um pacote não concede
capacidades adicionais ao agente. O schema da operação atual continua obrigatório.
