# Vitrine — demonstração para prospecção

Aplicação **separada**, com base 100% sintética. Serve para mostrar o produto a
um prospect sem tocar em dado de cliente e sem reusar a identidade visual da
Rizza, que tem contrato de imagem.

A transportadora da demonstração é a **NORTEVIA TRANSPORTES LTDA** — inventada,
com semente fixa: rodar o gerador duas vezes produz a mesma empresa, então os
prints continuam valendo.

```
http://localhost:5000
  diretor@vitrine.demo  / demo123   (admin — todas as abas)
  operacao@vitrine.demo / demo123   (só Embarques, CIOT e PGR)
```

O segundo usuário existe para mostrar o **controle de acesso por aba** ao vivo,
que costuma ser pergunta de diretoria.

---

## Subir do zero

```bash
python -X utf8 seed/gerar.py        # gera as fixtures (~20 s)
python -X utf8 seed/_valida.py      # gate: as fixtures passam pelas réguas do app
python -X utf8 seed/bootstrap.py    # cria o banco, carrega GPS e usuários
python server.py
```

## Manter a base viva

```bash
python -X utf8 seed/atualizar.py            # ciclo completo (~2 min)
python -X utf8 seed/atualizar.py --rapido   # só os robôs do dia (~30 s)
```

**Rodar o `--rapido` uma vez por dia enquanto a vitrine estiver de pé.** A base
tem data: as telas abrem no mês corrente, "Cargas hoje" conta o dia e o mapa
mostra quem está na estrada — base parada mostra zero em tudo isso e parece
sistema sem uso. O ciclo faz, nesta ordem: fixtures com hoje = hoje → trilha de
GPS → robô do manifesto (a continuação vem junto) → motor/pernas/janela (as
viagens vazias) → PGR → CIOT → fita → inventário de CO₂e.

Duas sutilezas que o ciclo resolve e valem saber:

* **a última varredura do robô fecha em AMANHÃ.** O filtro dele é
  `data_emissao <= DATE(fim)`, e no DAX isso é meia-noite: manifesto emitido às
  14h de hoje ficaria de fora e "Cargas hoje" mostraria zero. Em produção
  acontece o mesmo — o robô só pega o dia na rodada seguinte;
* **a trilha é cortada em "agora".** O GPS de uma viagem é gerado inteiro, mas o
  histórico só recebe o que já aconteceu; o resto da estrada fica no
  `embarques_simulacao`, e o simulador entrega o ponto mais recente que não está no
  futuro — o caminhão anda em tempo real e o worker grava o trajeto. Até 28/09/2026
  o simulador recebia o último ponto da trilha (o destino, dias à frente): toda
  viagem em andamento às 04:00 virava "No destino" sem trajeto e com KM 0. Os
  pontos ficam em **UTC**, como o resto do app;
* **a trilha é a vida do VEÍCULO, não da viagem.** Espera na origem → carrega →
  estrada pelas cidades do meio do caminho (BH → POA desce por Campinas, Curitiba e
  Joinville) → descarrega (2–8 h) → segue vazio para a próxima origem ou para a
  filial mais perto → parado, reportando de hora em hora. É a saída do destino que
  faz o worker fechar a carga como "Entregue": antes a carreta nunca saía, e 21
  cargas ficavam "No destino" por dias ("há 7d"), com a carreta sumindo no Rio e
  reaparecendo em Serra. O rastreador é do veículo (todo cavalo; 80% das carretas);
* **os três passos de derivação são dry-run por padrão** (em produção mexem em
  carga já lançada) e têm datas de um estudo de agosto/2026 congeladas nos
  defaults. O ciclo passa `--aplicar` e a janela do ano.

## Como funciona

**Uma chave troca a fonte.** `DEMO=true` faz o `execute_dax()` do `server.py`
responder pelas fixtures em vez do Power BI. Funciona porque o app tem **um**
ponto de contato com o Power BI — as ~46 consultas passam todas por ali. Nada
acima disso sabe que está numa demonstração.

**Os robôs são os de verdade.** O que preenche as telas não é fixture: é o robô
do manifesto criando as cargas, o worker de rastreamento movendo o mapa, o PGR
apurando os episódios, a conferência do CIOT achando as pendências e a fita
montando as ordens. A fixture é só o SSW sintético que eles leem.

