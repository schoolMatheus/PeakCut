# Distância entre motion e legenda conforme a referência

O usuário forneceu uma imagem de 540 × 960 com o cartão aproximadamente entre x=113–433 e y=736–798. O tamanho estava correto; a correção solicitada foi aproximar o motion da legenda.

O motion agora acompanha a legenda. Com Clean, seu quadro transparente começa 32 pixels abaixo do ponto de referência das palavras; o cartão de inscrição tem mais 58 pixels de margem interna. Com a legenda em y=1380 no vídeo completo, o cartão aparece perto de y=735 na prévia em metade do tamanho. Não fica mais fixo ao rodapé. Legendas excepcionalmente baixas usam no máximo y=1608 com Clean, y=1584 com Bounce e y=1560 com Hype para manter toda a animação dentro do quadro, com 40 pixels de margem inferior.

Largura, artes, velocidade de 1,5× e ganho dos sons de 60% foram preservados. Após a validação, o usuário solicitou o commit e o push desta correção na branch `codex/interacao-publico`.

Verificação específica: 14 testes de motion passaram, incluindo o posicionamento de referência, todos os layouts, a legenda efetivamente enviada ao renderizador e o áudio exportado. A nova prévia foi renderizada e medida: cartão branco entre x=111–429 e y=736–796, praticamente igual à imagem fornecida.

A revisão identificou que o comentário poderia tocar uma cedilha no estilo Hype durante a saída. Clean manteve o offset de 32 pixels da referência; Bounce passou a usar 56 e Hype 80, preservando margem para letras maiores e acentuadas. Um teste de regressão cobre esse limite.

Prévia: `outputs/motion-youtube-reference.mp4`; quadro: `outputs/motion-youtube-reference.jpg`. O teste de carga de CPU falhou de forma transitória na suíte inicial antes da alteração (75% medidos, abaixo do limiar do teste); passou na execução seguinte. A suíte final passou nos 42 grupos após a correção de Hype. A revisão confirmou 25 pixels livres entre a cedilha e o cartão durante a saída.
