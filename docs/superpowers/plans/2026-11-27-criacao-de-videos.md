# Criar vídeo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Entregar a aba de produção completa, com prompts em disco, pesquisa real,
narração por API, montagem 9:16, revisão e exportação.

**Architecture:** Coordenador Python com etapas persistidas por usuário/canal,
carregamento de instruções antes de cada operação e saídas estruturadas. Conectores
HTTP fazem pesquisa/voz/análise audiovisual; FFmpeg realiza a montagem local.

**Tech Stack:** Python, FastAPI, httpx, yt-dlp, FFmpeg, Pillow e HTML/CSS/JavaScript.

**Spec:** `docs/superpowers/specs/2026-11-27-criacao-de-videos-design.md` (aprovada).

## Global Constraints

- Formato vertical 9:16; final 1080 × 1920 a 30 fps e piloto 540 × 960.
- Reler regras gerais e prompt integral da etapa a cada chamada de IA.
- Documentos obrigatórios e credenciais ausentes impedem o avanço.
- Usuário obtido da sessão; threads com contexto; mudanças chamam `ctx.bump()`.
- Preservar preferências, versões, aprovações e arquivos de outros usuários.
- Nenhuma publicação automática ou geração de vídeo por IA.
- Rodar `python tests/run.py` antes e depois de alterar `studio/`.
- Rust somente quando medições demonstrarem um gargalo local justificável.

## Review Focus

- Prompt editado sem reiniciar precisa aparecer no próximo pedido, inclusive retries.
- Documentos com instruções maliciosas não podem virar comandos ou regras do fluxo.
- Queda durante síntese/render não pode perder versões nem repetir áudio válido.
- Aprovação antiga não pode autorizar uma versão nova do piloto.
- URLs externas não podem acessar endereços privados por DNS ou redirecionamento.

## Task 1: Instruções, estado, memória e configurações

**Files:** criar `studio/creation_prompts.py`, `studio/creation_store.py`,
`tests/test_creation.py`; atualizar a situação da especificação e do README.

**Interfaces:** `creation_prompts.compose(stage, operation, context, documents)`
retorna prompt e metadados; `creation_store` oferece projetos, memória, documentos,
configurações e artefatos sempre sob o usuário atual.

- [x] Escrever testes de releitura, falta de prompt/documento, cwd diferente,
  snapshots, isolamento, favoritos e segredos públicos ocultos; rodar e observar RED.
- [x] Implementar loader explícito, limites sem truncamento silencioso, estado
  atômico, revisões de documentos e memória por canal.
- [x] Rodar `python tests/test_creation.py`; exigir GREEN.

## Task 2: Pesquisa e conectores de serviços

**Files:** criar `studio/creation_web.py`; ampliar `studio/ai.py`;
criar `tests/test_creation_services.py`.

**Interfaces:** busca Google/YouTube e leitura pública devolvem evidências;
`ai.creation_speech(settings, text, direction, out)` sintetiza sem vazar chaves;
`ai.creation_review(settings, path, prompt)` examina áudio/vídeo e devolve JSON.

- [x] Testar DNS/redirect privado, HTML, duplicatas, falhas externas, síntese e
  revisão com HTTP simulado; observar RED.
- [x] Conferir documentação oficial de SerpAPI, OpenAI, ElevenLabs e Gemini.
- [x] Implementar rede limitada, pesquisa, mídias, síntese, catálogo de vozes e
  revisão audiovisual com relatórios de cobertura e segredos separados.
- [x] Rodar `python tests/test_creation_services.py`; exigir GREEN.

## Task 3: Timeline e renderização vertical

**Files:** criar `studio/creation_render.py`, `tests/test_creation_render.py`.

**Interfaces:** validar timeline estruturada; `render(timeline, folder, output,
height)` monta cenas e áudio; `inspect(path)` verifica arquivo integral.

