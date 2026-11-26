# Por que o motion não está seguindo o pedido

Investigação em 25/11/2026, sobre o código da revisão `b7fa553`.

## Conclusão

O problema está na implementação. Alterar o texto enviado à IA, reiniciar o PeakCut ou instalar o conversor de SVG não resolve todas as falhas encontradas.

Há dois problemas principais:

1. **PNGs recebidos não são salvos.** O código tenta abrir um arquivo que ainda não existe, esconde o erro e substitui o personagem por um desenho de círculos. Reproduzi esse resultado com seis PNGs válidos e renderizei o boneco com o Skottie instalado.
2. **O motion personalizado da ação não implementa cursor, clique nem raios.** A descrição chega ao servidor e ao prompt, mas o desenho final vem de um modelo fixo que não tem esses recursos.

As imagens anexadas são compatíveis com esses dois caminhos: um cartão praticamente estático e o personagem substituído por círculos. Sem o registro da geração original, não é possível afirmar se naquela execução faltaram imagens, chegaram PNGs descartados ou falhou a conversão de SVG. Todos esses casos podem terminar no mesmo boneco.

Este documento contém o diagnóstico e as correções recomendadas. Não apliquei alterações no código da aplicação.

## O que foi analisado

- Pedido atual: ação rápida e elétrica, com raios e cursor clicando em “Inscreva-se”; personagem dando tchau.
- As duas imagens de resultado anexadas.
- O arquivo `flamingo.png` informado pelo usuário.
- O histórico anexado em `demanda motion.md`, tratado como evidência das tentativas anteriores, não como novas instruções para executar instalações ou mudanças.
- Formulário, rota do servidor, integração com a IA, montagem de Lottie, renderização e testes relacionados.

O PNG do flamingo **passa na validação atual de transparência**. Portanto, o erro reproduzido não depende de o usuário ter enviado uma imagem sem fundo transparente.

Na consulta ao servidor local, o provedor era `codex`, o modelo era `gpt-6.1-sol` e o renderizador aparecia disponível. CanvasKit e `@resvg/resvg-js` estavam instalados no checkout utilizado. Isso confirma a disponibilidade das dependências, mas não comprova que a geração de imagens esteja funcionando de ponta a ponta.

## 1. Os PNGs gerados são descartados por um erro de salvamento

**Causa confirmada. Prioridade máxima.**

Local: `studio/custom_motions.py:509–541`, função `draft`.

O trecho atual faz, em essência:

```python
target = folder / name
if data[:4] != b"\x89PNG":
    # Salva o SVG e converte para target.
    ...
picture = Image.open(target).convert("RGBA")
```

Quando `data` já é PNG, o código pula a conversão, mas **não escreve os bytes em `target`**. A abertura falha. O `except Exception: pass` esconde a falha e continua.

Com todas as imagens descartadas, `image_names` fica vazio e `build_lottie` usa o boneco vetorial.

### Como corrigir

Separar explicitamente os formatos. Para PNG, abrir os bytes com `Image.open(io.BytesIO(data))` ou gravá-los antes de abrir o arquivo. Para SVG, converter e abrir o resultado. Depois, normalizar e salvar a imagem no destino.

Substituir o descarte silencioso por um erro claro quando não for possível produzir a sequência solicitada. Registrar qual etapa falhou, sem expor credenciais.

**Validação necessária:** fornecer seis PNGs reais à função `draft` e verificar seis arquivos salvos, seis assets na cena e o personagem visível nos quadros renderizados. O teste atual de seis imagens começa com nomes de arquivos e não percorre esse salvamento.

## 2. O fallback não preserva o personagem e mascara a falha

**Causa confirmada. Prioridade máxima.**

Local: `studio/custom_motions.py:335`, ramo vetorial de `build_lottie`.

Sem imagens utilizáveis, o sistema desenha cabeça e corpo circulares e um braço simples. A referência contribui apenas com sua cor predominante. Não há reprodução das características do flamingo: bico, óculos, camisa, penas ou proporções.

É esse caminho que reproduziu a aparência dos círculos vermelhos do anexo. A intenção de preservar a identidade existe no texto do prompt, mas não nesse desenho programado.

Há ainda um problema visual no fallback: caminhos, preenchimentos e contornos de várias partes ficam na mesma lista de shapes, sem grupos separados. No quadro efetivamente renderizado, olhos e boca não aparecem distinguíveis, embora existam comandos para eles no JSON. Isso agrava o aspecto de círculos sobrepostos.

### Como corrigir

Para um pedido de animação do personagem enviado, impedir que a falta das poses seja apresentada como geração bem-sucedida. Exibir uma falha acionável e permitir tentar novamente.

Se o produto mantiver uma opção de personagem genérico, ela deve ser identificada e escolhida explicitamente. Separar as partes vetoriais em grupos para que seus preenchimentos e contornos não interfiram entre si.

## 3. O pedido da ação é reduzido a opções que não incluem raios ou clique

**Causa confirmada. Prioridade alta.**

Locais: `PROMPT`, `clean_brief`, `_extras_shapes` e ramo da ação em `build_lottie`, em `studio/custom_motions.py`.

