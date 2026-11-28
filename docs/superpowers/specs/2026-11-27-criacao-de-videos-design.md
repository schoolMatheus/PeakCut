# Criação de vídeos no PeakCut

Status: aprovado pelo usuário em 27/11/2026; implementado e verificado localmente.

## Objetivo e requisitos recebidos

Criar a aba **Criar vídeo**, que transforma um tema em um vídeo vertical completo,
com pesquisa, roteiro, narração por API, edição de mídias reais, piloto, revisão e
exportação final. O usuário pode informar referências, mas a pesquisa deve ir além
delas. Os cinco prompts em `criacao_de_videos/` são instruções de execução do
produto, além dos documentos gerados por projeto. Não são apenas documentação
para o assistente que implementa a funcionalidade.

O formato é sempre 9:16. Confirmar o nicho, permitir a decisão quando houver menos
de 20 referências e obter feedback do piloto são requisitos do usuário. Não há
geração de vídeo por IA, clonagem de voz presumida ou publicação automática.

O usuário também autorizou substituir partes lentas por funções dedicadas em Rust
no backend. A escolha deve partir de medições; não implica reescrever a aplicação.

## Evidências no repositório

- `studio/config.py` já define saída de 1080 × 1920 a 30 fps.
- `studio/ai.py` centraliza os provedores de IA, respostas JSON e leitura de imagem.
  Não oferece hoje um ciclo de ferramentas de pesquisa ou síntese de voz.
- As chamadas de análise em `studio/codex.py` e `studio/claude_cli.py` desabilitam
  ferramentas. Pedir pesquisa apenas por texto não fornece acesso real à web.
- `studio/profiles.py` busca perfil público e informações de transmissão; não há
  análise editorial suficiente para confirmar o nicho do canal.
- `studio/media.py` já integra yt-dlp, extração de áudio e inspeção de arquivos.
  Seu caminho atual de URLs aceita sites de vídeo conhecidos. A pesquisa ampla
  exige um caminho adicional para páginas e mídias públicas, com validação de rede.
- `studio/render.py` já tem FFmpeg, Pillow, composições verticais, legendas,
  interações e diretório temporário curto para compatibilidade com Windows.
  Falta uma montagem de timeline com múltiplas mídias e narração sintetizada.
- `studio/jobs.py` oferece fila justa, contexto por usuário e persistência de jobs.
- `studio/creators.py` mantém canais e suas preferências por usuário.
- `web/index.html` é a aplicação sem build; alterações devem seguir os estilos
  existentes, sem sombras e com hover que muda somente cor.
- A pasta recebida contém cinco `.txt`, ainda sem regras gerais salvas. A etapa 1
  contém a frase sobre reutilização educativa como ordem. A conversa estabelece
  que ela deve ser apenas um contraexemplo explicitamente incorreto.

## Abordagens e escolha proposta

1. **Fluxo controlado pelo PeakCut, com conectores de pesquisa e voz e renderização
   local.** Reaproveita autenticação, dados, IA e FFmpeg. Permite validar fontes,
   documentos e aprovações antes de avançar. É a abordagem recomendada.
2. **Delegar o projeto inteiro a uma sessão autônoma de CLI.** Exigiria alterar as
   restrições atuais dos adaptadores e dar ferramentas amplas a uma execução longa.
   A continuidade, as provas da pesquisa e o isolamento dependeriam dessa sessão.

A implementação adota a primeira abordagem, aprovada com este documento. Entregar
somente roteiro não atende ao resultado solicitado.

## Contrato obrigatório de leitura dos prompts

Adicionar `criacao_de_videos/00 - regras gerais.txt` com o escopo recebido e as
regras comuns, preservando os nomes dos cinco arquivos existentes.

Um módulo de carregamento resolve a pasta pela raiz da instalação, nunca pelo
diretório atual do processo e nunca por um caminho pessoal fixo. O mapa de arquivos
é explícito e versionado; não procurar nomes arbitrários enviados pelo navegador.

