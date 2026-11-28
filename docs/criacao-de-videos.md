# Criar vídeo

A aba transforma um tema em vídeo vertical 9:16 usando pesquisa pública, documentos
versionados, narração por API e montagem local com FFmpeg e Remotion. Ela não publica no canal.

As diretrizes de edição foram atualizadas para **FFmpeg + Remotion**, com timeline
JSON validada com Pydantic no Python e composição visual `PeakCutVertical` no
projeto `my-video/`. FFmpeg prepara as mídias, mixa o áudio e finaliza o MP4.
O contrato, a divisão de tarefas e os critérios de integração estão em
[Diretrizes de edição FFmpeg/Remotion](edicao-ffmpeg-remotion.md).

## Configuração

Na instalação, execute `npm ci` e `npm exec remotion browser ensure` dentro de
`my-video/`, usando uma versão de Node compatível com seu `package.json`. O navegador
de renderização é instalado nessa preparação; um render não tenta baixá-lo.
O backend Python precisa das dependências de `requirements.txt` e de FFmpeg/FFprobe.
Não é necessário manter o Remotion Studio aberto.

1. Configure a IA de texto nas configurações gerais do PeakCut.
2. Em **Configurações → IA → Narração do vídeo**, configure a chave específica de
   ElevenLabs ou OpenAI, o modelo e a voz. Isso não altera a assinatura ou API
   selecionada para a IA de texto.
3. Abra **Criar vídeo → Pesquisa, voz e revisão** para configurar SerpAPI, consultar
   vozes, ouvir amostras e ajustar a interpretação.
4. Nessa seção da criação, configure uma chave e um modelo Gemini com capacidade
   de analisar áudio e vídeo.
5. Escolha o canal. O sistema propõe um nicho quando há evidências; você confirma
   ou informa o correto. Um canal inacessível não é tratado como vazio.

As chaves de cada usuário ficam em sua área de dados, fora do repositório, e não
entram nos prompts ou documentos. Em modo local, as variáveis documentadas em
`.env.example` também podem fornecer chaves. Ao trocar a empresa que fornece a
voz, configure a chave correspondente; a chave anterior não é reaproveitada.

As integrações seguem as documentações de [SerpAPI](https://serpapi.com/search-api),
[ElevenLabs](https://elevenlabs.io/docs/api-reference/text-to-speech/convert),
[OpenAI Speech](https://developers.openai.com/api/reference/python/resources/audio/subresources/speech/methods/create)
e [Gemini para vídeo](https://ai.google.dev/gemini-api/docs/video-understanding).
Serviços externos podem cobrar pelo uso. A OpenAI anunciou encerramento dos modelos
legados de Speech em 6 de janeiro de 2027; o adaptador usa `/v1/audio/speech` e exige
um modelo compatível com esse endpoint, não um modelo Realtime.

Direção textual e parâmetros de interpretação dependem do provedor. O adaptador
ElevenLabs não converte automaticamente instruções livres em SSML ou tags faladas.
O documento de narração registra as capacidades aplicadas.

## Instruções e documentos

Antes de cada operação, o backend relê `00 - regras gerais.txt` e o prompt da etapa
na pasta `criacao_de_videos/`, resolvida a partir da instalação. Também lê as revisões
válidas dos Markdown necessários. Os nomes dos arquivos estão no README dessa pasta.

Se um arquivo obrigatório estiver ausente, vazio ou alterado fora do versionamento,
o fluxo para com uma explicação. Não há substituição por um prompt genérico nem
corte silencioso do contexto. Os arquivos em `runs/` registram instruções, hashes e
revisões usados. Alterar um prompt afeta a próxima operação sem reiniciar o servidor.

Os documentos do projeto são dados de entrada, separados das instruções. O roteiro
usa apenas a pesquisa documentada como base factual. Referências do usuário não
limitam a pesquisa; fontes inacessíveis ou sem conteúdo suficiente não são contadas.
Páginas extensas são identificadas como consulta parcial, com cobertura registrada.

## Produção e revisão

O fluxo pesquisa, cria roteiro, produz áudio por fala, mede os tempos e monta o piloto
completo em 540×960. A exportação final usa 1080×1920 a 30 fps. O catálogo de edição
inclui recorte/composição, Ken Burns, cortes, fades, crossfade, slide, títulos,
lower thirds, chamadas para ação, comparações, legendas, trilha e efeitos. Legendas
por palavra exigem timestamps reais; o fluxo de voz usa legendas por segmento
quando o provedor não fornece alinhamento. Operações são dados validados, não
comandos livres nem código React gerado por projeto.

A timeline canônica v2 fica no projeto junto aos manifestos do render: mídias,
hashes, templates, versões dos motores, áudio medido e verificações. O backend
converte timelines antigas ao refazer a montagem, preservando os arquivos anteriores.
Um piloto antigo precisa ser refeito com o novo motor antes de aprovar a exportação.
Mudar mídias ou código dos templates/mixagem depois da aprovação exige novo piloto.

As fases de preparação, composição, áudio e verificação atualizam o progresso.
Interromper cancela também os processos de renderização. Erros de timeline/render
podem gerar uma correção automática; falhas persistentes e dependências ausentes
ficam pendentes, sem trocar o motor silenciosamente.

O usuário decide se a pesquisa pode avançar com menos de 20 referências. Na revisão,
envie comentários com trechos e resultados desejados. Comentários ambíguos geram
perguntas. A aprovação se aplica à versão apresentada; refazer invalida a aprovação.

É possível adicionar imagens, vídeos, trilhas e efeitos ao projeto antes de retomar
a montagem. Quando uma mídia pública não puder ser obtida, o sistema tenta alternativas
ou informa a pendência. Ele registra a procedência e as condições de uso encontradas.
Finalidade educativa não é tratada como autorização automática de reutilização.

A revisão automática envia partes reais de áudio/vídeo ao modelo configurado e
registra os intervalos cobertos. Isso se soma à verificação técnica do arquivo e à
revisão humana do piloto, sem prometer que o modelo detecta todos os problemas.
Sem capacidade ou configuração de revisão, a etapa fica pendente.

Interromper preserva o trabalho. Após reiniciar o servidor, retome explicitamente;
arquivos válidos de narração são reutilizados pelo conteúdo e pelas configurações.
Documentos e renders anteriores permanecem no projeto.

## Desempenho e testes

O projeto registra tempos e consumo de CPU por operação. Funções dedicadas em Rust
podem substituir gargalos locais comprovados, mantendo os contratos. A codificação
já é executada pelo FFmpeg; atrasos de rede ou de API não justificam migrar Python.

`python tests/run.py` inclui os testes do fluxo. Eles usam serviços simulados e
mídias locais, sem chaves pagas. Isso não comprova disponibilidade ou qualidade de
uma conta externa específica; uma produção real requer os serviços configurados.