```
seed/gerar.py  →  fixtures (documentos)  →  execute_dax (DEMO)  →  telas
                  trilha de GPS          →  Postgres            →  worker/PGR
```

### Estado hoje (24/09/2026, medido)

| | |
|---|---|
| viagens geradas | 1.201 (**jan–set/2026**) |
| cargas criadas **pelo robô** | 1.126 |
| posições de GPS | ~130 mil (a janela de retenção, vida inteira de cada veículo) |
| pendências de CIOT | 9 (as plantadas) |
| ordens de coleta | 29 |
| faturamento do ano | R$ 8,7 mi · 1.674 CTe |
| DRE | EBITDA 11% a 15% conforme o mês |
| Veículos (só frota) | margem positiva nos meses fechados |

A janela vai a **janeiro** de propósito: a matriz do Faturamento é tomador × mês,
e meia tabela em branco lê como base incompleta.

As 12 abas respondem: Auditoria, Tarifas, Embarques, Coletas, Mapa, PGR,
Jornada, CIOT, DRE, Despesas, Conhecimentos, Faturamento, Veículos e Verda.
Contábil, Reunião e Contratos ficaram fora do recorte.

## Projeção financeira e o passado da NORTEVIA (26/09/2026)

A aba **Projeção** (`/projecao`) é a mesma do Tabela Auditoria — motor `projecao.py`,
teste `_teste_projecao.py` — no visual da vitrine e com textos genéricos. Ela precisa
de ~5 anos de receita e despesa (36 meses de janela + 24 testes às cegas) e de parcelas
futuras já contratadas, que a janela de 9 meses não tinha. Quem dá esse passado é o
`seed/narrativa.py`:

| | |
|---|---|
| história | crescimento de ~12% ao ano desde jan/2022, sazonalidade de transportadora, choque mensal de ~5% e um **cliente novo** grande a partir de mar/2025 (+8% de patamar — o degrau que o modelo admite não prever) |
| receita por ano | 2022 R$ 7,0 mi · 2023 7,7 · 2024 8,7 · 2025 10,6 |
| margem EBITDA | 4% a 16% por mês, com as quedas de dezembro/janeiro (13º, férias, IPVA) |
| contratos | FINAME, CDC, consórcio, empréstimo e capital de giro (`CONTRATOS`), com parcelas até 2030 — a escada de compromissos |
| provisões | o financeiro lança PREVISAO/PROVISAO (valor redondo) para os meses à frente — a tabela "Projeção × lançado no ERP" |
| substitutos | a observação cita o original; ~1/3 dos originais fica esquecido na base, e a projeção desconta |
| acerto do modelo nesta base | 6,8% no mês seguinte, 5,9% na soma de 12 meses — na faixa da operação real, de propósito (99% ninguém acredita) |

Três regras que sustentam isto:

* **O histórico mora só no dataset da DRE e no 477.** O robô do manifesto, o mapa e as
  telas operacionais leem o outro dataset e continuam com a janela.
* **Semente por mês.** A janela de 9 meses desliza com a data; o passado não. Um mês que
  já passou sai idêntico todo dia, e a projeção só se move com o mês corrente.
* **O histórico é enxuto:** ~45 colunas por CTe em vez das 149 (o `demo_dax` lê coluna
  ausente como vazia, como o BI faz com célula em branco). Com todas, seriam +30 MB.

A janela principal também passou a seguir a história (volume do mês = tendência ×
sazonalidade × choque). Plana, a projeção via uma reta.

## Atualização diária (produção)

Com `DEMO=true`, o `server.py` roda o **ciclo completo** do `seed/atualizar.py` no boot
(se a base não é de hoje) e todo dia às 04:00 BRT — `VITRINE_ATUALIZAR=false` desliga,
`VITRINE_ATUALIZAR_HORA` muda o horário. Até 26/09/2026 a imagem ficava com a base do dia
do build (zero carga hoje). O `--rapido` não serve no container: não regera as fixtures.
O `demo_dax` relê a fixture quando o arquivo muda, e o `gerar.py` grava de forma atômica.

## Deploy

No servidor, o código fica em `/opt/stacks/portfoliotransporte` — a PASTA é a única
coisa **sem** o "i". Repositório, imagem e serviço têm o "i" (`portifoliotransporte`,
`portifoliotransporte_app`). A imagem é construída no próprio servidor:

