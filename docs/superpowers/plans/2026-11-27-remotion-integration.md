# Integração local FFmpeg + Remotion

Base autorizada: docs/edicao-ffmpeg-remotion.md e pedido explícito de implementação completa.
Execução no checkout solicitado, preservando alterações existentes e composições de exemplo.

## Contratos

- Timeline canônica v2.0 em frames, 1080×1920, 30 fps, assets por ID, tracks e transições explícitas.
- Pydantic rejeita campos desconhecidos; migração explícita do formato scenes preserva tempos e narração.
- Python prepara assets/áudio com FFmpeg, entrega mídia privada por servidor efêmero restrito ao job.
- Node valida entrada, renderiza PeakCutVertical sem áudio; FFmpeg realiza a mixagem única e mux final.
- Worker recebe job JSON, emite progresso/erro/done em NDJSON. Cancelamento encerra a árvore de processos.
- Piloto e final compartilham timeline/assets/templates e aprovação vinculada a seus hashes.
- Cada chamada continua relendo prompts e documentos, inclusive reparos e revisão.

## Tarefas

- [x] 1. Registrar baseline completo e criar testes de timeline/migração/contratos antes do código.
- [x] 2. Implementar modelos canônicos e catálogo de capacidades; ligar schema editorial aos recursos reais.
- [x] 3. Implementar composição, worker e validação Node em my-video, preservando exemplos.
- [x] 4. Integrar preparação/cache, mídia privada, render, mixagem, verificação e cancelamento no Python.
- [x] 5. Integrar revisões, snapshots, progresso e aprovação por timeline/assets/templates no coordenador.
- [x] 6. Testar render real de vídeo, imagem, transição, texto, legenda, narração e música em 9:16.
- [x] 7. Rodar suíte completa, lint/types Node, revisão independente e atualizar guias com limitações reais.

## Foco de revisão

Caminhos e rede fora do job; preservação da narração em sobreposições; mudança de template depois da
aprovação; cancelamento de Chromium/FFmpeg; erro estruturado sem credenciais; migração de projetos;
mesma timeline entre resoluções e consulta atual dos prompts.

## Registro

Implementação iniciada após autorização explícita; sem novo pedido de aprovação da mesma arquitetura.

Verificação final: python tests/run.py → All tests passed; npm test → 7/7; npm run lint e npm run build → exit 0.
Revisão independente: três achados corrigidos com testes RED→GREEN; relatório em docs/superpowers/reports/2026-11-27-remotion-integration.md.
