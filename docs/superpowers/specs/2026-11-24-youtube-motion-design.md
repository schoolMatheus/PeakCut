# Motions de interação no estilo YouTube

## Brief do usuário

Melhorar a animação existente usando a referência `https://github.com/diffusionstudio/lottie.git`, com uma direção de arte própria para cada pedido, clique e digitação onde fizerem sentido. Usar vermelho, branco e preto do YouTube. Usar os três efeitos fornecidos em `docs/efeitos-sonoros`; todos os motions devem tocar `ribhavagrawal-woosh-230554` ao entrar. Ganho inicial dos efeitos: 60% da amplitude original, sem normalizar para cima. Preservar as legendas.

## Direção de arte e sequência

- Inscrição: símbolo de play e botão vermelho; cursor se aproxima, clica, e o botão passa a inscrito com confirmação.
- Like: polegar vetorial, clique com pequena reação e estado ativado em vermelho.
- Comentário: balão e campo de comentário; cursor entra, uma mensagem curta é digitada e o envio é confirmado.
- Compartilhamento: seta vetorial e clique que aciona uma saída direcional curta, seguida de confirmação.
- Salvar: marcador vetorial recebe clique, preenche em vermelho e confirma a ação.

Cada motion tem entrada, ação, breve leitura e saída. Mostrar um por vez, uma vez por clipe, em uma faixa separada das legendas e do título. Ajustar a velocidade em clipes curtos sem deixar sons ou imagens ultrapassarem os limites da chamada. Textos em português brasileiro e inglês, conforme o idioma do clipe.

## Construção

Escolha de implementação: criar cenas Lottie editáveis e verificá-las no player Skottie do projeto indicado. Pré-renderizar os desenhos em vídeos transparentes para o PeakCut compor com ffmpeg. Isso permite arte vetorial mais rica sem exigir Node ou o player no computador que gera os clipes. A alternativa de continuar só com desenhos ASS limita o desenho de ícones, cursor e transições; instalar um player em cada renderização acrescentaria dependências e custo desnecessários.

Copiar os sons fornecidos para os recursos distribuídos com `studio/`, mantendo os arquivos originais. Sincronizar clique ao pressionar e teclado apenas durante a digitação. Aplicar ganho linear `0.6` em cada efeito e limitar picos da mistura para evitar distorção. Manter a voz original, a censura e a montagem de trechos. Não modificar a paleta da interface do aplicativo.

## Critérios de conclusão

- Cinco motions visivelmente diferentes, com fundo transparente e animação coerente.
- Woosh em todas as entradas; clique e teclado sincronizados, todos a 60%.
- Nenhuma interação selecionada: renderização e áudio anteriores preservados.
- Funcionar com legendas desligadas, com trechos editados e no pacote Docker.
- Testes antes e depois, verificação do player oficial e amostra final com imagem e áudio conferidos.

O brief acima foi definido pelo usuário. Os detalhes visuais e a pré-renderização são decisões de implementação desta revisão.