```bash
cd /opt/stacks/portfoliotransporte && git pull && docker build -t ghcr.io/ggabrielmilho-web/portifoliotransporte:latest . && docker service update --force --image ghcr.io/ggabrielmilho-web/portifoliotransporte:latest portifoliotransporte_app
```

Em uma linha só: colado em várias linhas no MobaXterm, o `\` quebra e o bash reclama
do `&&`. Não mexer em variável de ambiente pelo editor da stack no Portainer depois
disso — ele volta o serviço para a imagem antiga.

O container novo não tem a marca `seed/fixtures/.atualizado_em`, então ~60 s depois de
subir ele roda sozinho o ciclo completo do `seed/atualizar.py` (3–4 min). Para
acompanhar:

```bash
docker service logs -f portifoliotransporte_app 2>&1 | grep -i vitrine
```

Só se o ciclo não aparecer no log, rodar à mão (nunca junto com o automático — os dois
truncam e recriam as mesmas tabelas):

```bash
docker exec -it $(docker ps -q -f name=portifoliotransporte_app) python -X utf8 seed/atualizar.py
```

## Segredos

`SECRET_KEY` e `DB_PASSWORD` **não moram no `docker-compose.yml`**: entram por `${VAR}` no
ambiente do deploy (`VITRINE_SECRET_KEY`, `DB_PASSWORD`; o `:?` recusa subir sem eles).
Até 26/09/2026 estavam no arquivo, com o repositório público — trocar as duas é pendência.
A pasta estática também foi fechada (`static_folder=None`): `/./<arquivo>` servia o código
e o próprio compose sem login.

## O que mudou em relação ao Tabela Auditoria

| mudança | por quê |
|---|---|
| `demo_dax.py` | serve as fixtures no lugar do Power BI; **falha alto** no DAX que não conhece, para o erro aparecer na preparação e não na frente do cliente |
| `FROTA_NOME` / `FROTA_PATTERN` (env) | o `ILIKE '%RIZZA%'` estava em nove lugares e **não é marca, é regra de negócio**: é o dono das placas que separa Frota de Agregado e Terceiro. Sem extrair, o robô classificava as 539 viagens como Terceiro e não lançava nada |
| 8 colunas no `init_db.py` | `no_local_fonte` e as 7 da ordem de coleta existiam só em produção, por ALTER. O `server.py` as lê, então banco novo subia quebrado (ver abaixo) |
| Contábil, Reunião e Contratos removidos | fora do recorte da vitrine |
| `seed/` | gerador, trilha, gate e bootstrap |

### Achado que vale para o projeto original

O `init_db.py` do Tabela Auditoria **não cria 8 colunas que o `server.py` lê**:
`no_local_fonte`, `coleta_origem`, `coleta_via`, `embarcador`, `origem_cnpj`,
`destino_cnpj`, `origem_endereco`, `destino_endereco`. Foram adicionadas por
`ALTER` no servidor e nunca voltaram ao script. Em produção não aparece porque o
banco é antigo; **um deploy em base nova quebraria** na listagem de cargas
(`c.no_local_fonte não existe`). Aqui já está corrigido; lá continua aberto.

## Identidade visual

Trocada, porque a do Tabela Auditoria tem contrato de imagem e não pode ser
reusada. A paleta e o formato da lateral vêm do **JOGA Portfolio**, com
autorização.

| | antes | agora |
|---|---|---|
| navegação | 16 botões em 3 linhas no topo (~120 px antes do conteúdo) | **barra lateral** de 236 px, agrupada |
| ícones | emoji colorido | glifo geométrico monocromático, herdando a cor do tema |
| superfícies | `#0a0e17` quase-preto azulado | `#16171d` grafite quente |
| acento | ciano `#38bdf8` + índigo `#818cf8` | âmbar `#f0a830` + teal `#2dd4bf` |
| tipografia | DM Sans + JetBrains Mono | Space Grotesk + Space Mono |

Verde, vermelho, laranja e amarelo **não** mudaram: ali são semântica (positivo,
negativo, alerta), não marca — trocá-los junto teria quebrado a leitura de todas
as tabelas de uma vez.

**Onde mexer agora:** `theme.css` (um arquivo, servido como estático pela rota
`/theme.css`). Antes disto cada uma das 21 telas trazia o próprio `:root` e ainda
repetia os valores em ~400 hexadecimais soltos no meio do CSS — trocar de cor era
editar 21 arquivos e torcer.

