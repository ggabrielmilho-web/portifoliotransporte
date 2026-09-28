# Base demo — passo 1: o shape das tabelas do Power BI

Objetivo do diretório: montar uma **base de demonstração sintética** para prospecção,
sem nenhum dado da Rizza. Este passo coletou apenas o **contrato** das tabelas
(quais colunas, de que tipo, em que formato e volume), que é o que o gerador do
passo 2 precisa imitar.

**Nada aqui é rastreado pelo git** (mesmo padrão das ~35 ferramentas `_*.py` do repo).

## Por que o shape importa

Metade das telas faz `EVALUATE 'public X'` — puxa a tabela inteira e renderiza o
que vier. Quem define as colunas necessárias **não é o DAX, é o HTML**: `index.html`
declara 40 colunas, `dre-conhecimentos.html` ~60. Gerar dado sem o shape certo
entrega tela com coluna vazia, que numa demo lê como sistema quebrado.

## O que foi gravado, e o que não foi

`shape/<tabela>.json`, 135 KB no total. Por coluna: tipo, % de nulo, faixa de
comprimento, máscara de formato e ordem de grandeza numérica.

**Nenhum valor de linha foi persistido.** A amostra (`TOPN 25`) é lida, resumida
e descartada dentro da própria função. Três regras no `_shape.py`:

1. a máscara troca dígito por `9`, maiúscula por `A` e minúscula por `a` — diz
   *"placa é `AAA9A99`"*, nunca qual placa;
2. **184 colunas sensíveis** (nome, CNPJ, CPF, endereço, telefone, chassi,
   conta bancária…) não ganham nem máscara: só tipo, comprimento e % de nulo;
3. valor distinto só de **rótulo de sistema**, por lista explícita (`ALLOW`) —
   tipo de operação, situação, UF, produto, evento. Nada que identifique pessoa
   ou empresa.

`placa` fica fora da lista de sensíveis de propósito: a máscara dela é
justamente o que o gerador precisa, e ela não identifica pessoa sem o cadastro.

Auditoria do resultado: `0` vazamentos (nenhum vocabulário em coluna sensível,
nenhuma máscara em coluna cegada).

## O que a coleta revelou

| achado | consequência para o gerador |
|---|---|
| **`conhecimentos_emitidos` é DIFERENTE nos dois datasets** — `main` tem 102 colunas / 22.656 linhas (2025→2026); `dre` tem 149 / 349.268 (2021→2026) | a fixture é **por (tabela, dataset)**, não por tabela. O robô e a Verda leem a do `main`; DRE e Faturamento leem a do `dre` |
| **datas são `str` ISO** (`9999-99-99A99:99:99`), não `datetime` | gerar string; `date` quebra comparação de texto |
| **`placa_carreta` convive nas duas grafias** (`AAA9999` e `AAA9A99`), `placa_cavalo` só Mercosul | a demo tem de reproduzir a mistura, senão a normalização nunca é exercitada — e ela é um diferencial que se mostra |
| **`CHAVE_MANIFESTO` e `CHAVE_CTRB` são colunas reais** de `public manifestos` (`AAA999999-9` / `AAA999999`) | o gerador as emite; CIOT, Jornada e Verda dependem delas |
| `capacidade` e `ano` de `veiculos_045` são **texto** (`99,99`, `99` — ano de 2 dígitos) | idem |
| **98 eventos** no 477, `MAPA_DRE` mapeia 97 | gerar os eventos do `MAPA_DRE`; ver pendência abaixo |
| 4 colunas da Auditoria (`data_efetiva`, `ancoragem`, `prop_cavalo`, `prop_carreta`) **não existem na tabela** — são anexadas em Python | a fixture **não** as inclui |
| `custo_pessoal` tem 133 linhas e competência só até `2026-08` | a provisão da aba Veículos (usa o mês anterior) precisa ser exercitada na demo |

### Pendência de produção encontrada de passagem (não é da demo)

Dois eventos do 477 **não estão no `MAPA_DRE`**: `IR` e
`RETENCAO PIS/COFINS/CSLL`. Um terceiro está mapeado e não existe na base
(`DESPESAS PJ - OPERACIONAL`). Não foi tocado — vale conferir se a linha de
Impostos da DRE está perdendo o IR.

## Volume da base real (para dimensionar a sintética)

```
Auditoria Receita          5.224   ctrbs_oss              5.224
manifestos                 4.951   manifestos_ctrc      393.798
conhecimentos (main)      22.656   conhecimentos (dre)  349.268
consulta_despesas_477    153.784   tarifas_frete          3.188
veiculos_045               6.911   motoristas_047         3.933
semparar_lancamentos      12.477   abastecimentos_vc      1.438
rotas_km                   2.184   coletas_0157             290
custo_pessoal                133
```

A demo não precisa desse volume — precisa de ~3 meses coerentes. O número serve
para a ordem de grandeza dos KPIs não parecer de brinquedo.

## Como rodar de novo

```bash
python -X utf8 _seed_demo/_shape.py --plano                      # o que faria, sem rede
python -X utf8 _seed_demo/_shape.py                              # tudo (~1 min)
python -X utf8 _seed_demo/_shape.py --tabela manifestos          # uma só
```

Lê o `.env` da raiz (as 6 chaves `POWERBI_*`). É só leitura: `COUNTROWS`,
`MIN/MAX` da coluna de data, `TOPN 25` e os distintos da `ALLOW`.

---

# Passo 2 — o gerador (FEITO)

