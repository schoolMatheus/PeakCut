# Diretrizes de edição com FFmpeg e Remotion

Esta atualização define a arquitetura solicitada para a criação de vídeos do
PeakCut. Os prompts operacionais estão em `criacao_de_videos/`: regras gerais e
cinco etapas. Este documento detalha o contrato da integração local.

## Situação real e objetivo

O fluxo usa uma timeline canônica v2 validada com Pydantic em
`studio/creation_timeline.py`. `studio/creation_render.py` mantém a interface do
coordenador e delega a execução para `studio/creation_remotion.py`.

O projeto `my-video/` existe, com Remotion 4.0.547, React, TypeScript e Zod
declarados em `package.json`. As composições de exemplo 1920×1080 foram preservadas.
A composição dedicada `PeakCutVertical` executa a timeline 9:16; o worker Node
recebe apenas dados validados e arquivos preparados pelo backend.

O novo renderizador está integrado ao piloto e à exportação final. A migração do
formato antigo `scenes` ocorre ao refazer a montagem, sem apagar os renders anteriores.
As capacidades reais são incluídas em cada chamada da IA, junto aos prompts atuais.

O destino definido pelo usuário é uma camada de edição programática local:

```text
Prompts atuais + documentos válidos + capacidades disponíveis
                         ↓
                   Agente de edição
                         ↓
              Timeline JSON versionada
                         ↓
           Python valida e planeja a execução
                         ↓
        FFmpeg prepara mídias e proxies locais
                         ↓
      Remotion compõe o vídeo sem áudio final
                         ↓
     FFmpeg mixa áudio e finaliza o piloto MP4
                         ↓
     Verificação técnica + análise audiovisual
                         ↓
      Correção limitada → revisão do usuário
                         ↓
       Mesma edição → exportação 1080×1920
```

O agente escolhe entre operações e templates implementados. O backend compila
os dados em operações dos motores. O agente não gera código executável por vídeo.

## Responsabilidades dos motores

| Camada | Responsabilidade |
|---|---|
| Agente | Descrever cenas, cortes, camadas, falas, trilhas, efeitos, legendas e transições em dados; corrigir os parâmetros a partir de evidências |
| Python | Pydantic, compatibilidade de versões, assets privados, compilação do plano, fila, progresso, cancelamento, cache, documentos e aprovações |
| FFmpeg/FFprobe | Inspeção, extração de trechos, normalização de mídia, proxies, áudio, mixagem, ducking, loudness, mux e verificação do arquivo |
| Remotion em `my-video/` | Composição visual determinística: cenas, enquadramentos, Ken Burns, transições, camadas, textos, gráficos e legendas animadas |

O padrão proposto é uma composição visual completa no Remotion, com arquivos
preparados pelo FFmpeg e áudio final montado uma vez pelo FFmpeg. Isso evita
depender de intermediários transparentes para a integração inicial. Se no futuro
um modo apenas FFmpeg ou um modo de overlays for oferecido, terá capacidades e
plano explícitos, preservando a semântica da timeline. Não será um fallback
silencioso para uma edição aprovada em outro modo.

Não é necessário acrescentar MoviePy ou um serviço de render em nuvem. A escolha
da conexão de texto, por API ou assinatura, continua independente dos motores.
O backend continua centralizando as chamadas de IA existentes.

## Contrato da timeline

O schema é versionado e validado com Pydantic no backend. Deve rejeitar
campos desconhecidos e valores inválidos, além de validar relações entre campos.
O contrato entregue à IA deve corresponder às capacidades realmente integradas.
O worker Node valida novamente sua entrada; tipos TypeScript não substituem a
validação em tempo de execução. Zod pode validar a fronteira do worker, mantendo
paridade com o schema canônico e verificações compartilhadas de exemplos.

O contrato deve cobrir:

- `schemaVersion`, identificação da revisão e formato de referência.
- `format`: 1080×1920, 30 fps, duração total em frames, fundo e áreas seguras.
- Manifesto de assets por ID: tipo, origem documentada, hash, metadados medidos e
  localização relativa privada resolvida pelo backend. O manifesto público da IA
  não contém caminhos absolutos ou segredos.
- Faixas de vídeo/imagem, texto/gráficos, legendas e áudio, com IDs e camadas.
- Clipes: ID da cena, ID da mídia, início e duração na timeline, entrada/saída no
  arquivo de origem, ajuste de enquadramento, foco e parâmetros de movimento.
