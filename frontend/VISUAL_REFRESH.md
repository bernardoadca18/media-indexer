# Repaginação NeoVids — 17/10/2026

Shell lateral, biblioteca de projetos, sessões e busca receberam identidade dark, tipografia
e hierarquia compatíveis com os demais consoles. Removido o layout do template Vite que
limitava largura, centralizava textos e forçava títulos gigantes. A folha passa a carregar
as utilidades pelo import apropriado à versão de Tailwind já instalada, sem upgrade.

Busca exige projeto e usa o POST `/media-search/projects/{project_id}` existente, com `query`
e `max_results`. As antigas rotas GET de busca não existem no backend; o cliente foi corrigido.
A interface oferece somente o escopo de projeto suportado pela API. Resultados pertencem à
consulta submetida, não ao texto em edição. O link preserva sessão, índice temporal e intervalo;
a timeline recebe `start_seconds` e `end_seconds` validados e mantém o contrato de leitura.
Nenhum preview de mídia foi inventado.

## Iniciar

Na pasta `frontend`, execute `npm ci` e `npm run dev -- --host 127.0.0.1 --port 5175`.
A porta é uma escolha de desenvolvimento para evitar colisão com os outros consoles;
use uma porta livre e confira a URL impressa pelo Vite. Configure `VITE_API_BASE` com a API
real acessível pelo navegador (padrão existente: `http://localhost:8000`). Reinicie o Vite
após mudar variáveis. A API precisa permitir a origem de desenvolvimento em seu CORS.

A publicação da UI no fluxo de infraestrutura local não foi confirmada. Por isso nenhum
link para esta UI foi inserido no Orchestrator. A interface pode ser iniciada diretamente.

## Validação

- PASS: TypeScript/build (`npm run build`) e lint (`npm run lint`, oxlint).
- PASS: navegador com fixtures: biblioteca, busca por projeto, projeto obrigatório, resultado
  e sessão com intervalo 12–24 s; request da timeline contém os limites corretos em segundos.
- PASS: 1440×900 e 390×844, busca móvel sem overflow horizontal; modal de projeto com Escape
  e retorno de foco. Vazio validado com lista sintética vazia.
- PASS: três testes de contrato: `node --experimental-strip-types --test tests/search-contract.test.mjs` em frontend (Node 22.6+).
- SKIP: APIs reais, ingestão e processos de indexação.

Capturas e fixtures ficam em `C:/workspaces/neovids/visual-qa`. Ingestão, evidências e cálculos
da timeline foram preservados. Não foram iniciados jobs, provedores, FFmpeg ou embeddings.

O botão que apenas alternava estado local de reprodução, sem player associado, agora destaca
o trecho com indicação acessível; não afirma reproduzir uma mídia inexistente na tela.
