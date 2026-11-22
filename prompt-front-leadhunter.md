# Prompt — LeadHunter: front mais interativo e futurista, mantendo a identidade Upside

Leia o `CLAUDE.md` do projeto antes de começar. Ele continua valendo inteiro. Este prompt só acrescenta uma camada de interface por cima do que já existe.

## Objetivo

Hoje o front do LeadHunter (FastAPI + Jinja2 + HTMX + Chart.js) está correto e sóbrio, mas estático. Quero que ele passe a sensação de uma central de comando: viva, responsiva ao que acontece no backend, com movimento e profundidade, como um software de IA moderno. Ao mesmo tempo, quem conhece a Upside precisa bater o olho e reconhecer a marca: o ink quente, o paper, o terracota, o U serifado, o Cormorant nos títulos e o Space Grotesk nos dados.

O futurismo vem de movimento, luz, profundidade e resposta imediata. Não vem de neon, de cor nova nem de visual de game. A referência é o carrossel do Instagram da Upside: fundo ink com brilho quente radial, grade sutil ao fundo, título serifado com a segunda linha em itálico areia, rótulos pequenos em caixa alta espaçada e terracota como única cor de ação.

## Restrições que não se quebram

1. **Sem frontend separado.** Nada de React, Vue, Next ou build de JS. Continua Jinja2 + HTMX + CSS + JavaScript puro em `app/static/js/`. Biblioteca nova só se for pequena, via CDN, sem build e justificada no resumo final. A preferência é resolver com CSS e APIs nativas (View Transitions, `IntersectionObserver`, `requestAnimationFrame`, Web Animations).
2. **`tokens.css` é a fonte da marca.** Não invente hex soltos em `app.css` nem nos templates. Cor nova, sombra, brilho ou duração entra como token em `tokens.css`, derivada da paleta oficial (`--ink`, `--paper`, `--terracota`, `--areia`, `--pedra`). As únicas exceções já existentes são as cores de gráfico.
3. **Cantos discretos.** Mantenha `--radius-sm/md/lg` (4, 6 e 8 px). Nada de pílula, nem em botão, badge ou chip.
4. **Tipografia da marca.** Cormorant Garamond em títulos e momentos editoriais. Space Grotesk em interface, números e tabelas, com `font-variant-numeric: tabular-nums` em todo número que muda.
5. **Terracota é a cor de ação e de destaque.** Não espalhe terracota como decoração. Brilho e acento seguem a regra do carrossel: um ponto de luz quente por área, não dez.
6. **Gráficos acessíveis.** O par investimento × resultados foi validado contra daltonismo. Se você criar um tema escuro, a cor de resultados precisa de uma variante própria validada sobre o ink, com contraste mínimo de 3:1 contra o fundo e distinguível do terracota em protanopia e deuteranopia. Documente a checagem no comentário do token, como já está feito hoje.
7. **Acessibilidade.** Todo movimento respeita `prefers-reduced-motion` (o bloco que já existe em `app.css` precisa continuar cobrindo tudo). Foco visível em todos os controles. Nada de informação só por cor ou só por animação. Contraste AA no texto.
8. **HTMX é o motor.** Toda interação nova precisa continuar funcionando depois de um swap do HTMX. Use event delegation, como em `app.js`, e os eventos `htmx:beforeRequest`, `htmx:afterSwap` e `htmx:afterSettle`. Nada de inicialização que só roda no `DOMContentLoaded`.
9. **Sem regressão.** Os testes (`make test`) continuam passando. Não mude rotas, contratos de templates parciais nem o domínio. Esta tarefa é só apresentação: templates, CSS e JS.
10. **Desempenho.** Anime só `transform` e `opacity`. Nada de layout thrash. `backdrop-filter` apenas em elementos fixos e pequenos (rail, topo, modal, toasts), nunca em linhas de tabela. A página precisa continuar leve numa máquina comum.

## Direção visual

### Tema escuro "Ink" como padrão do app, com o paper como alternativa

- Crie `[data-theme="ink"]` (padrão) e `[data-theme="paper"]` (o visual atual) em `tokens.css`, remapeando apenas os tokens de aplicação: `--surface`, `--surface-sunken`, `--text`, `--text-muted`, `--line`, `--row-hover` e afins. A paleta oficial não muda.
- No Ink, o fundo da área de trabalho é `--ink`, as superfícies são camadas ligeiramente mais claras derivadas de `--ink-soft`, o texto é `--on-ink` e as bordas usam `--line-ink`.
- Coloque um alternador de tema no rodapé do rail. A escolha fica em `localStorage`, com `try/catch`. Aplique o tema antes da pintura com um script inline mínimo no `<head>` do `base.html`, para não piscar.
- O PDF de relatório (`templates/reports/monthly.html` + WeasyPrint) continua no paper e não é afetado.

### Atmosfera