- Transições: clipes participantes, tipo disponível, duração e intervalo exato.
- Templates e estilos: IDs, versões e parâmetros permitidos, sem CSS/HTML livre,
  expressões JavaScript ou imports escolhidos pelo modelo.
- Áudio: papel de voz, música, som original ou SFX; início, trecho de origem,
  duração, ganho, fades e regras de ducking.
- Legendas: palavras ou segmentos com timestamps, origem do alinhamento, texto e
  estilo registrado. Legendas por palavra exigem alinhamento real.
- Vínculos com os IDs REF, MID, CENA e FALA dos documentos de produção.

Use intervalos `[startFrame, startFrame + durationInFrames)`. A origem da mídia
usa `sourceInSeconds`/`sourceOutSeconds`, medidos no arquivo, sem confundir esses
campos com início/fim na timeline. O backend quantiza os tempos de composição uma
vez e verifica o erro acumulado. O áudio mantém precisão por amostras/segundos;
não é truncado para caber na quantização de vídeo.

As transições sobrepõem clipes. Por exemplo, um clipe de 180 frames e outro de
129 frames com sobreposição de 9 resultam em 300 frames, não 309. Com tempos
absolutos, o segundo começa no frame 171. Os dois adaptadores recebem esses tempos;
não podem descontar a sobreposição novamente. No Remotion, ao usar uma sequência
que calcula sobreposições, o compilador deve traduzir esses tempos sem duplicá-las.

O schema deve validar existência e tipo dos assets, limites finitos, IDs únicos,
referências, duração de origem suficiente, cortes válidos, cobertura visual,
camadas, limites de texto/recursos e transições menores que os clipes envolvidos.
Sobreposições são permitidas onde declaradas; lacunas visuais precisam de intenção
explícita. Rejeitar operações que o catálogo da instalação não implementa.

Erros devem informar código, campo/JSON pointer, cena/clipe e motivo. O agente
corrige dados; não recebe autorização para executar código para contornar erros.

### Exemplo do contrato

Este JSON demonstra tempos e divisão de responsabilidades. Os IDs de templates
estão implementados; os arquivos devem existir e passar pela inspeção do backend.
Os hashes e metadados medidos ficam no manifesto separado do render.

```json
{
  "schemaVersion": "2.0",
  "format": {
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "durationInFrames": 300,
    "safeAreaProfile": "shorts-default-v1"
  },
  "assets": [
    {"id": "MID-001", "kind": "video", "file": "assets/source.mp4"},
    {"id": "MID-002", "kind": "image", "file": "assets/detail.png"},
    {"id": "VOZ-001", "kind": "audio", "file": "audio/narration.wav"},
    {"id": "MUS-001", "kind": "audio", "file": "assets/music.wav"},
    {"id": "SFX-001", "kind": "audio", "file": "assets/whoosh.wav"}
  ],
  "tracks": [
    {
      "id": "visual",
      "type": "video",
      "layer": 0,
      "clips": [
        {
          "id": "CLIP-001",
          "sceneId": "CENA-001",
          "assetId": "MID-001",
          "startFrame": 0,
          "durationInFrames": 180,
          "sourceInSeconds": 12,
          "sourceOutSeconds": 18,
          "fit": "cover",
          "focus": {"x": 0.5, "y": 0.4}
        },
        {
          "id": "CLIP-002",
          "sceneId": "CENA-002",
          "assetId": "MID-002",
          "startFrame": 171,
          "durationInFrames": 129,
          "fit": "contain",
          "motion": {"id": "ken_burns_v1", "scaleFrom": 1, "scaleTo": 1.08}
        }
      ]
    },
    {
      "id": "titles",
      "type": "text",
      "layer": 1,
      "clips": [
        {
          "id": "TITLE-001",
          "sceneId": "CENA-001",
          "startFrame": 15,
          "durationInFrames": 60,
          "templateId": "title_pop_v1",
          "text": "O detalhe que muda tudo",
          "position": {"x": 0.5, "y": 0.2}
        }
      ]
    },
    {
      "id": "voice",
      "type": "audio",
      "role": "narration",
      "clips": [
        {"id": "FALA-001", "assetId": "VOZ-001", "startFrame": 0, "durationInFrames": 300, "gainDb": 0}
      ]
    },
    {
      "id": "music",
      "type": "audio",
      "role": "music",
      "duckUnder": "voice",
      "clips": [
        {"id": "MUSIC-001", "assetId": "MUS-001", "startFrame": 0, "durationInFrames": 300, "gainDb": -18}
      ]
    },
    {
      "id": "effects",
      "type": "audio",
      "role": "sfx",
      "clips": [
        {"id": "SOUND-001", "assetId": "SFX-001", "startFrame": 168, "durationInFrames": 15, "gainDb": -12}
      ]
    }
  ],
  "transitions": [
    {"id": "TR-001", "fromClipId": "CLIP-001", "toClipId": "CLIP-002", "type": "crossfade", "startFrame": 171, "durationInFrames": 9}
  ]
}
```

