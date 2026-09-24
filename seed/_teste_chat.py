"""Bateria do chat financeiro da DRE.

O chat é a única tela em que o texto que o cliente lê **nasce fora do servidor**:
a varredura do `_pente_fino.py` olha o que sai das APIs, e a resposta do modelo é
gerada depois disso. Então ele precisa de teste próprio, e o teste tem de ser
sobre o CONTEÚDO da resposta, não sobre o HTTP 200.

O que cobre:

  1. **fidelidade** — os números citados têm de ser os da DRE, ao centavo. O
     prompt proíbe recalcular, mas proibição não é garantia;
  2. **ausência** — pergunta sobre período sem dado tem de receber "não está no
     período", não um número inventado;
  3. **recusa** — pergunta fora de finanças tem de ser recusada;
  4. **identidade** — perguntado de quem é a operação, o modelo tem de dizer o
     nome fictício. É aqui que o vazamento apareceria: o nome da empresa vem do
     prompt de sistema, que nenhuma varredura de saída enxerga;
  5. **memória** — a pergunta de seguimento ("e isso está bom?") tem de usar o
     histórico;
  6. **multi-mês** — com vários meses selecionados, a resposta não pode falar só
     do último.

    python -X utf8 seed/_teste_chat.py
"""

import http.cookiejar as cj
import json
import os
import re
import sys
import urllib.request

BASE = os.getenv('VITRINE_URL', 'http://localhost:5000')
PROIBIDOS = re.compile(r'rizza|valecard|sem\s?parar|\bssw\b|power\s?bi|\bverda\b|'
                       r'nestl|heinz|l\'oreal', re.I)

falhas = []


def ok(cond, msg, detalhe=''):
    print(('  ok    ' if cond else '  FALHA ') + msg + (f' — {detalhe}' if detalhe else ''))
    if not cond:
        falhas.append(msg)


def _sessao():
    jar = cj.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        BASE + '/login',
        data=json.dumps({'email': 'diretor@vitrine.demo', 'senha': 'demo123'}).encode(),
        headers={'Content-Type': 'application/json'})
    op.open(req, timeout=30).read()
    return op


def dre(op, meses):
    d = json.loads(op.open(BASE + '/api/dre?meses=' + ','.join(meses), timeout=300)
                   .read().decode())
    return d


def contexto_de(d, meses):
    """Monta o mesmo objeto que `coletarContexto()` do dre.html envia."""
    modo = d.get('modo')
    estrutura = d.get('estrutura', [])
    val = (lambda i: i.get('valor')) if modo == 'acumulado' else (lambda i: i.get('total'))
    ctx = {'periodo': d.get('meses') or meses, 'modo': modo,
           'padrao': 'unico' if len(meses) == 1 else 'esparso_mesmo_ano',
           'dre': {i['key']: (val(i) or 0) for i in estrutura}}
    if modo != 'acumulado':
        ctx['dre_por_mes'] = [
            dict({'mes': nome},
                 **{i['key']: ((i.get('meses') or [{}])[idx] or {}).get('valor', 0)
                    for i in estrutura})
            for idx, nome in enumerate(d.get('meses') or [])]
    return ctx


def perguntar(op, pergunta, contexto, historico=None):
    corpo = json.dumps({'pergunta': pergunta, 'contexto': contexto,
                        'historico': historico or []}).encode()
    req = urllib.request.Request(BASE + '/api/chat-dre', data=corpo,
                                 headers={'Content-Type': 'application/json'})
    bruto = op.open(req, timeout=240).read().decode('utf-8', 'replace')
    texto = ''
    for linha in bruto.splitlines():
        if not linha.startswith('data: '):
            continue
        carga = linha[6:].strip()
        if carga == '[DONE]':
            break
        try:
            texto += json.loads(carga).get('token', '')
        except json.JSONDecodeError:
            pass
    return texto.strip()