Antes de cada execução, retomada, regeneração ou chamada auxiliar de uma etapa:

1. Ler do disco as regras gerais e o prompt integral da etapa atual, em UTF-8.
2. Validar presença, conteúdo não vazio e identificação correta dos arquivos.
3. Carregar o briefing e as preferências aplicáveis, separadamente dos documentos.
4. Carregar as revisões dos documentos de entrada exigidos para a etapa.
5. Compor o pedido de IA com regras gerais + instruções da etapa + contrato da
   operação atual + contexto e documentos, claramente delimitados.
6. Salvar, por execução, nome, hash SHA-256 e cópia do conteúdo dos prompts e a
   identificação/hash das entradas, sem credenciais. Essas cópias servem para
   auditoria, não substituem a leitura da pasta na próxima execução.
7. Validar a resposta estruturada, gerar os Markdown e executar apenas operações
   suportadas e validadas pelo código.

Não basta mencionar os nomes dos arquivos no texto enviado ao modelo. O conteúdo
precisa estar efetivamente incluído. Não guardar os prompts apenas em constantes
Python nem carregar uma única vez no início do servidor.

Uma execução usa um conjunto coerente de instruções. Mudanças no disco passam a
valer na próxima execução. Se uma etapa posterior usar documento feito com regras
anteriores, registrar essa origem e marcar incompatibilidades reais para refação.
Não reescrever documentos já aprovados silenciosamente.

Se faltar prompt, documento ou espaço no contexto do modelo, a etapa fica pendente
com motivo e solução visíveis. Não usar um prompt genérico ou cortar silenciosamente
o material. Dividir operações por cena ou fonte, mantendo as instruções integrais
e o conjunto necessário de evidências em cada pedido. Para montar o roteiro completo,
usar uma operação final de consistência com a pesquisa documental aplicável.

Documentos e páginas externas são dados não confiáveis. Não podem substituir as
regras da etapa, conceder autorização, escolher comandos ou revelar credenciais.
Preservar quebras de linha e links nos documentos sem aplicar o sanitizador de
texto curto de clipes ao Markdown inteiro. Reaproveitar as proteções existentes
com um tratamento específico para documentos extensos.

### Entradas por etapa

| Operação | Instruções relidas da pasta | Documentos/contexto necessários | Saída principal |
|---|---|---|---|
| Identificar nicho | Regras gerais | Canal, evidências públicas e preferências | Proposta de nicho e evidências |
| 1. Pesquisar | Regras gerais + etapa 1 | Briefing, nicho confirmado, referências opcionais, pesquisas anteriores | `01_pesquisa_referencias.md` |
| 2. Roteirizar | Regras gerais + etapa 2 | Revisão válida de `01_pesquisa_referencias.md` | `02_roteiro_audiovisual.md` |
| 3. Narrar | Regras gerais + etapa 3 | Pesquisa, roteiro, voz e preferências registradas | `03_direcao_narracao.md`, áudios e `03_roteiro_integrado.md` |
| 4. Montar/revisar | Regras gerais + etapa 4 | Roteiro integrado, mídias, áudio, timeline e feedback anterior | Piloto, timeline e `04_revisao_piloto.md` |
| 5. Finalizar | Regras gerais + etapa 5 | Revisão aprovada, feedback e arquivos de montagem | Vídeo final e `05_entrega_final.md` |

A pesquisa é a fonte factual exclusiva do roteiro. Instruções, briefing e memória
orientam a forma; não acrescentam fatos ao tema. Informação nova exige retornar à
pesquisa e atualizar as dependências antes de usá-la.

## Interface e experiência

Adicionar entrada **Criar vídeo** à navegação existente e uma rota própria da
aplicação. A tela permite selecionar o canal e consultar projetos anteriores.

