"""Modo demo: as consultas DAX são respondidas pelas fixtures locais.

Quando `DEMO=true`, o `execute_dax()` do `server.py` não fala com o Power BI —
manda a consulta para cá. Este módulo interpreta o **subconjunto de DAX que o
app realmente usa** e devolve a resposta no mesmo formato do `executeQueries`,
para que nada acima disto precise saber que está numa demonstração.

Desenho, e o porquê de cada escolha:

* **Não é um interpretador de DAX.** É um avaliador dos formatos que as ~46
  consultas do app produzem: `EVALUATE` de tabela, `FILTER`, `SELECTCOLUMNS`,
  `SUMMARIZE`, `ADDCOLUMNS`, `CALCULATETABLE`, `TOPN`, `DISTINCT` e `ROW`, com
  as funções escalares que aparecem dentro delas.
* **Falha ALTO no que não conhece.** Uma consulta nova levanta `DaxNaoSuportado`
  com o texto inteiro. O erro aparece na preparação da demo, não na frente do
  cliente — que é o único momento em que ele é barato. Devolver lista vazia
  seria a mesma armadilha do `executeQueries` que corta e responde HTTP 200.
* **Uma fixture por (tabela, dataset).** `conhecimentos_emitidos` existe nos dois
  datasets com shapes diferentes (102 × 149 colunas); servir a mesma para os dois
  entregaria coluna faltando em metade das telas.
"""

import json
import os
import re
from datetime import date, datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(AQUI, 'seed', 'fixtures')


# Funções que devolvem LINHAS (e não um escalar). É por esta lista que o
# CALCULATE sabe se o argumento é um filtro-tabela ou uma condição booleana.
FN_TABELA = {'FILTER', 'CALCULATETABLE', 'SELECTCOLUMNS', 'ADDCOLUMNS',
             'SUMMARIZE', 'TOPN', 'DISTINCT', 'VALUES', 'ROW', 'UNION', 'ALL'}


class DaxNaoSuportado(Exception):
    """Consulta fora do subconjunto conhecido. Erra alto, de propósito."""


# ── Fixtures ──────────────────────────────────────────────────────────────
_CACHE = {}    # nome → (mtime do arquivo, linhas)


def _carregar(tabela, dataset):
    """Tabela do dataset pedido. `conhecimentos_emitidos` tem uma por dataset.

    O cache vale enquanto o arquivo não muda: a atualização diária regera as fixtures
    com o servidor no ar, e um cache eterno mostraria a base do dia do build até o
    próximo restart (foi o que deixou a vitrine de produção parada em 24/09/2026)."""
    for nome in (f'{tabela}.{dataset}', tabela):
        caminho = os.path.join(FIXTURES, f'{nome}.json')
        if not os.path.exists(caminho):
            continue
        mtime = os.path.getmtime(caminho)
        if nome in _CACHE and _CACHE[nome][0] == mtime:
            return _CACHE[nome][1]
        with open(caminho, encoding='utf-8') as fh:
            _CACHE[nome] = (mtime, json.load(fh))
        return _CACHE[nome][1]
    raise DaxNaoSuportado(f'sem fixture para a tabela {tabela!r} (dataset {dataset})')


# ── Tokenizador ───────────────────────────────────────────────────────────
_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<tabcol>'[^']*'\[[^\]]+\])          # 'public x'[col]
  | (?P<tab>'[^']*')                        # 'public x'
  | (?P<col>\[[^\]]+\])                     # [alias]
  | (?P<str>"(?:[^"]|"")*")                 # "texto"
  | (?P<num>\d+\.\d+|\d+)
  | (?P<op><>|>=|<=|&&|\|\||[=<>+\-*/&])
  | (?P<punct>[(),{}])
  | (?P<name>[A-Za-z_][A-Za-z_0-9.]*)
