"""Gate da base demo: as fixtures passam pelas RÉGUAS DO PRÓPRIO PROJETO.

Conferir a base contra uma régua escrita aqui não prova nada — provaria só que
duas coisas que eu escrevi concordam. O que vale é jogar a fixture na função pura
que roda em produção e ver se ela enxerga o que foi plantado, nem mais nem menos.

Hoje cobre:
  * **CIOT** (`ciot_conferencia.conferir`, função pura) — tem de achar
    exatamente os defeitos plantados;
  * **integridade da cadeia** — manifesto ↔ CTRB ↔ CTe ↔ Auditoria;
  * **contrato de coluna** — toda coluna da tabela real existe na fixture.

Uso:  python -X utf8 _seed_demo/_valida.py
"""

import json
import os
import sys
from collections import Counter
from datetime import date

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

FIXTURES = os.path.join(AQUI, 'fixtures')
SHAPE = os.path.join(AQUI, 'shape')

falhas = []
avisos = []


def ok(cond, msg, detalhe=''):
    print(('  ok   ' if cond else '  FALHA ') + msg + (f' — {detalhe}' if detalhe else ''))
    if not cond:
        falhas.append(msg)


def carregar(nome):
    with open(os.path.join(FIXTURES, f'{nome}.json'), encoding='utf-8') as fh:
        return json.load(fh)