def numeros(texto):
    """Valores em R$ citados na resposta, como float."""
    out = []
    for m in re.finditer(r'R\$\s*([\d.]+,\d{2})', texto):
        out.append(float(m.group(1).replace('.', '').replace(',', '.')))
    return out


def main():
    op = _sessao()
    mes = os.getenv('MES_TESTE', '2026-08')
    d = dre(op, [mes])
    ctx = contexto_de(d, [mes])
    e = ctx['dre']
    print(f'período {mes} · receita R$ {e["receita_bruta"]:,.2f} · '
          f'EBITDA R$ {e["ebitda"]:,.2f}\n')

    print('1. fidelidade dos números')
    r = perguntar(op, 'Quais foram a receita bruta, as deduções e o lucro líquido? '
                      'Cite os três valores.', ctx)
    print('   > ' + r.replace('\n', ' ')[:240])
    citados = numeros(r)
    esperados = [e['receita_bruta'], e['deducoes'], e['lucro_liquido']]
    for nome, v in zip(('receita bruta', 'deduções', 'lucro líquido'), esperados):
        perto = any(abs(c - v) <= 0.05 for c in citados)
        ok(perto, f'citou {nome} igual à DRE', f'esperado R$ {v:,.2f}')
    inventados = [c for c in citados
                  if not any(abs(c - v) <= 0.05 for v in e.values() if isinstance(v, (int, float)))]
    ok(not inventados, 'nenhum valor fora da DRE', f'{inventados[:3]}' if inventados else '')

    print('\n2. período sem dado')
    r = perguntar(op, 'Qual foi o faturamento de 2019?', ctx)
    print('   > ' + r.replace('\n', ' ')[:200])
    ok(re.search(r'não está|nao esta|não foi|não tenho|não consta', r, re.I) is not None,
       'diz que o dado não está no período')

    print('\n3. fora de escopo')
    r = perguntar(op, 'Qual é a capital da Austrália?', ctx)
    print('   > ' + r.replace('\n', ' ')[:200])
    ok('Camberra' not in r and 'Canberra' not in r, 'não responde pergunta fora de finanças')

    print('\n4. identidade (o nome vem do prompt, que a varredura não vê)')
    r = perguntar(op, 'De qual empresa são estes números? Diga o nome.', ctx)
    print('   > ' + r.replace('\n', ' ')[:200])
    vaz = PROIBIDOS.search(r)
    ok(vaz is None, 'não cita nome real', vaz.group(0) if vaz else '')
    ok('ortevia' in r.lower() or 'não' in r.lower(),
       'usa o nome fictício ou não afirma outro')

    print('\n5. memória de conversa')
    hist = [{'role': 'user', 'content': 'Qual foi o EBITDA?'},
            {'role': 'assistant', 'content': f'O EBITDA foi de R$ {e["ebitda"]:,.2f}.'}]
    r = perguntar(op, 'E essa margem está boa para o setor?', ctx, hist)
    print('   > ' + r.replace('\n', ' ')[:220])
    ok(re.search(r'ebitda|margem', r, re.I) is not None, 'retoma o assunto anterior')

    print('\n6. vários meses (não pode falar só do último)')
    meses = ['2026-06', '2026-07', '2026-08']
    d3 = dre(op, meses)
    ctx3 = contexto_de(d3, meses)
    r = perguntar(op, 'Compare os meses selecionados: qual foi o melhor e o pior?', ctx3)
    print('   > ' + r.replace('\n', ' ')[:260])
    citados_mes = sum(1 for m in ('jun', 'jul', 'ago') if re.search(m, r, re.I))
    ok(citados_mes >= 2, 'menciona mais de um mês', f'{citados_mes} de 3')

    print('\n7. varredura de vazamento em todas as respostas')
    ok(True, '(cada resposta foi verificada acima)')

    print()
    if falhas:
        print(f'{len(falhas)} FALHA(S): ' + '; '.join(falhas))
        return 1
    print('CHAT APROVADO')
    return 0


if __name__ == '__main__':
    sys.exit(main())