""", re.X)


def _tokenizar(q):
    toks, i = [], 0
    while i < len(q):
        m = _TOKEN.match(q, i)
        if not m:
            raise DaxNaoSuportado(f'não sei ler a partir de: {q[i:i + 60]!r}')
        i = m.end()
        tipo = m.lastgroup
        if tipo != 'ws':
            toks.append((tipo, m.group()))
    toks.append(('fim', ''))
    return toks


# ── Parser (descendente recursivo) ────────────────────────────────────────
class P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def olhar(self):
        return self.t[self.i]

    def comer(self, valor=None):
        tipo, v = self.t[self.i]
        if valor is not None and v.upper() != valor.upper():
            raise DaxNaoSuportado(f'esperava {valor!r}, veio {v!r}')
        self.i += 1
        return tipo, v

    def expr(self):
        no = self.e_and()
        while self.olhar()[1] == '||':
            self.comer()
            no = ('ou', no, self.e_and())
        return no

    def e_and(self):
        no = self.cmp()
        while self.olhar()[1] == '&&':
            self.comer()
            no = ('e', no, self.cmp())
        return no

    def cmp(self):
        no = self.concat()
        tipo, v = self.olhar()
        if v in ('=', '<>', '>=', '<=', '>', '<'):
            self.comer()
            return ('cmp', v, no, self.concat())
        if tipo == 'name' and v.upper() == 'IN':
            self.comer()
            return ('in', no, self.concat())
        return no

    def concat(self):
        no = self.soma()
        while self.olhar()[1] == '&':
            self.comer()
            no = ('concat', no, self.soma())
        return no

    def soma(self):
        no = self.mult()
        while self.olhar()[1] in ('+', '-'):
            op = self.comer()[1]
            no = ('arit', op, no, self.mult())
        return no

    def mult(self):
        no = self.unario()
        while self.olhar()[1] in ('*', '/'):
            op = self.comer()[1]
            no = ('arit', op, no, self.unario())
        return no

    def unario(self):
        if self.olhar()[1] == '-':
            self.comer()
            return ('neg', self.unario())
        return self.primario()

    def primario(self):
        tipo, v = self.comer()
        if tipo == 'num':
            return ('lit', float(v) if '.' in v else int(v))
        if tipo == 'str':
            return ('lit', v[1:-1].replace('""', '"'))
        if tipo == 'tabcol':
            tab, col = v.split('[', 1)
            return ('campo', tab.strip("'"), col[:-1])
        if tipo == 'col':
            return ('campo', None, v[1:-1])
        if tipo == 'tab':
            return ('tabela', v.strip("'"))
        if v == '(':
            no = self.expr()
            self.comer(')')
            return no
        if v == '{':                      # lista literal: {"a","b"}
            itens = []
            while self.olhar()[1] != '}':
                itens.append(self.expr())
                if self.olhar()[1] == ',':
                    self.comer()
            self.comer('}')
            return ('lista', itens)
        if tipo == 'name':
            if self.olhar()[1] == '(':
                self.comer('(')
                args = []
                while self.olhar()[1] != ')':
                    args.append(self.expr())
                    if self.olhar()[1] == ',':
                        self.comer()
                self.comer(')')
                return ('fn', v.upper(), args)
            return ('nome', v)
        raise DaxNaoSuportado(f'token inesperado: {v!r}')


# ── Avaliação ─────────────────────────────────────────────────────────────
def _data(v):
    """Compara data com data, não string com data."""
    if isinstance(v, (datetime, date)):
        return v
    if isinstance(v, str):
        t = v.strip().replace('T', ' ')[:19]
        for f in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y'):
            try:
                return datetime.strptime(t, f)
            except ValueError:
                pass
    return None


def _cmp_par(a, b):
    """Iguala os tipos dos dois lados antes de comparar."""
    if isinstance(a, (datetime, date)) or isinstance(b, (datetime, date)):
        return _data(a), _data(b)
    if isinstance(a, (int, float)) and isinstance(b, str):
        try:
            return a, float(b)
        except ValueError:
            return str(a), b
    if isinstance(b, (int, float)) and isinstance(a, str):
        try:
            return float(a), b
        except ValueError:
            return a, str(b)
    return a, b


_FMT = [('YYYY', '%Y'), ('MM', '%m'), ('DD', '%d'), ('HH', '%H'), ('YY', '%y')]


class Ctx:
    """Contexto: as linhas da tabela corrente e a linha em avaliação."""

    def __init__(self, dataset, linhas=None, linha=None, filtros=None, tabela=None):
        self.dataset = dataset
        # None = SEM contexto de filtro (topo da consulta); [] = contexto vazio
        # (o filtro não casou nada). Os dois eram a mesma coisa aqui, e o atalho
        # "sem contexto soma a tabela inteira" transformava mês sem movimento na
        # soma de TODOS os meses: a receita da DRE do ano inteiro saía 9x maior
        # que a base, com a despesa certa ao lado. Vazio tem de somar zero.
        self.linhas = linhas
        self.linha = linha
        # De QUAL tabela `linhas` veio. Sem isto, um `SUMX('t', ...)` dentro de um
        # SUMMARIZE recarregava 't' inteira a cada grupo em vez de somar só as
        # linhas do grupo — o total saía multiplicado pelo nº de grupos. Foi o
        # que inflou a retenção em ~80x e o KM em 24 milhões na aba Veículos.
        self.tabela = tabela
        # Filtros que ainda vão DESCER até a tabela base. O CALCULATETABLE do
        # DAX filtra o contexto e só então avalia a expressão; aplicar depois
        # (no resultado do SUMMARIZE) não acha a coluna — ela já foi agrupada —
        # e derruba tudo. Era o que zerava as despesas da DRE.
        self.filtros = filtros or []


def _campo(no, ctx):
    _, _tab, col = no
    if ctx.linha is None:
        return None
    if col in ctx.linha:
        return ctx.linha[col]
    # SELECTCOLUMNS renomeia; o alias pode ter vindo com o nome da tabela junto.
    for k in ctx.linha:
        if k.split('[')[-1].rstrip(']') == col:
            return ctx.linha[k]
    return None


def esc(no, ctx):
    """Avalia uma expressão escalar contra `ctx.linha`."""
    t = no[0]
    if t == 'lit':
        return no[1]
    if t == 'campo':
        return _campo(no, ctx)
    if t == 'neg':
        v = esc(no[1], ctx)
        return -v if isinstance(v, (int, float)) else v
    if t == 'concat':
        return f'{_txt(esc(no[1], ctx))}{_txt(esc(no[2], ctx))}'
    if t == 'e':
        return bool(esc(no[1], ctx)) and bool(esc(no[2], ctx))
    if t == 'ou':
        return bool(esc(no[1], ctx)) or bool(esc(no[2], ctx))
    if t == 'cmp':
        a, b = _cmp_par(esc(no[2], ctx), esc(no[3], ctx))
        if a is None or b is None:
            return no[1] == '<>'
        try:
            return {'=': a == b, '<>': a != b, '>': a > b,
                    '<': a < b, '>=': a >= b, '<=': a <= b}[no[1]]
        except TypeError:
            return False
    if t == 'in':
        alvo = esc(no[1], ctx)
        vals = no[2]
        if vals[0] == 'lista':
            return any(_iguais(alvo, esc(x, ctx)) for x in vals[1])
        if vals[0] == 'fn' and vals[1] in FN_TABELA:
            # `x IN VALUES('t'[c])`: o lado direito é uma TABELA de uma coluna.
            # Avaliado como escalar, caía em "função não implementada" — e o
            # erro só aparecia quando alguém rodava o job que usa essa forma.
            linhas = tab(vals, ctx) or []
            for r in linhas:
                for v in r.values():
                    if _iguais(alvo, v):
                        return True
            return False
        return _iguais(alvo, esc(vals, ctx))
    if t == 'arit':
        a, b = esc(no[2], ctx), esc(no[3], ctx)
        a = a if isinstance(a, (int, float)) else 0
        b = b if isinstance(b, (int, float)) else 0
        return {'+': a + b, '-': a - b, '*': a * b,
                '/': (a / b if b else 0)}[no[1]]
    if t == 'lista':
        return [esc(x, ctx) for x in no[1]]
    if t == 'fn':
        return _fn(no[1], no[2], ctx)
    if t == 'tabela':
        return no[1]
    if t == 'nome':
        if no[1].upper() in ('TRUE', 'FALSE'):
            return no[1].upper() == 'TRUE'
        if no[1].upper() == 'BLANK':
            return None
        raise DaxNaoSuportado(f'nome solto: {no[1]}')
    raise DaxNaoSuportado(f'nó desconhecido: {t}')


def _txt(v):
    if v is None:
        return ''
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _iguais(a, b):
    a, b = _cmp_par(a, b)
    return a == b


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _fn(nome, args, ctx):
    if nome == 'DATE':
        return datetime(int(esc(args[0], ctx)), int(esc(args[1], ctx)),
                        int(esc(args[2], ctx)))
    if nome == 'FORMAT':
        v, f = esc(args[0], ctx), esc(args[1], ctx)
        d = _data(v)
        if d is None:
            return _txt(v)
        pat = str(f)
        for de, para in _FMT:
            pat = pat.replace(de, para)
        return d.strftime(pat)
    if nome in ('LEFT', 'RIGHT'):
        s, n = _txt(esc(args[0], ctx)), int(esc(args[1], ctx))
        return s[:n] if nome == 'LEFT' else s[-n:]
    if nome == 'LEN':
        return len(_txt(esc(args[0], ctx)))
    if nome in ('UPPER', 'TRIM'):
        s = _txt(esc(args[0], ctx))
        return s.upper() if nome == 'UPPER' else s.strip()
    if nome == 'SEARCH':
        agulha, palheiro = _txt(esc(args[0], ctx)).upper(), _txt(esc(args[1], ctx)).upper()
        achou = palheiro.find(agulha)
        if achou >= 0:
            return achou + 1
        return esc(args[3], ctx) if len(args) > 3 else 0
    if nome == 'COALESCE':
        for a in args:
            v = esc(a, ctx)
            if v not in (None, ''):
                return v
        return None
    if nome == 'IF':
        return esc(args[1], ctx) if esc(args[0], ctx) else (
            esc(args[2], ctx) if len(args) > 2 else None)
    if nome == 'DIVIDE':
        a, b = _num(esc(args[0], ctx)), _num(esc(args[1], ctx))
        return a / b if b else (esc(args[2], ctx) if len(args) > 2 else 0)
    if nome in ('ROUND', 'INT', 'VALUE', 'ABS'):
        v = esc(args[0], ctx)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return 0
        if nome == 'ROUND':
            return round(v, int(esc(args[1], ctx)) if len(args) > 1 else 0)
        return int(v) if nome == 'INT' else (abs(v) if nome == 'ABS' else v)
    if nome == 'BLANK':
        return None

    # ── agregações: rodam sobre uma tabela, não sobre a linha ──
    if nome == 'COUNTROWS':
        return len(tab(args[0], ctx) or [])
    if nome in ('SUM', 'MIN', 'MAX', 'AVERAGE', 'DISTINCTCOUNT', 'COUNT'):
        no = args[0]
        if no[0] != 'campo':
            raise DaxNaoSuportado(f'{nome} espera uma coluna')
        linhas = ctx.linhas
        if linhas is None and no[1]:
            # Só quando NÃO há contexto nenhum (topo da consulta) `SUM('t'[c])`
            # é a soma da tabela inteira. Contexto vazio soma zero, como no DAX.
            linhas = _carregar(no[1].replace('public ', ''), ctx.dataset)
        linhas = linhas or []
        vals = [_campo(no, Ctx(ctx.dataset, linhas, r)) for r in linhas]
        return _agregar(nome, vals)
    if nome in ('SUMX', 'MINX', 'MAXX', 'AVERAGEX', 'COUNTX'):
        linhas = tab(args[0], ctx) or []
        nb = _nome_tabela(args[0]) or ctx.tabela
        vals = [esc(args[1], Ctx(ctx.dataset, linhas, r, ctx.filtros, nb))
                for r in linhas]
        return _agregar(nome.rstrip('X'), vals)
    if nome == 'MID':
        s = _txt(esc(args[0], ctx))
        ini = int(esc(args[1], ctx))
        n = int(esc(args[2], ctx))
        return s[ini - 1:ini - 1 + n]          # DAX conta a partir de 1
    if nome == 'SUBSTITUTE':
        return _txt(esc(args[0], ctx)).replace(_txt(esc(args[1], ctx)),
                                               _txt(esc(args[2], ctx)))
    if nome == 'CONCATENATE':
        return _txt(esc(args[0], ctx)) + _txt(esc(args[1], ctx))
    if nome == 'CONTAINSSTRING':
        return _txt(esc(args[1], ctx)).upper() in _txt(esc(args[0], ctx)).upper()
    if nome == 'FIND':
        achou = _txt(esc(args[1], ctx)).find(_txt(esc(args[0], ctx)))
        if achou >= 0:
            return achou + 1
        return esc(args[3], ctx) if len(args) > 3 else 0
    if nome in ('YEAR', 'MONTH', 'DAY', 'HOUR'):
        dt = _data(esc(args[0], ctx))
        if dt is None:
            return 0
        return {'YEAR': dt.year, 'MONTH': dt.month,
                'DAY': dt.day, 'HOUR': dt.hour}[nome]
    if nome == 'TODAY' or nome == 'NOW':
        return datetime.now()
    if nome == 'ISBLANK':
        return esc(args[0], ctx) in (None, '')
    if nome == 'NOT':
        return not esc(args[0], ctx)
    if nome == 'SWITCH':
        chave = esc(args[0], ctx)
        resto = args[1:]
        for i in range(0, len(resto) - 1, 2):
            if _iguais(chave, esc(resto[i], ctx)):
                return esc(resto[i + 1], ctx)
        return esc(resto[-1], ctx) if len(resto) % 2 else None
    if nome == 'LOOKUPVALUE':
        # LOOKUPVALUE(<col resultado>, <col busca>, <valor>, ...): procura a
        # PRIMEIRA linha que casa em todos os pares e devolve a coluna pedida.
        alvo_col = args[0]
        if alvo_col[0] != 'campo':
            raise DaxNaoSuportado('LOOKUPVALUE espera coluna no 1º argumento')
        linhas = _carregar(alvo_col[1].replace('public ', ''), ctx.dataset)
        pares = list(zip(args[1::2], args[2::2]))
        for r in linhas:
            c = Ctx(ctx.dataset, linhas, r)
            if all(_iguais(_campo(col, c), esc(val, ctx)) for col, val in pares):
                return _campo(alvo_col, c)
        return None
    if nome == 'CALCULATE':
        # Os modificadores do CALCULATE vêm de duas formas: condição booleana
        # (`t[col] = "x"`) ou TABELA já filtrada (`FILTER(t, ...)`). Tratar a
        # segunda como booleana filtrava a tabela corrente — que no topo da
        # consulta é vazia — e a medida voltava zero. Era o que zerava a receita
        # da DRE.
        linhas, pend = ctx.linhas, list(ctx.filtros)
        for a in args[1:]:
            if a[0] == 'fn' and a[1] in FN_TABELA:
                linhas = tab(a, ctx)
            else:
                alvos = _tabelas_citadas(a)
                pend.append((next(iter(alvos)) if len(alvos) == 1 else None, a))
                if linhas:
                    linhas = _aplicar(a, linhas, ctx)
        return esc(args[0], Ctx(ctx.dataset, linhas, ctx.linha, pend))
    raise DaxNaoSuportado(f'função escalar não implementada: {nome}')


def _agregar(nome, vals):
    nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if nome == 'SUM':
        return sum(nums)
    if nome == 'AVERAGE':
        return sum(nums) / len(nums) if nums else 0
    if nome == 'COUNT':
        return len([v for v in vals if v not in (None, '')])
    if nome == 'DISTINCTCOUNT':
        return len({v for v in vals if v not in (None, '')})
    limpos = nums or [v for v in vals if v not in (None, '')]
    if not limpos:
        return None
    return min(limpos) if nome == 'MIN' else max(limpos)


def _aplicar(cond, linhas, ctx):
    """Condição de CALCULATE/CALCULATETABLE: filtra as linhas."""
    linhas = linhas or []
    return [r for r in linhas if esc(cond, Ctx(ctx.dataset, linhas, r))]


# ── Funções de tabela ─────────────────────────────────────────────────────
def _nome_tabela(no):
    """A tabela base de um nó de tabela, quando dá para saber."""
    if not isinstance(no, tuple):
        return None
    if no[0] == 'tabela':
        return no[1]
    if no[0] == 'fn' and no[1] in FN_TABELA:
        for a in no[2]:
            n = _nome_tabela(a)
            if n:
                return n
    return None


def _tabelas_citadas(no, achadas=None):
    """Nomes de tabela que uma expressão menciona (para saber onde o filtro cai)."""
    achadas = achadas if achadas is not None else set()
    if isinstance(no, tuple):
        if no[0] == 'campo' and no[1]:
            achadas.add(no[1])
        for x in no[1:]:
            _tabelas_citadas(x, achadas)
    elif isinstance(no, list):
        for x in no:
            _tabelas_citadas(x, achadas)
    return achadas


def tab(no, ctx):
    """Avalia um nó que devolve LINHAS."""
    t = no[0]
    if t == 'tabela':
        # Já estamos iterando esta tabela (grupo de um SUMMARIZE, linha de um
        # FILTER): o contexto manda, como em DAX.
        if ctx.tabela == no[1] and ctx.linhas is not None:
            return ctx.linhas
        linhas = _carregar(no[1].replace('public ', ''), ctx.dataset)
        for alvo, cond in ctx.filtros:
            if alvo is None or alvo == no[1]:
                linhas = [r for r in linhas
                          if esc(cond, Ctx(ctx.dataset, linhas, r))]
        return linhas
    if t != 'fn':
        raise DaxNaoSuportado(f'esperava tabela, veio {t}')

    nome, args = no[1], no[2]
    if nome == 'FILTER':
        base = tab(args[0], ctx) or []
        nome_base = _nome_tabela(args[0])
        return [r for r in base
                if esc(args[1], Ctx(ctx.dataset, base, r, ctx.filtros, nome_base))]
    if nome == 'CALCULATETABLE':
        pend = list(ctx.filtros)
        extra = []
        for a in args[1:]:
            if a[0] == 'fn' and a[1] in FN_TABELA:
                extra.append(tab(a, ctx))      # filtro-tabela: resolve agora
            else:
                alvos = _tabelas_citadas(a)
                pend.append((next(iter(alvos)) if len(alvos) == 1 else None, a))
        linhas = tab(args[0], Ctx(ctx.dataset, ctx.linhas, ctx.linha, pend)) or []
        for sub in extra:
            chaves = {id(r) for r in sub}
            linhas = [r for r in linhas if id(r) in chaves] or linhas
        return linhas
    if nome == 'SELECTCOLUMNS':
        base = tab(args[0], ctx) or []
        pares = list(zip(args[1::2], args[2::2]))
        out = []
        for r in base:
            c = Ctx(ctx.dataset, base, r)
            out.append({f'[{esc(a, c)}]': esc(b, c) for a, b in pares})
        return out
    if nome == 'ADDCOLUMNS':
        base = tab(args[0], ctx) or []
        pares = list(zip(args[1::2], args[2::2]))
        out = []
        for r in base:
            c = Ctx(ctx.dataset, base, r)
            novo = dict(r)
            for a, b in pares:
                novo[f'[{esc(a, c)}]'] = esc(b, c)
            out.append(novo)
        return out
    if nome == 'SUMMARIZE':
        base = tab(args[0], ctx) or []
        grupos, medidas = [], []
        i = 1
        while i < len(args):
            if args[i][0] == 'campo':
                grupos.append(args[i])
                i += 1
            else:
                medidas.append((args[i], args[i + 1]))
                i += 2
        nome_base = _nome_tabela(args[0])
        baldes = {}
        for r in base:
            c = Ctx(ctx.dataset, base, r, ctx.filtros, nome_base)
            chave = tuple(_campo(g, c) for g in grupos)
            baldes.setdefault(chave, []).append(r)
        out = []
        for chave, linhas in baldes.items():
            linha = {f'[{g[2]}]': v for g, v in zip(grupos, chave)}
            # A medida enxerga SÓ as linhas do grupo — é o contexto de filtro do
            # DAX. Passar a tabela base é o que faz `SUMX('t', ...)` aqui dentro
            # somar o grupo em vez de recarregar a tabela.
            for alias, expr in medidas:
                c = Ctx(ctx.dataset, linhas, linhas[0], ctx.filtros, nome_base)
                linha[f'[{esc(alias, c)}]'] = esc(expr, c)
            out.append(linha)
        return out
    if nome == 'TOPN':
        n = int(esc(args[0], ctx))
        return (tab(args[1], ctx) or [])[:n]
    if nome in ('DISTINCT', 'VALUES'):
        base = (tab(args[0], ctx) or []) if args[0][0] != 'campo' else None
        if base is None:                      # VALUES('t'[col])
            _, tnome, col = args[0]
            if ctx.tabela == tnome and ctx.linhas is not None:
                linhas = ctx.linhas           # distintos DENTRO do contexto
            else:
                linhas = _carregar(tnome.replace('public ', ''), ctx.dataset)
            vistos = sorted({r.get(col) for r in linhas}, key=lambda x: (x is None, str(x)))
            return [{f'[{col}]': v} for v in vistos]
        vistos, out = set(), []
        for r in base:
            k = tuple(sorted((str(a), str(b)) for a, b in r.items()))
            if k not in vistos:
                vistos.add(k)
                out.append(r)
        return out
    if nome == 'ROW':
        pares = list(zip(args[0::2], args[1::2]))
        # `None`, não `[]`: o ROW não estabelece contexto de filtro, então uma
        # agregação dentro dele sem CALCULATE é sobre a tabela inteira.
        c = Ctx(ctx.dataset, None, None, ctx.filtros, ctx.tabela)
        return [{f'[{esc(a, c)}]': esc(b, c) for a, b in pares}]
    if nome == 'ALL':
        return tab(args[0], ctx) if args else []
    if nome == 'UNION':
        out = []
        for a in args:
            out.extend(tab(a, ctx))
        return out
    raise DaxNaoSuportado(f'função de tabela não implementada: {nome}')


# ── Entrada ───────────────────────────────────────────────────────────────
def executar(query, dataset='main'):
    """Devolve a resposta no formato do executeQueries do Power BI."""
    q = query.strip()
    if not q.upper().startswith('EVALUATE'):
        raise DaxNaoSuportado('consulta sem EVALUATE')
    corpo = q[len('EVALUATE'):].strip()
    # ORDER BY não muda o conteúdo, e nenhuma tela do app depende da ordem do
    # Power BI (todas ordenam no cliente). Cortar é mais honesto que fingir.
    corpo = re.split(r'\bORDER\s+BY\b', corpo, flags=re.I)[0].strip()

    p = P(_tokenizar(corpo))
    no = p.expr()
    if p.olhar()[0] != 'fim':
        raise DaxNaoSuportado(f'sobrou depois da expressão: {p.olhar()[1]!r}')

    linhas = tab(no, Ctx(dataset)) or []
    return {'results': [{'tables': [{'rows': linhas}]}]}


def disponivel():
    return os.path.isdir(FIXTURES) and bool(os.listdir(FIXTURES))