def main():
    man = carregar('manifestos')
    ctrb = carregar('ctrbs_oss')
    cte = carregar('conhecimentos_emitidos.main')
    aud = carregar('Auditoria Receita')

    print('\n1. Contrato de coluna (fixture × tabela real)')
    for nome in sorted(os.listdir(FIXTURES)):
        tab = nome[:-5]
        cam = os.path.join(SHAPE, f'{tab}.json')
        if not os.path.exists(cam):
            continue
        with open(cam, encoding='utf-8') as fh:
            reais = set(json.load(fh)['colunas'])
        linhas = carregar(tab)
        if not linhas:
            continue
        tem = set(linhas[0])
        faltam = reais - tem
        sobram = tem - reais
        ok(not faltam and not sobram, f'{tab}',
           (f'faltam {sorted(faltam)[:4]}' if faltam else '')
           + (f' sobram {sorted(sobram)[:4]}' if sobram else f'{len(tem)} colunas'))

    print('\n2. Integridade da cadeia')
    chaves_ctrb = {c['ctrb'][:9] for c in ctrb}
    mf_com_ctrb = [m for m in man if m['CHAVE_CTRB']]
    orfaos = [m for m in mf_com_ctrb if m['CHAVE_CTRB'] not in chaves_ctrb]
    ok(not orfaos, 'todo manifesto com CHAVE_CTRB aponta para um CTRB existente',
       f'{len(orfaos)} órfão(s)')

    mfs = {m['CHAVE_MANIFESTO'] for m in man}
    soltos = [c for c in cte if c['primeiro_manifesto'] not in mfs]
    ok(not soltos, 'todo CTe aponta para um manifesto existente', f'{len(soltos)} solto(s)')

    ctrbs_set = {c['ctrb'] for c in ctrb}
    fora = [a for a in aud if a['CTRB'] not in ctrbs_set]
    ok(not fora, 'toda linha da Auditoria tem CTRB', f'{len(fora)} fora')
    ok(len(aud) == len(ctrb), 'Auditoria Receita é 1:1 com ctrbs_oss (como na base real)',
       f'{len(aud)} × {len(ctrb)}')

    print('\n3. Régua do CIOT (função pura do projeto)')
    import ciot_conferencia as cc
    ctrbs_in = [{'ctrb': c['ctrb'], 'emissao': c['emissao'],
                 'Tipo Operação': c['Tipo Operação'], 'propriedade': c['propriedade'],
                 'ciot': c['ciot'], 'tabela_antt': c['tabela_antt'],
                 'placa_cavalo': c['placa_cavalo'], 'placa_carreta': c['placa_carreta'],
                 'cpf_motorista': c['cpf_motorista'], 'motorista': c['motorista'],
                 'manifesto': c['manifesto'], 'observacao': c['observacao'],
                 'cidade_uf_origem': c['cidade_uf_origem'],
                 'cidade_uf_destino': c['cidade_uf_destino']} for c in ctrb]
    mans_in = [{'mf': m['CHAVE_MANIFESTO'], 'd': m['data_emissao'],
                'kc': m['CHAVE_CTRB'], 'nc': m['numero_ctrb_os'],
                'cav': m['placa_cavalo'], 'car': m['placa_carreta'],
                'cpf': m['cpf_motorista'], 'motorista': m['nome_motorista']}
               for m in man]

    pend, resumo = cc.conferir(ctrbs_in, mans_in, date(2026, 9, 1))
    print(f'     resumo da régua: {dict(resumo)}')

    # os nomes são os da régua do projeto: `sem_ciot`, `ciot_erro`, `manifesto_sem_ctrb`
    esperado = {'sem_ciot': 4, 'ciot_erro': 3, 'manifesto_sem_ctrb': 2}
    achado = Counter(p['tipo'] for p in pend)
    for tipo, n in esperado.items():
        got = achado.get(tipo, 0)
        ok(got == n, f'CIOT acha os {n} "{tipo}" plantados', f'achou {got}')
    extras = {t: n for t, n in achado.items() if t not in esperado}
    if extras:
        avisos.append(f'pendências não plantadas que a régua encontrou: {extras}')
        print(f'     aviso: a régua achou também {extras}')

    print('\n4. Trilha de GPS')
    pos = carregar('embarques_posicoes_historico')
    # A tabela tem UNIQUE(placa, data_posicao): duplicata não é detalhe estético,
    # é a carga abortando no meio. Duas viagens da mesma carreta podem se
    # sobrepor no tempo, então isto precisa ser testado, não suposto.
    chaves = Counter((p['placa'], p['data_posicao']) for p in pos)
    dups = [k for k, n in chaves.items() if n > 1]
    ok(not dups, 'sem duplicata em (placa, data_posicao) — a tabela tem UNIQUE',
       f'{len(dups)} duplicada(s)')

    # Odômetro é cumulativo NO APARELHO: se andar para trás, a consolidação
    # diária descarta o dia (delta negativo vira NULL) e o km/L some.
    por_placa = {}
    for p in sorted(pos, key=lambda x: (x['placa'], x['data_posicao'])):
        ant = por_placa.get(p['placa'])
        if ant is not None and p['odometer'] < ant:
            falhas.append('odômetro retrocede')
            break
        por_placa[p['placa']] = p['odometer']
    else:
        ok(True, 'odômetro cumulativo e crescente por placa', f'{len(por_placa)} placas')

    import pgr
    limiar = getattr(pgr, 'LIMIAR', 95)
    teto = getattr(pgr, 'TETO', 130)
    acima = [p for p in pos if limiar < p['velocidade'] <= teto]
    ok(len(acima) > 20, f'há registros entre o limiar ({limiar}) e o teto ({teto}) do PGR',
       f'{len(acima)} registro(s), {len({p["placa"] for p in acima})} placa(s)')
    ok(not [p for p in pos if p['velocidade'] > teto],
       'nenhuma leitura acima do teto anti-ruído (seria equipamento travado)')

    print('\n5. Volume por mês (a demo precisa de movimento no mês corrente)')
    por_mes = Counter(m['data_emissao'][:7] for m in man)
    for k in sorted(por_mes):
        print(f'     {k}: {por_mes[k]:>4d} manifestos')
    ok(por_mes.get('2026-09', 0) > 50, 'mês corrente tem movimento')

    print()
    for a in avisos:
        print(f'aviso: {a}')
    if falhas:
        print(f'\n{len(falhas)} FALHA(S): ' + '; '.join(falhas))
        return 1
    print('gate verde')
    return 0


if __name__ == '__main__':
    sys.exit(main())
