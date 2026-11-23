# Interação com o público

## Objetivo

Adicionar às opções de criação de clipes uma seção opcional de interação com o público. O usuário escolheu texto animado sobre o vídeo, sem atrapalhar as legendas. O trabalho fica na branch `codex/interacao-publico`.

## Abordagens

- Pedidos na descrição: simples, mas não aparecem enquanto a pessoa assiste.
- Texto animado sobre o clipe: escolhido pelo usuário. Reaproveitar o renderizador de texto existente permite manter os arquivos independentes de serviços externos.

## Comportamento proposto

- Cinco caixas de seleção independentes: inscrição no canal, like, comentário, compartilhamento e salvar o vídeo. Todas desmarcadas inicialmente; permitir selecionar várias.
- Mostrar uma chamada por vez, uma vez por clipe, distribuída ao longo da duração. Entrada e saída suaves com movimento curto e fade; aproximadamente três segundos por chamada, ajustados em clipes curtos.
- Usar frases curtas e prontas, no idioma do texto do clipe, sem voz ou alteração do áudio.
- Colocar a chamada em uma faixa separada das legendas, com margem que considere seu tamanho e animação. Preferir abaixo; quando não couber na área segura do quadro, usar acima. Não mover nem cobrir as legendas ou o título.
- Funcionar também com legendas desativadas, em todos os layouts e nas qualidades disponíveis.
- Salvar as escolhas com as opções do vídeo e nos modelos personalizados. Modelos e clipes antigos continuam sem chamadas. Manter a seleção ao renderizar novamente um clipe.
- Reutilizar a aparência dos controles existentes, a paleta e o renderizador ASS/ffmpeg. Não adicionar dependências, editor de animação ou configuração de tempo nesta primeira versão.

Desenho aprovado pelo usuário na conversa em 23/11/2026. Incluir também as mesmas escolhas no editor do clipe, para permitir ajustá-las ao renderizar novamente.

## Validação

Executar `python tests/run.py` antes e depois de alterações em `studio/`. Verificar seleção e persistência, rejeição de opções inválidas, chamadas isoladas e combinadas, legendas ligadas/desligadas e clipes antigos. Renderizar uma amostra e conferir quadros da animação para verificar legibilidade e separação das legendas.