- **Brilho quente:** um gradiente radial terracota bem difuso (opacidade baixa, em token `--glow-warm`) atrás do título de cada página, como no carrossel. É fixo e não acompanha o scroll.
- **Grade técnica:** textura de grade fina no fundo da área de trabalho, feita com `background-image` de linhas em `--line-ink-soft` a cada 32 px, desvanecendo nas bordas com `mask-image`. Deve ser quase imperceptível.
- **Profundidade:** cards e painéis com borda de 1 px, um realce superior interno (`inset 0 1px 0` com o paper a ~4%) e sombra suave. Nada de glassmorphism nas áreas de conteúdo.
- **Títulos de página:** padrão editorial da marca. Rótulo em caixa alta espaçada (`--tracking-wide`, `--fs-micro`, cor areia) dentro de um chip retangular com borda, título em Cormorant e, quando houver segunda parte, ela em itálico areia. Exemplo: "Hunter" / *"quem vale a pena ligar hoje."*

### Rail (barra lateral)

- Fundo ink com leve translucidez e `backdrop-filter` sobre a grade.
- O item ativo ganha um indicador terracota vertical que desliza entre os itens na navegação (View Transitions com `view-transition-name` no indicador). Sem a API, ele aparece sem animação.
- O U do rail tem um brilho sutil no hover. Aproveite a animação de `identidade/Upside Logo Animado.dc.html` como referência para a tela de login e para o estado de carregamento inicial, não para o uso contínuo.

## Interações por área

### Global

- **Transições de página:** ative View Transitions do HTMX (`htmx.config.globalViewTransitions = true`) com um crossfade curto (180–220 ms) e um leve deslocamento vertical do conteúdo. Fallback sem a API: troca instantânea.
- **Barra de progresso de requisição:** uma linha terracota de 2 px no topo da área de trabalho, que avança durante qualquer requisição HTMX e some no `htmx:afterSettle`.
- **Skeletons:** áreas que o HTMX troca (tabelas de prospects, linhas de clientes, métricas) mostram skeleton com shimmer sutil enquanto carregam, usando `hx-indicator`. O shimmer é um gradiente que se move. Com redução de movimento, vira só um bloco estático.
- **Toasts:** o backend já pode mandar `HX-Trigger`. Crie um sistema de toast acionado por um evento `toast` (`{"toast": {"message": "...", "kind": "success|error|info"}}`). Toast no canto inferior direito, ink com borda e barra lateral na cor do tipo, entrando com slide e fade, fechando sozinho em 4 s, pausando no hover e com `role="status"`. Substitua por toast as mensagens de sucesso que hoje recarregam a tela, quando isso não mudar contrato.
- **Paleta de comandos (Ctrl/Cmd + K):** um modal que busca entre páginas (Hunter, Anúncios, Relatórios, Clientes, Jobs se admin), clientes cadastrados e ações (nova busca, gerar relatório). Os clientes vêm de um endpoint JSON leve. Se ele não existir, crie em `app/clients/web.py` ou `api.py`, respeitando as fronteiras do `CLAUDE.md` e com teste. Navegação por setas, Enter abre, Esc fecha, foco preso no modal.
- **Atalhos:** `G` + `H` (Hunter), `G` + `A` (Anúncios), `G` + `R` (Relatórios), `G` + `C` (Clientes), `N` (nova busca no Hunter) e `?` para um painel com os atalhos. Nenhum atalho dispara com foco em campo de texto.
- **Botões:** feedback de pressão (`scale(0.98)`) e, no primário, um brilho terracota que acompanha o cursor via variáveis CSS `--mx`/`--my`. Botão que dispara requisição HTMX mostra um spinner discreto e fica `aria-busy` até o fim.

### Hunter

- **Busca em andamento:** troque o bloco "Buscando no Google" por um painel vivo. Um radar em SVG com varredura terracota girando lentamente, o tempo decorrido contando em tempo real no cliente (a partir de `started_at`) e uma barra que avança até o `max_time` previsto. O polling de 5 s continua igual e o painel só se anima entre as trocas. Fila e falha ganham estados próprios: fila com pontos pulsando, falha com o ícone parado e o texto atual.
- **Fim da busca:** quando o total de encontrados chegar, o número entra com contagem animada (0 → total, ~600 ms, `easeOutCubic`) e a tabela aparece com as linhas surgindo em cascata (stagger de 20 ms, no máximo 15 linhas animadas).
- **Nota do prospect:** troque o número solto por um anel circular em SVG que se preenche até a nota (0–100), com a cor indo de pedra, abaixo de 40, a areia e depois terracota, acima de 70. O número fica no centro e em texto. O tooltip com o porquê (`score_details`) vira um popover ancorado com a lista de pontos por regra, cada uma com uma mini barra.
- **Tabela de prospects:** cabeçalho fixo com fundo translúcido, hover de linha com realce terracota suave e uma borda esquerda de 2 px, e ações rápidas (WhatsApp, Maps, copiar telefone) aparecendo no hover e no foco da linha, sempre acessíveis por teclado. A troca de status pelo select HTMX ganha uma confirmação visual na célula (flash curto).
- **Filtros:** viram chips retangulares alternáveis, mantendo os parâmetros de query atuais, com contagem de resultados atualizando via HTMX sem recarregar a página.

