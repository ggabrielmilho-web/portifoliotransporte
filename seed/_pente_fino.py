"""Varre a vitrine inteira atrás de vazamento de nome real.

Por que existe: procurar no CÓDIGO não prova nada. O nome pode chegar à tela
pela fixture, pelo banco, por um `print`, pelo cabeçalho de uma imagem de
WhatsApp ou por um texto de alerta montado em Python — e foi exatamente assim
que "Manifesto em placa que não é da frota Rizza" passou por uma varredura que
só olhou HTML e JS.

Então este script olha o que **sai**: cada página renderizada, cada resposta de
API, cada arquivo estático servido e cada fixture. Se o nome não aparece em
nenhum desses, ele não chega ao cliente.

    python -X utf8 seed/_pente_fino.py
"""

import json
import os
import re
import sys
import urllib.request
import http.cookiejar as cj

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
BASE = os.getenv('VITRINE_URL', 'http://localhost:5000')

# Nome que não pode sair daqui. A busca é case-insensitive; `\b` evita que "3S"
# case dentro de placa ou número de documento.
PROIBIDOS = [
    r'rizza', r'rizzalog', r'carvalhoia',
    r'valecard', r'vale\s?card',
    r'sem\s?parar', r'semparar',
    r'\bssw\b', r'power\s?bi',
    # `(?<![\d.])` evita casar a duração `0.3s` de animação do CSS.
    r'(?<![\d.])\b3s\b',
    r'pamcard', r'autotrac', r'bvix', r'uazapi',
    # com fronteira, senão "verdade" conta como vazamento
    r'\bverda\b',
    r'nestl', r'heinz', r"l'oreal", r'loreal', r'\bmartins\b',
    r'winthor', r'sankhya', r'totvs',
]
RX = re.compile('|'.join(PROIBIDOS), re.I)

PAGINAS = ['/login', '/inicio', '/', '/tarifas', '/embarques', '/embarques/novo',
           '/embarques/relatorio', '/embarques/ordens', '/embarques/mapa',
           '/pgr', '/jornada', '/ciot', '/dre', '/dre/despesas',
           '/dre/conhecimentos', '/faturamento', '/veiculos', '/carbono',
           '/admin', '/sem-acesso']
ESTATICOS = ['/theme.css', '/nav-perms.js', '/report-filter.js', '/mapa-config.js']
APIS = [
    '/api/me', '/api/status', '/api/auditoria', '/api/tarifas',
    '/api/dre?meses=2026-08', '/api/dre/detalhamento?meses=2026-08',
    '/api/dre/despesas?start=2026-08-01&end=2026-09-30',
    '/api/dre/conhecimentos?start=2026-09-01&end=2026-09-30',
    '/api/faturamento/tomadores?ano=2026',
    '/api/veiculos/analise?dim=cavalo&meses=2026-08&tipos=Frota',
    '/api/veiculos/analise?dim=carreta&meses=2026-08&tipos=Frota,Agregado',
    '/api/jornada', '/api/ciot/pendencias?status=todas',
    '/api/pgr', '/api/pgr/opcoes', '/api/pgr?meses=2026-09',
    '/api/carbono', '/api/carbono/travadas',
    '/api/embarques/kpis', '/api/embarques/cargas', '/api/embarques/ordens',
    '/api/embarques/motoristas', '/api/embarques/veiculos',
    '/api/embarques/clientes', '/api/embarques/embarcadores',
    '/api/rastreamento/posicoes', '/api/rastreamento/health',
    '/api/rastreamento/log', '/api/admin/users', '/api/icms?origem=SP&destino=MG',
]


def achados(texto, origem, limite=4):
    out = []
    for m in RX.finditer(texto or ''):
        ini = max(0, m.start() - 55)
        trecho = re.sub(r'\s+', ' ', texto[ini:m.end() + 55])
        out.append((origem, m.group(0), trecho))
        if len(out) >= limite:
            out.append((origem, '…', f'(+ ocorrências além das {limite} primeiras)'))
            break
    return out


def main():
    jar = cj.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    req = urllib.request.Request(
        BASE + '/login',
        data=json.dumps({'email': 'diretor@vitrine.demo', 'senha': 'demo123'}).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        op.open(req, timeout=30).read()
    except Exception as e:
        print(f'não consegui logar em {BASE}: {e}')
        return 2

    todos = []

    print('1. páginas renderizadas')
    for p in PAGINAS:
        try:
            txt = op.open(BASE + p, timeout=120).read().decode('utf-8', 'replace')
        except Exception as e:
            print(f'   ?? {p}: {e}')
            continue
        h = achados(txt, f'página {p}')
        todos += h
        print(f'   {"!!" if h else "ok"} {p}')

    print('2. estáticos servidos')
    for p in ESTATICOS:
        try:
            txt = op.open(BASE + p, timeout=60).read().decode('utf-8', 'replace')
        except Exception:
            continue
        h = achados(txt, f'estático {p}')
        todos += h
        print(f'   {"!!" if h else "ok"} {p}')

    print('3. respostas de API')
    for a in APIS:
        try:
            txt = op.open(BASE + a, timeout=300).read().decode('utf-8', 'replace')
        except Exception as e:
            print(f'   ?? {a}: {str(e)[:60]}')
            continue
        h = achados(txt, f'api {a}')
        todos += h
        print(f'   {"!!" if h else "ok"} {a}')

    print('4. fixtures')
    fx = os.path.join(AQUI, 'fixtures')
    for nome in sorted(os.listdir(fx)) if os.path.isdir(fx) else []:
        with open(os.path.join(fx, nome), encoding='utf-8') as fh:
            txt = fh.read()
        h = achados(txt, f'fixture {nome}')
        todos += h
        if h:
            print(f'   !! {nome}')
    print('   (só os que vazaram aparecem)')

    print()
    if not todos:
        print('PENTE-FINO LIMPO — nenhum nome real na saída.')
        return 0
    print(f'{len(todos)} VAZAMENTO(S):\n')
    for origem, termo, trecho in todos:
        print(f'  [{termo}] {origem}')
        print(f'      …{trecho}…')
    return 1


if __name__ == '__main__':
    sys.exit(main())
