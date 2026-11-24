# Motion abaixo das legendas e a 1,5×

Correção solicitada pelo usuário na branch `codex/interacao-publico`.

- O motion completo fica no rodapé, entre y=1640 e y=1880 no quadro de 1080 × 1920, com margem inferior de 40 pixels. Não existe mais a alternativa de posicioná-lo acima das legendas.
- Quando a legenda original está baixa demais, somente os clipes com motion reservado ajustam seu ponto de referência até y=1516. Isso deixa espaço para o crescimento das palavras e um intervalo antes da arte. Clipes sem interação mantêm a posição anterior.
- A duração anterior de cada motion é dividida por 1,5. A entrada mantém o instante programado; imagem, clique, woosh e teclado usam a mesma razão de tempo. O ganho dos efeitos continua em 60%.
- `python tests/test_motion.py`: 12 testes passaram, incluindo posição em todos os layouts, reserva real das legendas, velocidade, sons comprimidos, ganho e transparência.
- `python tests/run.py`: os 42 grupos passaram antes e depois da alteração. `git diff --check` sem erros.
- Revisão independente aprovada, sem questões pendentes. A prévia com legendas foi renderizada e o quadro conferido.

Prévia atualizada: `outputs/motion-youtube-preview-1_5x.mp4`. O script `tools/preview_interaction_motion.py` também gera a versão atual em `outputs/motion-youtube-preview.mp4`.

As mudanças valem nas próximas renderizações; clipes já exportados precisam ser refeitos. Esta correção foi deixada sem novo commit ou push.