O exemplo omite legendas e metadados de assets por brevidade. O schema completo
define todos os campos, defaults e limites; não deve aceitar objetos livres
para acomodar parâmetros ainda não implementados.

Para áudio, `audioStartSeconds` e `audioDurationSeconds` preservam os tempos medidos
com precisão de amostras; os frames continuam sendo a referência visual. Esses
campos são opcionais e exclusivos das faixas de áudio. A conversão de projetos
antigos estende apenas os handles visuais de uma transição, mantendo a duração
e a posição exatas da narração.

## Integração Python ↔ Node/Remotion

Resolver `my-video/` a partir da raiz da instalação. Não gravar o caminho pessoal
da máquina em código nem depender do diretório de início do servidor. Preservar as
composições de exemplo e acrescentar uma composição dedicada, por exemplo
`PeakCutVertical`, com duração calculada da timeline e canvas 1080×1920 a 30 fps.

O backend chama um worker Node mantido no projeto, com argumentos separados e
sem shell. O worker recebe um manifesto validado e devolve progresso, erros e
artefatos por um protocolo estruturado. O Node não recebe chaves de IA, banco ou
credenciais do servidor. Cancelamento deve interromper worker, Chromium e FFmpeg,
liberar recursos e manter somente artefatos concluídos e válidos.