O caminho principal é: escolher canal → confirmar nicho quando necessário →
informar tema e referências opcionais → acompanhar as etapas → revisar o piloto
vertical → baixar a entrega. A duração desejada faz parte do briefing; não herdar
automaticamente o limite de duração de um clipe extraído de transmissão.

Exibir progresso real: fontes consultadas, documento disponível, narração gerada,
montagem em curso ou resposta necessária. Não simular porcentagens ou declarar
execução quando só existe planejamento. Expor erros em linguagem simples e permitir
retomar a operação sem apagar o trabalho.

O projeto oferece acesso aos documentos, suas revisões, fontes, mídia de revisão,
feedback e download final. A memória do canal tem edição, exclusão de preferências,
vozes favoritas e distinção entre preferências confirmadas e sugestões.

Reaproveitar componentes, tradução e navegação do HTML existente. Especificação
visual detalhada e ajuste responsivo ficam com o implementador, respeitando o
AGENTS.md. Não é necessário um chatbot genérico para conduzir todo o processo.

## Nicho e memória

Examinar descrição e uma amostra de títulos, descrições e materiais acessíveis do
canal. Guardar as evidências e os limites da consulta. Canal inacessível, canal vazio
e evidências insuficientes são situações diferentes.

Uma proposta de nicho não vale como confirmação. O usuário confirma ou substitui o
nicho. Confirmar uma vez por canal, com possibilidade de revisão posterior.

Persistir memória por usuário e canal. Cada preferência guarda valor, origem, data
e situação (declarada/confirmada/inferida). Guardar os aspectos apreciados nas
referências, não apenas URLs. Uma correção de projeto só entra na memória permanente
quando o usuário indicar esse alcance. A instrução atual prevalece sobre a memória.

Cada projeto conserva o retrato das preferências utilizadas. Alterar a memória não
deve modificar retroativamente um vídeo aprovado.

## Persistência e estados

Proposta técnica: guardar memória e projetos em uma área `creation/` dentro da
pasta do canal pertencente ao usuário. Usar arquivos JSON para estado estruturado,
Markdown para consulta e subpastas para fontes, áudios, mídias, versões e renders.
O layout preciso pode ser ajustado, mantendo o isolamento do usuário.

Estados distinguem execução, espera de configuração, espera de confirmação do nicho,
espera de decisão sobre referências, espera de feedback, necessidade de esclarecimento,
falha recuperável, cancelamento e conclusão. Guardar etapa, operação, revisão,
entradas utilizadas e histórico de decisões. Escritas são atômicas e protegidas
contra duas execuções simultâneas do mesmo projeto.

Na inicialização, execuções interrompidas tornam-se retomáveis; não repetir chamadas
pagas automaticamente sem verificar os arquivos e recibos já persistidos. Invalidar
somente as saídas dependentes das mudanças. Cada aprovação pertence a uma versão
específica do piloto; alterações posteriores invalidam a aprovação dessa entrega.

Usar `ctx.uid()` e `ctx.as_user()`/`ctx.bind()` para execução em segundo plano,
`ctx.bump()` quando o estado visível mudar e fila justa para trabalhos longos. Novos
artefatos entram no cálculo de armazenamento e nas operações de exclusão de conta
ou canal; não deixar arquivos órfãos ou acessíveis a outro usuário.

## Pesquisa real e obtenção de mídias

O modelo planeja consultas e sintetiza conteúdo; o código realiza as buscas e
leituras. Não presumir que o modelo textual navega por conta própria.

Proposta técnica: conector configurável para resultados Google e busca/metadados
YouTube com yt-dlp. O conector deve permitir outras fontes e consultas por domínio.
Primeiro adaptador proposto para Google: SerpAPI. Sua implementação e parâmetros
serão verificados na documentação oficial antes de escrever a integração.

Conectores ausentes ou indisponíveis devem resultar em configuração pendente ou
erro recuperável, nunca em referências inventadas. Não exigir esse serviço para
que recursos existentes de clipping continuem funcionando.