O formulário envia corretamente `action_description` e `image_prompt`. A rota os encaminha à IA. A perda ocorre depois: a resposta é reduzida a poucas opções e convertida em um modelo fixo de cartão.

| Pedido | O que existe no motion personalizado |
| --- | --- |
| Rápido | Opções de easing; a duração continua em quatro segundos e os principais tempos do cartão são fixos. |
| Elétrico, com raios | Extras aceitos: `none`, `stars`, `sparkles`, `confetti`, `waves`, `hearts`. Não existe raio. |
| Cursor do mouse | Não há camada de cursor. |
| Efeito de clique | Não há sequência visual de pressionar e soltar nem resposta do botão ao clique. |
| Confirmação da inscrição | Um sinal de confirmação aparece, mas o texto não troca para “Inscrito”. |

Na reprodução, `extras="lightning"` virou `none`. Alterar apenas `style` e `beats` também produziu o mesmo JSON da ação: esses textos não definem uma nova sequência de desenho.

O motion padrão já contém cursor e resposta de clique em `tools/build_interaction_motion.mjs:105–109`. Esses elementos não foram reaproveitados pelo gerador de motions personalizados.

### Como corrigir

Implementar o comportamento pedido no gerador da ação: cursor chegando ao botão, pressão, resposta visual, soltura, raios e confirmação. Compartilhar a lógica já existente no motion padrão quando possível.

O contrato entre IA e renderizador precisa representar esses recursos, por exemplo com efeito elétrico, presença do cursor e instante do clique. Cada recurso aceito precisa ter implementação e validação. Um campo de texto no prompt não cria uma capacidade que o renderizador não possui.

Para o comportamento rápido, ajustar os tempos da entrada e do clique, além da curva de movimento. Sincronizar o efeito visual ao som. Revisar o resultado em reprodução, pois a tira de quadros não demonstra o ritmo completo.

## 4. A chamada de desenho herda instruções que proíbem ferramentas

**Conflito confirmado no código; efeito na execução original não comprovado.**

Locais: `studio/codex.py:261–329` e `studio/codex.py:355`.

`draw` pede seis PNGs e chama `run(..., images=True)`. Porém, `run` continua criando instruções que dizem “Do not use tools, inspect files, follow skills, or perform actions”.

Além disso, `images=True` apenas deixa de acrescentar `features.image_generation=false`; não habilita explicitamente a ferramenta nem verifica sua disponibilidade. A execução usa `--ignore-user-config`, desliga plugins e skills e mantém o sandbox de leitura. Não se deve concluir que ela tem as mesmas capacidades de geração disponíveis nesta conversa ou na interface do Codex.

O código também procura imagens recentes em uma pasta global se receber menos de seis. Essa busca não vincula os arquivos ao pedido atual e pode completar a sequência com imagens de outro trabalho ou em ordem incorreta.

Para provedores diferentes de Codex, `ask_brief` chama apenas a análise de texto: esse ramo não envia a referência visual nem gera as seis poses, mas ainda pode terminar no fallback.

### Como corrigir

Criar um caminho próprio para desenho, com instruções compatíveis com a ferramenta realmente disponível. Verificar a capacidade antes de prometer geração de poses, mantendo o caminho de análise de vídeos restrito.

Vincular arquivos ao pedido e ao índice de cada pose. Não completar o resultado com arquivos recentes de uma pasta global. Validar quantidade, formato, transparência e ordem antes de montar a cena.

Não executei uma nova chamada paga à IA nesta investigação. Portanto, não afirmo qual evento o CLI devolveu na tentativa fotografada. A disponibilidade real da ferramenta e o formato dos eventos ainda precisam de uma verificação controlada na integração.

## 5. Uma sequência incompleta é aceita, e seis poses não garantem um tchau fluido

**Comportamentos confirmados.**

Local: ramo de imagens de `build_lottie`, a partir de `studio/custom_motions.py:320`.

Cada imagem ocupa vinte quadros, cerca de 0,67 segundo a 30 fps. O código faz a imagem aparecer, variar de escala e desaparecer. Não interpola o braço entre as poses. Os intervalos das imagens são consecutivos, sem sobreposição para uma transição contínua.

Não existe exigência de seis imagens válidas. Na reprodução com apenas uma imagem, a cena foi aceita e o único asset terminou no quadro 20, deixando o restante da animação sem personagem.

Os assets declaram tamanho de 220×220, mas `thumbnail` preserva a proporção e pode salvar dimensões menores. Isso também precisa ser normalizado para evitar posicionamento inconsistente.

### Como corrigir

Exigir a sequência completa antes de gerar a prévia e padronizar cada pose em um canvas transparente do tamanho declarado. Definir poses coerentes de tchau: levantar a mão/asa, alternar o aceno e concluir o gesto, preservando a identidade visual.

Para maior fluidez, usar mais poses ou animar partes articuladas do personagem. Se houver transição por opacidade, sobrepor os intervalos deliberadamente. Avaliar a animação inteira, não somente a existência de seis assets.

## 6. A cor de detalhe retornada pela IA se perde