```
empresa.py   a transportadora fictícia (semente fixa → sempre a mesma empresa)
gerar.py     a cadeia documental + custos + coletas
trilha.py    o rastro de GPS
_valida.py   o gate: as fixtures passam pelas RÉGUAS DO PRÓPRIO PROJETO
fixtures/    a saída (18 arquivos, ~19 MB)
```

```bash
python -X utf8 _seed_demo/empresa.py          # confere o universo
python -X utf8 _seed_demo/gerar.py --resumo   # conta, sem gravar
python -X utf8 _seed_demo/gerar.py            # grava fixtures/
python -X utf8 _seed_demo/_valida.py          # gate
```

## O que ele gera

**NORTEVIA TRANSPORTES LTDA**, 5 filiais, 72 veículos (54 frota), 26 motoristas,
16 tomadores, 40 cidades reais. Janela jun–set/2026, **539 viagens**.

| tabela | linhas | | tabela | linhas |
|---|---|---|---|---|
| manifestos | 539 | | conhecimentos_emitidos | 1.540 (2 datasets) |
| ctrbs_oss | 537 | | manifestos_ctrc | 770 |
| Auditoria Receita | 537 | | consulta_despesas_477 | 580 |
| tarifas_frete | 457 | | abastecimentos_valecard | 677 |
| rotas_km | 186 | | semparar_lancamentos | 924 |
| veiculos_045 | 72 | | custo_pessoal | 78 |
| motoristas_047 | 26 | | coletas_0157 | 31 |
| **posições GPS** | **~130 mil** (até agora) | | embarques_simulacao | ~7.900 (última posição + estrada à frente) |

## O princípio

Gera **viagens**, não telas. Cada viagem emite o conjunto inteiro de documentos,
e os robôs do projeto (robô do manifesto, fita, CIOT, PGR, Verda, worker) montam
as telas em cima disso — como em produção. O cruzamento bate por construção.

Três decisões que sustentam o resto:

* **o esqueleto vem do `shape/`**, não de lista escrita à mão — coluna que falta
  é coluna vazia na tela;
* **o `MAPA_DRE` é lido do `server.py`**, não copiado — duas verdades viram grupo
  vazio na DRE, e o erro só aparece na hora da demo;
* **a trilha grava nas duas tabelas** — `embarques_posicoes_historico` (o PGR lê
  daí, e o backfill é no-op em modo simulado) e `embarques_simulacao` (mapa ao vivo: a última posição passada e a estrada à frente, que o simulador libera conforme o relógio).

## Defeitos plantados

Base toda verde não vende: a demo mostra o sistema **pegando** problema. São
plantados na viagem, não na tabela — a régua tem de achar sozinha.

| defeito | n | quem acha |
|---|---|---|
| CTRB sem CIOT | 4 | aba CIOT |
| CIOT com erro da ANTT/Pamcard | 3 | aba CIOT |
| manifesto sem CTRB | 2 | aba CIOT |
| episódios acima de 95 km/h | 23 | PGR |
| carreta muda (sem GPS) | 1 | Mapa / aferidor |
| rota sem tarifa | ~8% | Auditoria |

## O gate, e o que ele pegou

`_valida.py` joga as fixtures nas funções puras do projeto. Achou três coisas
que eu teria mandado para a demo:

1. **`ciot_erro()` só reconhece a falha se o texto tiver `Mensagem recebida`** —
   é o prefixo com que o SSW grava a resposta da ANTT. Sem ele, os 3 defeitos
   caíam como "campo vazio", que é outra pendência;
2. **`motoristas_047` não tem `situacao` nem `categoria_cnh`** — eu inventei duas
   colunas que a tabela real não tem;
3. **a mesma carreta em duas viagens ao mesmo tempo** (sorteio sem disponibilidade).
   O estrago não ficava no documento: a trilha gravava dois pontos da mesma placa
   no mesmo instante — e a tabela tem `UNIQUE(placa, data_posicao)`, então a
   **carga abortaria** — e o odômetro, cumulativo no aparelho, andava para trás.
   Corrigido na origem, com controle de ocupação de cavalo/carreta/motorista.

## O que falta

1. **Como servir as fixtures** — decisão em aberto, ver abaixo.
2. **Bootstrap do banco**: `init_db.py` cria 23 tabelas; outras **9 nascem fora
   dele** (`ciot_*`, `fita_documentos`, `locais_fontes`, `embarques_programacao`,
   `verda_envios`, `icms_aliquota`). Base nova precisa rodar cada módulo uma vez.
3. **Layout** — requisito da demo (o layout atual tem contrato de imagem com a
   Rizza, então não pode ser reusado): `theme.css` (hoje são 29 HTML, cada um com
   o seu `:root`), `APP_NOME`/`APP_LOGO` e `FROTA_PROPRIETARIO_PATTERN` (o
   `ILIKE '%RIZZA%'` é **regra de negócio**, não marca — classifica
   Frota/Agregado/Terceiro).

### A decisão em aberto: como o app lê as fixtures

| caminho | como funciona | custo |
|---|---|---|
| **A — shim local** | `if DEMO` no `execute_dax()` serve a fixture; um interpretador mínimo cobre os ~8 formatos de DAX que o app usa (EVALUATE tabela, FILTER por data, SELECTCOLUMNS, SUMMARIZE, TOPN, ROW/COUNTROWS) e **falha alto** no que não conhecer | escrever e manter o shim; o desconhecido aparece na preparação, não na demo |
| **B — dataset Power BI de demo** | publicar as fixtures como um dataset próprio; a demo é só outro `POWERBI_DATASET_ID` | zero código no app e todas as 46 consultas valem como em produção, mas exige workspace e publicação |

B é mais fiel; A é mais rápido e não depende de licença. Decidir antes de escrever
o shim.