- [x] Testar caminhos, tempos, operações inválidas, 9:16, textos, áudio, trilha e
  transições com mídia sintética curta; observar RED.
- [x] Implementar catálogo fechado de operações, ASS/textos, movimentos,
  transições, mixagem, legendas, temporários curtos e inspeção técnica.
- [x] Rodar `python tests/test_creation_render.py`; exigir GREEN e medir tempos.

## Task 4: Coordenador completo e decisões do usuário

**Files:** criar `studio/creation.py`; ampliar `tests/test_creation.py`.

**Interfaces:** criar/listar/obter projeto, analisar/confirmar nicho, executar,
decidir referências, enviar feedback, aprovar versão, cancelar e retomar.

- [x] Testar fluxo integral com serviços simulados e mídia local: cinco etapas,
  documentos, decisões, cobertura de revisão, retomada e aprovação obsoleta; RED.
- [x] Implementar etapas com contratos estruturados, fila justa, recibos por fala,
  revisão incremental, relatório de desempenho e dependências por revisão.
- [x] Exigir prompts e documentos em todas as chamadas, inclusive correções.
- [x] Rodar `python tests/test_creation.py`; exigir GREEN.

## Task 5: Rotas e interface da aba

**Files:** criar `studio/creation_routes.py`; modificar `studio/server.py`,
`studio/jobs.py`, `studio/account.py`, `web/index.html`; criar testes de API/UI.

**Interfaces:** `/api/creation/*` autenticada; rota `/app/create`; sync inclui
resumo de projetos; APIs de documentos/arquivos verificam propriedade.

- [x] Testar modo local/contas, acesso cruzado, arquivo privado, validação de
  decisões e exclusão durante execução; observar RED.
- [x] Implementar API com limites/quota, worker justo, retomada segura e bloqueios
  de exclusão para projetos em execução.
- [x] Implementar tela com canal, tema, referências, memória, serviços, progresso,
  documentos, piloto 9:16, feedback, aprovação e downloads.
- [x] Verificar sintaxe JS, rotas, estados vazios/erros e interface real.

## Task 6: Verificação final e revisão

**Files:** atualizar `tests/run.py`, `.env.example`, README e relatório de execução.

- [x] Integrar novos testes ao runner sem serviços externos pagos.
- [x] Rodar a suíte completa, testes de render e verificações da interface.
- [x] Solicitar revisão independente do código, corrigir achados relevantes com
  testes e registrar decisões e limitações.
- [x] Medir gargalos antes de decidir sobre Rust; documentar o resultado.
- [x] Entregar mudanças locais e instruções de configuração sem chaves no chat.

## Registro de execução

- Método: implementação nesta sessão, autorizada pelo usuário, sem nova aprovação.
- Ruling: trabalhar no checkout solicitado, em `develop`, preservando alterações
  existentes; não mover a instalação nem criar outra cópia do aplicativo.
- Pre-flight: tarefas 1–5 compartilham o armazenamento e compositor de contexto;
  nomes e assinaturas acima serão mantidos e conferidos nos testes de integração.
- Baseline: `python tests/run.py` iniciado antes de qualquer alteração em `studio/`.

- Tarefas 1–5: verificadas com testes específicos e interface local.
- Revisão independente: achados tratados; testes de regressão em tests/test_creation*.py.
- Benchmark: 100 leituras/composições em 0,0658 s; renders de 2 s em 1,437 s (piloto) e 2,314 s (final). Sem gargalo Python que justifique Rust.
- Registro completo: docs/superpowers/reports/2026-11-27-criacao-de-videos.md.

- Verificação final: python tests/run.py → All tests passed.; 37 testes específicos da criação incluídos.
- Sintaxe: Node --check em creation.js e script inline do HTML; AST dos módulos Python; git diff --check sem erros.
- Ruling: manter alterações locais no checkout solicitado, preservando o estado do Git do usuário; nenhuma integração remota foi necessária para atualizar a aplicação.