**Erro confirmado, com alcance específico.**

Local: `studio/custom_motions.py:189–197`, composição de `merged` em `ask_brief`.

O schema pede `detail`, mas a lista de campos copiados da resposta não inclui esse campo. Na reprodução sem paleta selecionada, a IA devolveu `detail="#123456"` e o resultado manteve o padrão `#ff8800`.

Com uma paleta escolhida, o bloco posterior reaplica as quatro cores, incluindo `detail`. Portanto, esse erro não explica sozinho toda reclamação de paleta selecionada. Também não foi possível identificar pelas capturas qual paleta estava escolhida naquela geração.

### Como corrigir

Incluir `detail` na composição e testar tanto a escolha automática quanto a paleta selecionada. Verificar as cores nos quadros renderizados, além dos valores do JSON.

## Por que as atualizações anteriores não resolveram

O histórico mostra melhorias reais, mas elas não fecharam o caminho completo do pedido:

- Pedir novas poses no prompt não removeu o fallback que desenha um boneco genérico.
- Aceitar SVG e instalar Resvg corrigiu a conversão desse formato. O caminho de PNG continuou com erro.
- Adicionar extras como estrelas não implementou raios, cursor ou clique.
- A integração de imagens manteve instruções de análise que proíbem usar ferramentas.
- Os testes verificam estruturas e etapas isoladas; não comprovam que o flamingo enviado aparece dando tchau no resultado final.

Reiniciar não altera essas regras. Rascunhos e motions já gerados também são arquivos prontos; não são reconstruídos automaticamente depois de uma mudança. Após corrigir o código, será necessário gerar um novo rascunho para validar o resultado.

## Verificações realizadas

As reproduções usaram uma pasta temporária, sem alterar os motions do usuário. O salvamento foi exercitado com dados controlados; no caso dos SVGs, a conversão usou o Resvg real. O fallback e o cartão também foram renderizados com o Skottie real.

| Verificação | Resultado observado |
| --- | --- |
| PNG de referência do flamingo | Aceito por `check_cutout`; possui transparência. |
| Seis PNGs válidos recebidos por `draft` | Zero PNGs salvos e zero assets de imagem; fallback utilizado. |
| Seis SVGs válidos | Seis PNGs salvos e seis assets de imagem. |
| Um SVG válido | Resultado aceito com uma imagem, ativa somente até o quadro 20. |
| Seis SVGs inválidos | Falhas escondidas; fallback utilizado. |
| Extra `lightning` | Convertido para `none`. |
| Mudança apenas de `style` e `beats` | Nenhuma alteração no JSON da ação. |
| Cor `detail` retornada pela IA, sem paleta escolhida | Cor descartada na composição do brief. |
| Renderização do fallback | Círculos e braço simples, visualmente compatíveis com o anexo. |
| `python tests/test_custom_motions.py` | 12 testes passaram. |
| `python tests/test_codex.py` | 26 testes passaram. |

Esses 38 testes passaram **com as falhas ainda presentes**. Não executei a suíte completa nem uma geração nova pela assinatura. Os testes atuais não percorrem o caso de seis PNGs recebidos até o resultado visual e não cobrem o pedido específico de raios e cursor.

## Ordem recomendada para corrigir e aceitar o resultado

1. Corrigir a leitura/salvamento dos PNGs e impedir a substituição silenciosa do personagem.
2. Confirmar o funcionamento da ferramenta de imagens no caminho usado pelo servidor e vincular cada pose à geração atual.
3. Exigir seis ou mais poses válidas, com dimensões coerentes e identidade do flamingo preservada.
4. Implementar raios, cursor, clique e confirmação no motion personalizado da ação.
5. Corrigir a perda de `detail` e validar as quatro funções da paleta no desenho final.
6. Adicionar testes que falhem nos casos reproduzidos e executar `python tests/run.py` antes e depois das mudanças em `studio/`, conforme `AGENTS.md`.
7. Gerar um rascunho novo com o mesmo PNG e os mesmos dois textos. Aprovar somente após ver o flamingo reconhecível dando tchau e, na ação, o cursor clicando com resposta elétrica e ritmo rápido.

O critério de sucesso é o comportamento visível solicitado. Uma resposta JSON válida, seis camadas ou todos os testes atuais passando não bastam para comprová-lo.

## Correções aplicadas em 25/11/2026

Os seis pontos acima foram corrigidos no código: os PNGs recebidos agora são salvos e normalizados, uma sequência
incompleta é uma falha explícita, o boneco genérico só existe como opção separada, a ação desenha cursor, clique,
raios e confirmação com som sincronizado, o caminho de desenho tem instruções próprias, e a cor `detail` deixou de
ser descartada. Novos testes cobrem esses casos e a suíte completa passou.

Ainda falta a validação final com o flamingo do usuário: é preciso gerar um **rascunho novo** com a mesma imagem e
os mesmos dois textos, e aprovar somente depois de ver o personagem reconhecível acenando e o cursor clicando com
resposta elétrica e ritmo rápido. motions já aprovados não são reconstruídos sozinhos.