### Anúncios

- **Cards de métrica:** números com contagem animada ao entrar na tela e ao trocar de período, e variação contra o período anterior com seta e cor (sucesso ou erro, sempre acompanhadas de sinal e texto). Cada card tem uma sparkline de 30 dias desenhada em SVG inline a partir dos dados que já vão para o gráfico.
- **Gráficos (Chart.js):** animação de entrada da linha desenhando da esquerda para a direita, crosshair vertical sincronizado entre os dois gráficos (passar o mouse num marca o mesmo dia no outro, como pede o comentário de `charts.js`) e área sob a linha com gradiente na cor da série a ~12%. As cores saem dos tokens do tema ativo e o gráfico precisa ser re-renderizado ao alternar o tema.
- **Lista de clientes do painel:** cada linha com sparkline do investimento e barra de proporção do custo por resultado.
- **Filtro de período:** controle segmentado com indicador deslizante, mantendo o comportamento atual.

### Relatórios

- Lista com prévia em hover: miniatura do PDF quando existir, ou os totais do snapshot num cartão flutuante.
- **Gerar relatório:** botão com estado de progresso. Ao concluir, toast com link "Abrir PDF".

### Clientes e Jobs

- **Clientes:** a busca na lista filtra enquanto se digita (`hx-trigger="input changed delay:250ms"`), com destaque do termo encontrado.
- **Jobs (admin):** cada status com um indicador vivo. `running` pulsa, `failed` fica parado em erro e `queued` mostra pontos em sequência. A tabela se atualiza a cada 5 s por HTMX enquanto houver job em execução, e as linhas que mudaram de status fazem um flash curto.

### Login

- Tela inteira em ink com a grade e o brilho quente, o U animado da identidade no centro, a entrada do formulário em fade e o título em Cormorant com a segunda linha em itálico areia: "LeadHunter" / *"o upside da sua prospecção."*

## Organização do código

- `app/static/css/tokens.css`: temas, tokens de brilho, sombra, duração (`--dur-fast: 150ms`, `--dur-base: 220ms`, `--dur-slow: 600ms`) e curvas (`--ease-out: cubic-bezier(.2,.8,.2,1)`).
- `app/static/css/app.css`: componentes. Se crescer demais, separe em `app/static/css/motion.css` (animações, skeleton, transições) e `components.css`, todos importados no `base.html`.
- `app/static/js/`: um arquivo por responsabilidade, todos com event delegation e sem dependência entre si: `theme.js`, `motion.js` (contagens, stagger, View Transitions), `toast.js`, `command-palette.js`, `shortcuts.js`, `score-ring.js`, `sparkline.js` e o `charts.js` atual, estendido.
- Componentes reutilizáveis de template viram macros Jinja em `app/templates/_components/` (anel de nota, sparkline, chip de título, card de métrica, skeleton).
- Comentários em português e explicando o porquê, no mesmo tom dos arquivos atuais.

## Critérios de aceite

- [ ] O app abre no tema Ink e alterna para Paper sem piscar, e a escolha persiste.
- [ ] Nenhum hex novo fora de `tokens.css` (`grep -E "#[0-9a-fA-F]{3,8}" app/static/css/app.css` só encontra o que já existia ou nada).
- [ ] Com `prefers-reduced-motion: reduce`, nada se move: contagens mostram o valor final e skeletons ficam estáticos.
- [ ] Tudo funciona depois de swaps do HTMX: trocar o período, o status e o polling não quebram nenhuma interação.
- [ ] Paleta de comandos e atalhos funcionam só com teclado.
- [ ] Os gráficos usam as cores do tema ativo, o crosshair é sincronizado e a cor de resultados do Ink está validada e documentada.
- [ ] `make test`, `ruff` e `pyright` passam, com testes novos para qualquer endpoint criado.
- [ ] O PDF de relatório sai idêntico ao de antes.
- [ ] Você conferiu cada tela no navegador nos dois temas, inclusive a busca em andamento, e corrigiu o que estava quebrado antes de concluir.

## Entrega

Implemente tudo, sem placeholders e sem perguntar no meio. Trabalhe por área, na ordem: tokens e temas → global → Hunter → Anúncios → Relatórios → Clientes e Jobs → Login. Rode os testes ao fim de cada área. No final, resuma em poucas linhas o que mudou, qualquer biblioteca adicionada e o motivo, e o próximo passo natural.