Referências do usuário são sementes. Exigir pesquisa adicional e pelo menos 20
referências relevantes e realmente consultadas, sem duplicatas/republicações.
Contar apenas as verificadas; exibir separadamente links não lidos. Distinguir fontes
factuais, inspiração editorial e candidatos de mídia.

Se a quantidade for inferior a 20, salvar o documento parcial e oferecer avançar
ou aprofundar. A autorização para avançar fica registrada e não autoriza preencher
lacunas com invenções. O arquivo inclui conteúdo substancial para a etapa 2.

Guardar proveniência, acesso parcial/integral, datas, trechos úteis, autoria,
condições de uso identificadas e adequação ao recorte vertical. A frase de autorização
educativa automática fica apenas como contraexemplo incorreto.

Um novo leitor de páginas e baixador de mídias públicas deve validar esquema, DNS,
endereços públicos e redirecionamentos; limitar tamanho, tempo e tipo de conteúdo.
Isso permite fontes amplas sem transformar URLs em acesso à rede local do servidor.
Não remover as proteções do caminho atual de clipes.

## IA, documentos e timeline

Continuar centralizando chamadas de IA em `studio/ai.py`, com registro em
`studio/usage.py`. O coordenador da criação fornece instruções e contexto; adaptadores
atuais de Codex/Claude continuam sem acesso irrestrito a shell ou arquivos.

Solicitar JSON validável para roteiro, cenas, falas, referências e timeline. Os
Markdown são gerados a partir desses dados, com IDs estáveis e ligações às fontes.
Não tentar executar instruções livres extraídas de um Markdown como comandos FFmpeg.

A timeline define operações suportadas: mídias, trechos, enquadramento, movimento,
cortes/transições, textos, legendas, trilha, efeitos, volume e narração. Validar
tipos, intervalos, duração e dependências antes de renderizar. Se o modelo solicitar
um efeito não implementado, reformular dentro do catálogo ou registrar a pendência;
não dizer que o efeito foi aplicado.

## Voz e configurações externas

Proposta técnica: primeiros adaptadores de narração OpenAI e ElevenLabs, com uma
interface comum e catálogo de capacidades por provedor. Modelos, vozes e parâmetros
serão confirmados na documentação oficial durante a implementação. Não escolher
uma voz final pelo usuário.

Configurar credenciais de voz e pesquisa separadamente da IA textual. Em modo local,
o proprietário usa a tela de configurações. Em modo com contas, cada usuário mantém
suas configurações isoladas; variáveis de ambiente do servidor são administradas pelo
responsável pelo servidor. Não presumir uma chave de voz compartilhada entre contas.

Segredos são salvos fora de arquivos rastreados, não retornam nas respostas públicas,
não entram no contexto da IA nem nos documentos, e são ocultados nos erros. O formato
de armazenamento segue as convenções de segredos do projeto.

Salvar favoritas por provedor + ID + modelo e configurações. Oferecer amostra curta,
síntese por fala e regeneração incremental. Direção de voz separada do texto falado.
Não afirmar suporte universal a SSML/emoções. Medir áudio real, ajustar a timeline
e produzir o roteiro integrado. Salvar recibos dos trechos válidos para reutilização.

## Edição, revisão e exportação

Implementar renderização de timeline com FFmpeg, reaproveitando legendas e recursos
existentes quando aplicáveis. Trabalhar em pasta temporária curta no Windows.
Proposta de saída: final 1080 × 1920, 30 fps; piloto completo em 540 × 960, mantendo
9:16, proporção dos elementos e toda a edição. São escolhas técnicas propostas,
compatíveis com as constantes existentes; o usuário pode pedir outra qualidade 9:16.

