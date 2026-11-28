# Integração FFmpeg + Remotion — verificação local

## Implementação

- Timeline v2.0 Pydantic em studio/creation_timeline.py, com conversão explícita de cenas antigas.
- Worker Node e composição PeakCutVertical em my-video, com Zod na fronteira.
- Backend prepara proxies privados com FFmpeg, serve somente assets do job em loopback,
  compõe no Remotion e faz uma única mixagem/mux FFmpeg H.264/AAC a 30 fps.
- Narração preserva relógio por amostras; transições estendem somente handles visuais.
- Piloto 540×960 e final 1080×1920 usam o mesmo contrato aprovado.
- Hashes vinculam timeline, bytes de mídia, templates/fontes, compilador de áudio e perfil.
- Progresso, cancelamento da árvore de processos e correção limitada de erros foram integrados.
- Cada snapshot continua relendo prompts atuais e documentos válidos, com capacidades reais.

## Revisão independente

O revisor examinou modelos Python/Node, fluxo de aprovação, rede/arquivos privados,
transições, fonte de tempos e cancelamento. Encontrou três problemas:

1. Validação anterior ao worker não propagava cancelamento às inspeções FFprobe.
2. Python conferia somente sobreposições adjacentes, enquanto Node conferia todos os pares.
3. Python não exigia que o alvo de ducking fosse uma faixa de áudio.

Os três foram reproduzidos em testes que falharam antes das correções e passaram
após elas. A inspeção agora usa um único probe por asset, com cancelamento; o
contrato Python rejeita sobreposições aninhadas e alvos de ducking incompatíveis.
Um teste também entrega o dump canônico Python diretamente ao schema Node.

O revisor não repetiu renders nem a suíte completa; essas verificações são do
implementador. Não encontrou escape de rede/arquivos privados no escopo examinado.

## Evidências de integração

A suíte completa anterior à mudança terminou com All tests passed.
Os testes direcionados exercitaram render real pelo Chromium/Remotion e FFmpeg:

- Imagem, vídeo com timecode de origem, transição sobreposta, movimento, texto,
  legenda por segmento, voz, música e efeitos.
- Piloto reduzido e final 1080×1920, com mesmo hash editorial e diferença média
  de pixels após redução dentro do limite do teste.
- MP4 H.264 yuv420p BT.709, áudio AAC 48 kHz, duração e decodificação integral.
- Presença do fim da narração após a transição, início por amostras, ducking
  medido por frequência e looping restrito ao trecho de origem.
- Manifestos, legendas, medições de loudness, isolamento de assets, cancelamento,
  timeout, protocolo de progresso e remoção de segredos do ambiente do worker.

O teste revelou caminhos longos do Windows no cache de bundle: o cache de código
foi movido para um caminho temporário curto, separado por projeto e hash. Assets
continuam privados no projeto. A correção de cor BT.709 mantém yuv420p de saída;
a compensação da latência do limiter evita deslocar a voz em cinco milissegundos.

A preparação instalou o Chromium local exigido pelo Remotion. Renders não fazem
download de dependências. O endpoint /api/creation/capabilities foi conferido no
servidor de prévia 8766 com dados descartáveis e retornou renderer ffmpeg_remotion,
schemaVersion 2.0 e aspect 9:16.

## Limites

As APIs de pesquisa, texto, síntese e revisão audiovisual foram simuladas nos
ensaios de fluxo; não foram usadas credenciais reais ou serviços pagos. A edição
local foi efetivamente executada. Estes testes não medem qualidade editorial de
conteúdo real nem garantem recursos não publicados no catálogo de capacidades.
As legendas do fluxo atual usam segmentos medidos; karaokê exige timestamps reais
por palavra e não é simulado por distribuição de caracteres.

Não houve migração de instalação, alteração de dados de produção, commit ou push.
As composições de exemplo do usuário e as alterações existentes foram preservadas.

## Resultado final dos comandos

- `python tests/run.py`: exit 0, `All tests passed.`, incluindo 65 testes específicos de criação/integração.
- `npm test` em my-video: 7 testes passaram.
- `npm run lint` em my-video: ESLint e TypeScript passaram.
- `npm run build` em my-video: bundle gerado com sucesso.
- `git diff --check`: sem erros de whitespace (somente aviso de normalização CRLF preexistente em tests/run.py).
