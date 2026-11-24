# Motions YouTube — resultado da verificação

Implementação na branch `codex/interacao-publico`.

## Resultado

Os cinco pedidos usam artes próprias em vermelho, branco e preto: botão de inscrição e confirmação, polegar ativado, comentário digitado e enviado, seta de compartilhamento e marcador de salvar. Cursor, pressão e resposta visual estão sincronizados. Os arquivos Lottie são editáveis; as versões WebM transparentes são compostas pelo ffmpeg sem exigir Node na instalação do PeakCut.

Todos usam o woosh fornecido na entrada. Clique e teclado usam os outros dois MP3s fornecidos. O ganho de cada efeito é `0.6`, sem normalização que o aumente; o limitador não aplica ganho automático. Os pequenos silêncios de início são recortados para o som coincidir com o gesto. Em motions comprimidos, o áudio acompanha a velocidade da arte. As caudas terminam dentro do intervalo da chamada. O áudio original mantém sua normalização e censura anteriores e recebe silêncio de preenchimento quando termina antes da imagem.

As chamadas continuam opcionais e usam a seleção salva nos vídeos, modelos e criadores. Legendas e título ocupam faixas separadas da arte. Sem pedidos selecionados, o caminho anterior de áudio e vídeo é preservado.

## Validação

- `python tests/run.py`: os 42 grupos passaram na execução final.
- `python tests/test_motion.py`: dez testes passaram, incluindo pixels de transparência, arte nas cores pedidas, ganho RMS próximo de 60%, eventos dentro do clipe, efeitos isolados em um clipe de três segundos com cinco ações, montagem de trechos e áudio original mais curto que a imagem.
- Testes anteriores de opções, persistência e criadores preservados; a verificação específica de posição e tempo passou a usar o novo compositor.
- Dez JSONs com estrutura válida e dez vídeos transparentes gerados com Skottie. Textos e estados conferidos no player oficial e em quadros decodificados. Verificação independente confirmou correspondência entre cenas e gerador.
- Os três MP3s distribuídos são idênticos aos fornecidos, conferidos por SHA-256.
- Revisão independente encontrou dois problemas de áudio. Ambos foram reproduzidos em testes e corrigidos. Após a correção, a revisão mediu woosh/clique a cerca de 5 ms e teclado a cerca de 8 ms dos pontos programados no caso curto; também confirmou efeitos tardios com áudio original curto.
- `node --check tools/build_interaction_motion.mjs` e `git diff --check`: sem erros.

## Prévia e reprodução

`python tools/preview_interaction_motion.py` gera `outputs/motion-youtube-preview.mp4` e um quadro JPG, usando apenas imagem sintética e uma pasta temporária de dados. A prévia contém as cinco animações, som e legendas. `outputs/` está ignorada no Git.

Os recursos distribuídos ficam em `studio/assets/interactions/`, com instruções de reconstrução no README dessa pasta. O Docker já copia `studio/`, incluindo esses recursos. Não foi executado um build Docker/Linux nesta sessão; os testes de exportação usaram o ffmpeg instalado no Windows.

O player de criação temporário foi encerrado. Os sons originais e outros arquivos fornecidos pelo usuário foram preservados. Após a validação, o usuário solicitou o commit desta revisão, sem novo push.