Executar recortes verticais com preservação do assunto, movimentos, textos legíveis,
transições, mistura de trilha/efeitos e redução da trilha sob a voz. Reaproveitar
interações aprovadas do canal quando previstas no roteiro. Trilhas reais precisam
estar disponíveis; não substituir silenciosamente por uma promessa de música.

O piloto cobre a duração inteira, salvo pedido explícito de amostra. A revisão
inclui a prévia reproduzível, comentários por cena/timecode e histórico. Feedback
vago gera perguntas específicas; pedidos claros independentes podem ser executados.
Não transformar feedback incerto em alteração global por adivinhação.

Testes técnicos verificam dimensões, decodificação integral, arquivos, duração,
áudio e sobreposição de elementos. A revisão audiovisual tem relatório distinto:
registrar o que foi inspecionado por modelo e por usuário e o que não foi verificado.
Um teste de FFmpeg não comprova adequação semântica de imagens, naturalidade da voz
ou que alguém assistiu ao vídeo. Não inventar uma revisão humana/visual.

Os prompts exigem escuta da narração e revisão audiovisual integral, portanto o
fluxo prevê análise automática por um adaptador realmente capaz de processar áudio
e vídeo, além da revisão do piloto pelo usuário. Proposta técnica: primeiro
adaptador audiovisual Gemini, com credencial configurada e capacidades verificadas
na documentação oficial durante a implementação. A IA de texto escolhida para
roteirização pode continuar sendo outra.

Dividir áudios/vídeos longos em partes com cobertura integral e contexto de cenas;
registrar os intervalos efetivamente analisados, falhas, modelo e resultado. A
análise deve examinar som e imagem, não apenas transcrição e frames isolados. Não
prometer que o modelo detecta toda falha. Se faltar configuração ou capacidade,
deixar a etapa pendente e pedir a configuração necessária; não substituir essa
verificação obrigatória apenas por um relatório técnico ou uma aprovação presumida.

Após aprovação explícita de uma versão, aplicar alterações determinadas, exportar,
verificar o arquivo final e apresentar prévia e download, legendas, timeline,
documentação e créditos. Se houver mudança editorial além das alterações autorizadas,
voltar à revisão. Nenhuma publicação automática.

## Módulos e superfícies previstos

- Novo carregador de prompts e compositor de contexto, separado do texto de clipes.
- Novo coordenador de projetos, estados, documentos, memória e histórico.
- Conectores de pesquisa, leitura pública e obtenção de mídias.
- Adaptadores de voz centralizados na camada de integração de IA/serviços.
- Novo renderizador de timeline que reutiliza `studio/render.py` e `studio/media.py`.
- Rotas em `studio/server.py` para projetos, memória, nicho, decisões, revisão,
  configurações e arquivos com autorização.
- Integração de fila, sync, armazenamento e exclusão de conta/canal.
- Aba em `web/index.html`, preservando os estilos e a tradução existentes.

Os nomes exatos dos módulos, contratos internos e granularidade das rotas ficam
com o implementador. Não é necessária uma migração de dados dos clipes existentes.

## Ordem de implementação proposta

1. Rodar `python tests/run.py` antes de alterar `studio/`.
2. Implementar carregamento dos prompts, composição, snapshots e testes de leitura
   a cada execução, inclusive alteração de arquivo sem reiniciar o servidor.
3. Implementar estado persistente, isolamento, memória e confirmação de nicho.
4. Conectar pesquisa real, catálogo de fontes e decisão sobre menos de 20 referências.
5. Implementar roteiro estruturado e geração dos documentos.
6. Conectar voz, amostras, favoritas e narração incremental com tempos reais.
7. Implementar obtenção de mídias e renderização da timeline 9:16.
8. Integrar a aba e o fluxo de revisão/feedback/entrega.
9. Rodar testes de integração e `python tests/run.py` após as mudanças; verificar a
   interface e um vídeo de teste local sem usar dados reais do usuário.

Essa ordem organiza o desenvolvimento; não reduz a entrega a uma primeira versão
somente textual.

