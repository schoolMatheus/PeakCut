# Interação com o público — verificação

Implementado na branch `codex/interacao-publico`. As cinco chamadas são opcionais, podem ser combinadas e aparecem uma por vez com movimento curto e fade. O renderizador reserva uma faixa distante das legendas e do título.

## Onde configurar

- Opções de criação de clipes, ao lado de estilo, layout e qualidade.
- Configuração do criador, como padrão para seus vídeos e lives.
- Editor de cada clipe, antes de refazê-lo.

As escolhas são guardadas nos rascunhos, nos modelos salvos e nos clipes. Modelos antigos limpam a seleção; rascunhos antigos não apagam os padrões do criador. Desligar as legendas não desliga as chamadas.

## Evidências

- `python tests/test_interactions.py`: sete testes passaram. Abrange validação, duplicatas, persistência, modelos, criação de lives, substituição das opções, clipes antigos, legendas desligadas, duração curta e posições em todos os layouts.
- `python tests/run.py`: execução final passou nos 41 grupos, incluindo permissões e isolamento dos modelos entre usuários.
- Sintaxe dos scripts de `web/index.html` e `web/pt-br.js` verificada com Node; `git diff --check` sem erros.
- Navegador em uma instância temporária sem dados reais: cinco opções desmarcadas inicialmente; seleção múltipla; modelo salvo e aplicado; modelo antigo limpando as opções; preferência do criador persistindo; rascunho de quantidade permitindo herança do criador; escolha explícita preservada ao recarregar.
- Vídeo sintético de 18 segundos renderizado por ffmpeg com as cinco chamadas e legendas Hype. Quadros de entrada, permanência, saída e chamada seguinte inspecionados: texto legível e separado das legendas.
- Revisão independente encontrou duas questões, corrigidas: configuração dos padrões de lives e registro separado da escolha explícita em rascunhos.

## Ambiente de testes

A execução inicial dentro do sandbox não acessou o servidor temporário. As execuções completas seguintes usaram localhost fora do sandbox, com pastas vazias. Uma execução inicial apresentou falha na detecção do aplicativo Codex instalado no Windows; a execução final passou nesse grupo, sem alterar o código de detecção. A instância de interface usada para verificação foi encerrada.

O usuário solicitou o commit e o push da implementação na branch `codex/interacao-publico` após a validação.