A barra lateral é montada pelo `nav-perms.js`, que a injeta como primeiro filho
do `<body>` e empurra a página com `body.tem-sidebar`. Nenhum HTML precisou ser
reestruturado: eles já só declaravam um placeholder de menu. Os grupos são os
mesmos do `/inicio`, para as duas telas nunca divergirem.

`/inicio` e `/login` ficam **sem** a lateral de propósito: a entrada é o próprio
menu (cards largos com descrição vendem melhor) e o login não tem sessão para
montar menu nenhum.

## Antes de mostrar a alguém

- [ ] rodar o robô pela janela toda ao regerar a base (as cargas não vêm da fixture)
- [ ] conferir que `DEMO=true` está no ambiente de deploy
- [ ] logo: hoje é a inicial "N" num quadrado com gradiente. Trocar por arte, se houver

## Duas armadilhas que o shim escondeu (corrigidas)

Valem registro porque nenhuma aparecia como erro — apareciam como **número
errado**, que é o defeito mais caro numa demonstração:

1. **`CALCULATE(SUM(...), FILTER(...))`** passa uma *tabela* como filtro, não uma
   condição booleana. Tratando como condição, a receita da DRE vinha zero. E o
   `CALCULATETABLE` precisa empurrar o filtro **até a tabela base**: aplicado
   depois do `SUMMARIZE`, a coluna `REF` já não existe e as despesas zeravam.
2. **`SUMX('t', ...)` e `VALUES('t'[c])` dentro de um `SUMMARIZE`** têm de
   enxergar só as linhas do grupo. Recarregando a tabela inteira a cada grupo, a
   retenção da aba Veículos saía **140× maior** e o KM rodado dava 24 milhões.
   Hoje o `Ctx` carrega de qual tabela as linhas vieram, e o contexto manda.

O segundo só foi encontrado **olhando a tela** — os endpoints respondiam 200 e o
gate de fixtures passava. Vale a regra: antes de mostrar, abrir e ler os números.

## Pente-fino: nome real não pode sair daqui

`seed/_pente_fino.py` varre **o que sai** — cada página renderizada, cada
resposta de API, cada estático servido e cada fixture — contra uma lista de 23
termos proibidos (o cliente, os fornecedores, o ERP, a plataforma de BI, os
embarcadores reais).

```bash
python -X utf8 seed/_pente_fino.py      # tem de terminar em "PENTE-FINO LIMPO"
```

**Rodar isto antes de toda apresentação.** Procurar no código não bastava: o
nome chegava à tela por caminhos que uma busca em HTML não vê — texto de alerta
montado em Python (`"Manifesto em placa que não é da frota Rizza"`), o usuário
`admin@rizzalog.com.br` criado pelo `init_db`, o autor das 1.126 cargas gravado
no banco (`"Robô SSW (manifesto)"`), o cabeçalho das imagens de WhatsApp, o
fornecedor na regra de custo (`BVIX`, `AUTOTRAC`) e chaves de JSON
(`eh_rizza`, `so_valecard`) que aparecem a quem abrir o inspetor.

O que virou genérico, e por quê: nome de fornecedor entrega o cliente **e** o
fornecedor, e amarra o produto a uma operação só.

| era | virou |
|---|---|
| Verda (rota `/verda`, aba, textos) | **Carbono** (`/carbono`), "plataforma de carbono" |
| ValeCard | cartão de abastecimento |
| Sem Parar | tag de pedágio |
| SSW | ERP |
| Power BI | BI |
| 3S | rastreador |
| BVIX / AUTOTRAC (regra de custo) | `FORNECEDOR_RASTREADOR` (env) |
| Pamcard | ANTT (só o órgão, que é público) |
| Nestlé (placeholder), Martins (prompt) | nomes fictícios |
| colunas `*_eh_rizza` no banco | `*_eh_frota` |

## Avisos

- Em modo demo o CIOT tenta ler o histórico de refresh do Power BI e toma 403.
  Ele degrada sozinho (só loga), mas polui o log — vale silenciar quando sobrar tempo.
- Sem `CARTO_API_KEY` os tiles do CARTO vêm carimbados com "API KEY REQUIRED" no
  meio do mapa. A vitrine cai no **Esri Dark Gray**, que não pede chave; com
  chave, segue o CARTO (é o que produção usa).
- O `.env` daqui não tem credencial de cliente nenhuma. Não copiar o `.env` do
  Tabela Auditoria para cá.