## Desempenho e funções dedicadas em Rust

Medir duração e consumo de CPU/memória por operação: leitura de prompts, pesquisa,
obtenção de mídia, processamento de documentos, síntese, renderização e revisão.
Registrar tempos técnicos sem conteúdo sensível. Estabelecer uma medição comparável
antes de escolher uma otimização; mostrar progresso e permitir retomada enquanto
serviços externos respondem.

Se uma função local de processamento se tornar um gargalo comprovado e não for
resolvida adequadamente pelo código nativo já usado, implementar uma função dedicada
em Rust, mantendo o contrato de entrada/saída e a autorização por usuário no backend.
O usuário autorizou essa substituição. Não é necessário pedir autorização novamente
para uma otimização rotineira dentro desse escopo.

A proposta inicial de integração é um executável auxiliar com protocolo estruturado,
chamado com argumentos fixos e entrada validada, sem montar comandos de shell. Ele
deve ter limites de tempo, tratamento de falhas e suporte às plataformas atendidas.
Se a medição justificar uma extensão nativa em vez desse processo, o implementador
pode escolher a alternativa e documentar o ganho. Não exigir compilador Rust do
usuário para funcionalidades que continuam em Python.

Comparar correção, tempo e memória com a implementação anterior. Não migrar o
servidor inteiro nem atribuir à linguagem atrasos de rede, cotas ou filas de APIs.
FFmpeg já realiza a codificação em código nativo; trocar o coordenador por Rust,
sozinho, não comprova ganho de renderização. Otimizações não podem eliminar a leitura
dos prompts antes de cada execução nem enfraquecer validações ou isolamento.

## Critérios de aceitação

- Editar um prompt em disco afeta a próxima execução, sem reiniciar o servidor.
- Cada pedido de etapa recebe regras gerais, prompt atual e documentos necessários.
- Ausência de arquivo ou contexto incompleto impede avanço e mostra a causa.
- Uma pasta resolvida a partir da instalação funciona mesmo com outro cwd.
- Toda execução registra os hashes e as revisões efetivamente utilizados.
- Nicho inferido exige confirmação; correção manual é persistida.
- Memória, credenciais, documentos e mídias não vazam entre contas/canais.
- Fontes do usuário não limitam a pesquisa; resultados inacessíveis não contam
  como consultados; menos de 20 exige decisão explícita.
- Roteiro não adiciona fatos fora da pesquisa documentada.
- Direção de voz não é falada; áudios têm duração medida e regeneração incremental.
- Piloto é completo, reproduzível e vertical; silêncio não autoriza a finalização.
- Feedback vago leva a perguntas, e aprovações são vinculadas a versões.
- Final e piloto passam por inspeção técnica real; limitações de revisão são visíveis.
- Narração e vídeo têm cobertura integral registrada na revisão audiovisual; falta
  de capacidade/configuração impede declarar essa verificação concluída.
- A entrega contém MP4 9:16, documentos, timeline, créditos e legendas aplicáveis.
- Testes cobrem modo local e contas, retomada, falhas externas e credenciais ocultas.

Sem chaves, usar adaptadores simulados nos testes, fontes de teste controladas e
mídias locais curtas. Isso verifica o pipeline e o isolamento sem afirmar que houve
uma pesquisa pública ou uma síntese paga real. Testes externos reais dependem de
serviços configurados; se não forem executados, essa limitação deve ser declarada.

## Decisões propostas pelo implementador

As escolhas de fila coordenada, persistência JSON por canal, SerpAPI como primeiro
conector Google, dois primeiros adaptadores de voz, Gemini para revisão audiovisual
e resolução reduzida do piloto
são propostas técnicas deste documento, não preferências já confirmadas pelo usuário.
Custos e credenciais dependem dos serviços que o usuário configurar. A implementação
não precisa receber chaves pelo chat nem escolher a voz final agora.