A API oficial de renderização permite criar/reutilizar o bundle, selecionar a
composição e renderizar usando as mesmas `inputProps`. O bundle pode ser
reaproveitado quando código, dependências e fontes não mudaram.
([Exemplo oficial de SSR](https://www.remotion.dev/docs/ssr-node))

Na chamada pelas APIs Node, passar explicitamente as opções de render/bundler:
`remotion.config.ts` se aplica à CLI e não configura essas APIs automaticamente.
O projeto atual seleciona Rspack na configuração; a integração deverá alinhar essa
escolha no worker ou documentar outra escolha testada.
([Configuração oficial](https://www.remotion.dev/docs/config))

Verificar as versões locais antes de escolher APIs; os pacotes `remotion` e
`@remotion/*` usados na integração devem permanecer compatíveis. Declarar
dependências usadas diretamente, sem depender apenas de pacotes transitivos.
Instalar e registrar componentes de transição, legendas, fontes e animações
necessários antes de publicá-los no catálogo de capacidades.

O render só consome mídias preparadas, fontes empacotadas e dados locais. O
backend disponibiliza assets privados ao Chromium por um mecanismo restrito ao
job; não copiar dados de vários usuários para um `public/` compartilhado. Não
permitir destinos arbitrários de rede, caminhos fora do projeto ou conteúdo
HTML/SVG ativo não validado. Essas regras preservam o isolamento já existente.

Animações são calculadas a partir do frame e dos parâmetros; qualquer ruído usa
semente estável. Evitar relógio real, temporizadores e animações CSS temporais.
Fonts, assets e versões entram no manifesto de render para manter a reprodução.

## Áudio, legendas e perfis

O áudio original de cada mídia fica mudo por padrão. Só é incorporado quando
solicitado em uma faixa explícita. Música e SFX têm arquivos, trechos, ganhos e
fades próprios. O FFmpeg faz ducking a partir da voz e normaliza a mixagem uma vez;
os targets de loudness/true peak e a taxa de amostragem são parâmetros do perfil,
registrados e medidos, não promessas fixas de conformidade com toda plataforma.
([Filtros de áudio FFmpeg](https://ffmpeg.org/ffmpeg-filters.html))

Se houver prévia interativa com som, ela deve usar o mix de referência ou reproduzir
o mesmo plano de áudio com precisão verificada. Não misturar esse áudio novamente
na exportação. O MP4 do piloto é a referência de revisão.

Timestamps reais de palavras/segmentos vêm do TTS, alinhamento ou transcrição
disponível. Legendas animadas usam templates registrados; a ausência de alinhamento
por palavra exige estilo por segmento ou uma pendência explícita. A biblioteca de
templates pode oferecer título, chamada para ação, comparação, lower third, legenda
por segmento e karaokê, cada um com versão e capacidades verificadas.

| Perfil | Resolução | fps | Conteúdo |
|---|---|---|---|
| Piloto | 540×960 | 30 | Vídeo inteiro, edição e áudio completos |
| Final | 1080×1920 | 30 | Mesma timeline e composição aprovadas |

Coordenadas, margens, fontes e efeitos são definidos no canvas de referência e
escalados proporcionalmente. Não trocar tempos, templates ou fontes entre perfis.
Exportar MP4 H.264/AAC com formato de pixels de distribuição compatível. O perfil
precisa registrar codec, qualidade, parâmetros de áudio e versões utilizadas.

## Feedback e critérios de conclusão

Cada render tem evidências associadas à revisão/hash da timeline, hashes dos
assets, versões dos templates, prompts e documentos utilizados. Antes de cada
chamada, inclusive correções, reler regras gerais e prompt da etapa e carregar
os documentos válidos. O cache de render não dispensa essa leitura.

O loop deve combinar validação, FFprobe, decodificação completa, frames nas
fronteiras de cenas/transições e análise de trechos reais de áudio/vídeo. Frames
isolados não provam pronúncia, sincronização do áudio ou fluidez. Uma imagem
escura detectada automaticamente também pode ser intencional: cruzar a medida
com a timeline antes de classificá-la como falha.

Quando houver erro, devolver ao agente medidas e mensagens estruturadas. A
correção altera dados permitidos, salva nova revisão e invalida a aprovação
anterior quando afetar o resultado. O coordenador limita tentativas automáticas
e apresenta falhas persistentes. Após a revisão técnica, apresentar o piloto
inteiro e aguardar feedback humano; perguntas específicas resolvem queixas vagas.

Aprovação deve vincular a edição, assets e templates apresentados. O final pode
trocar somente o perfil de resolução/qualidade. Precisa de nova verificação real
após exportar, e de nova revisão humana se houver alteração editorial.

## Implementação e manutenção

Os módulos implementam o percurso abaixo. A instalação requer `npm ci` e
`npm exec remotion browser ensure` em `my-video/`; a falta de dependências fica
visível como configuração pendente. O Studio é opcional para o desenvolvedor.

1. Definir modelos Pydantic, schema versionado e catálogo de capacidades. Preservar
   a leitura de projetos atuais; uma migração explícita não apaga renders anteriores.
   Evitar colisão de nomes: no render atual, `scene.start` é tempo de origem;
   `timeline_start` tem outro significado. Converter explicitamente.
2. Criar o adaptador Python/Node e a composição vertical em `my-video/`, começando
   com cortes, imagens, narração e legendas por segmento.
3. Integrar Ken Burns, sobreposições/transições, camadas, templates de texto e
   legenda, SFX, áudio original explícito, ducking e loudness.
4. Conectar piloto, progresso/cancelamento e loop limitado de autocorreção à
   revisão existente, com evidências, hashes e perguntas sobre feedback ambíguo.
5. Exportar o final a partir da mesma edição aprovada e empacotar documentos,
   timeline, manifesto e créditos sem credenciais.

Verificar pelo menos: compatibilidade de timeline antiga, IDs/assets inválidos,
cancelamento, limites de recursos, isolamento de contas, sobreposição sem perda
de narração, alinhamento de legendas, mixagem sem duplicação e equivalência visual
entre piloto/final. Usar mídias locais e um render real curto FFmpeg/Remotion;
respostas simuladas de IA não substituem esse teste do motor. Antes/depois de
alterar `studio/`, seguir a suíte exigida por `AGENTS.md`.

Medir preparação, composição visual, mixagem e validação separadamente. Reutilizar
proxies e bundle por hashes, limitar concorrência e evitar recodificações inúteis.
Rust fica reservado a gargalos locais medidos; não resolve espera por API ou rede.

As condições de distribuição/uso do Remotion devem ser conferidas nos
[termos oficiais](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md)
para o contexto do PeakCut. Isso não altera as regras de procedência das mídias.
