"""Gate da torre de controle na vitrine: a tela tem de contar uma história com lógica.

Não confere número exato (a base é sintética e muda com a data) — confere o que, se
falhar, aparece na frente do cliente como "sistema errado". Cada regra saiu de um
defeito real achado ao portar a torre (28/09/2026):

  AO VIVO
    confere                 status e datas das cargas abertas contam a mesma história
    entraram hoje           no máximo os manifestos emitidos hoje até agora (era 1.114:
                            toda carga nascia às 04:00)
    frota                   tudo rastreado → "sem informação" = 0; parte das livres no pátio
    parada na estrada       nenhuma parada longa de DIA (o pernoite era a qualquer hora)
    relógios                GPS verde; documentos e motor verdes na régua diária
  RETRATO (7 dias anteriores, à meia-noite)
    não vazio               cargas e carretas existem (saía tudo zerado)
    sem parada falsa        à meia-noite o motorista dorme há < 11 h
    km roteirizado          viagem entregue tem km (sem ORS saía 0)
  TRILHA
    jornada                 nenhuma parada ≥ 2 h entre 06 h e 22 h no meio de uma viagem

    python -X utf8 seed/_teste_torre.py        # termina em "TORRE OK" ou lista as falhas
"""

import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(RAIZ, '.env'))

import psycopg2  # noqa: E402
import torre  # noqa: E402

BRT = timedelta(hours=3)
falhas = []


def ok(cond, regra, detalhe=''):
    print(f"  {'ok  ' if cond else 'FALHA'} {regra}" + (f' — {detalhe}' if detalhe else ''))
    if not cond:
        falhas.append(regra)


def conn():
    return psycopg2.connect(host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', '5432'),
                            dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
                            password=os.getenv('DB_PASSWORD'))


def ao_vivo(cur):
    print('\nAO VIVO')
    d = torre.montar(cur)
    agora = datetime.fromisoformat(d['instante'])
    cx, fr = d['caixas'], d['frota']

    ok(cx['confere']['ok'], 'confere: status × datas',
       f"{cx['confere']['n_divergentes']} de {cx['confere']['abertas']}: {cx['confere']['divergentes'][:5]}")

    with open(os.path.join(AQUI, 'fixtures', 'manifestos.json'), encoding='utf-8') as fh:
        man = json.load(fh)
    hoje = (agora - BRT).date()
    emitidos = sum(1 for m in man if datetime.fromisoformat(m['data_emissao']).date() == hoje
                   and datetime.fromisoformat(m['data_emissao']) + BRT <= agora)
    ent = cx['documento_sem_saida']['hoje_entraram']
    ok(ent <= emitidos, 'entraram hoje ≤ manifestos emitidos hoje até agora', f'{ent} × {emitidos}')

    baldes = {b['id']: b['n'] for b in fr['baldes']}
    ok(fr['total'] > 0 and baldes['sem_info'] == 0, 'frota toda com sinal (vitrine: tudo rastreado)',
       f"{fr['total']} carretas, sem_info {baldes['sem_info']}")
    ok(any(c['cidade'].startswith('pátio') for c in fr['livres_por_cidade']) or baldes['livres'] == 0,
       'livres aparecem no pátio das filiais', str(fr['livres_por_cidade'][:5]))

    tipos = Counter(e['tipo'] for e in d['excecoes'])
    h = (agora - BRT).hour + (agora - BRT).minute / 60
    # Incidentes plantados (trilha.QUEBRA_H, PARTIDA_ATRASADA_H, gerar._coleta_vencida_hoje):
    # cada um tem de aparecer na sua janela — e NENHUMA outra parada na estrada.
    if 9 <= h < 17.5:
        ok(tipos.get('Parada na estrada', 0) == 1 and baldes['olhar'] >= 1,
           'quebra plantada: 1 "parada na estrada" e "precisa olhar" ≥ 1', f"{dict(tipos)} olhar {baldes['olhar']}")
    elif 6 <= h < 20:
        ok(tipos.get('Parada na estrada', 0) == 0, 'de dia, fora da quebra, nenhuma "parada na estrada"', str(dict(tipos)))
    if 8.5 <= h < 20.5:
        ok(tipos.get('Documento sem saída', 0) >= 1, 'partida atrasada: "documento sem saída" > 12 h', str(dict(tipos)))
    if h >= 9.6:
        ok(tipos.get('Coleta vencida sem documento', 0) >= 1, 'coleta vencida plantada aparece', str(dict(tipos)))
    ok(tipos.get('Rastreador sem sinal', 0) == 0, 'nenhum "rastreador sem sinal"', str(dict(tipos)))
    # "Esperando no cliente" (24 h) nunca acende: o motor fecha a carga por `gps_dwell_destino`
    # com as mesmas 24 h. Vale também para o projeto de origem.
    ok(tipos.get('Esperando no cliente', 0) == 0, 'ninguém "esperando no cliente" > 24 h', str(dict(tipos)))

    r = d['relogios']
    ok(r['gps']['cor'] == 'verde', 'relógio de GPS verde', str(r['gps']))
    ok(r['documentos']['cor'] == 'verde' and r['motor']['cor'] == 'verde',
       'relógios de documentos e motor verdes (régua diária)', f"{r['documentos']} {r['motor']}")
    print(f"     caixas: aberta {cx['documento_sem_saida']['estoque']} · trânsito {cx['em_transito']['estoque']}"
          f" · destino {cx['no_destino']['estoque']} · entregues hoje {cx['entregues_no_dia']['estoque']}"
          f" · entraram hoje {ent}")
    print(f"     frota: {baldes} · exceções {dict(tipos)}")


def retratos(cur, dias=7):
    print(f'\nRETRATO dos {dias} dias anteriores (à meia-noite)')
    hoje = (datetime.now(timezone.utc).replace(tzinfo=None) - BRT).date()
    for i in range(1, dias + 1):
        dia = hoje - timedelta(days=i)
        d = torre.montar(cur, dia=dia)
        cx, fr = d['caixas'], d['frota']
        abertas = (cx['documento_sem_saida']['estoque'] + cx['em_transito']['estoque']
                   + cx['no_destino']['estoque'])
        tipos = Counter(e['tipo'] for e in d['excecoes'])
        km = d['produtividade']['km_roteirizado_entregues']
        print(f"     {dia:%d/%m}: abertas {abertas} · entregues {cx['entregues_no_dia']['estoque']}"
              f" · frota {fr['total']} · km {round(km['carregado'])} ({km['viagens']} viagens)"
              f" · exceções {dict(tipos)}")
        ok(abertas > 0 and fr['total'] > 0, f'{dia:%d/%m}: retrato não vazio')
        ok(tipos.get('Parada na estrada', 0) == 0, f'{dia:%d/%m}: meia-noite sem "parada na estrada"')
        ok(km['viagens'] == 0 or km['carregado'] > 0, f'{dia:%d/%m}: viagem entregue tem km roteirizado')


def jornada(cur):
    """Parada ≥ 2 h de DIA no meio de uma viagem (longe da origem e do destino)."""
    print('\nTRILHA · jornada do motorista')
    cur.execute("""SELECT c.numero, COALESCE(c.carreta1_placa, c.cavalo_placa), c.data_saida_real,
                          c.no_local_desde, c.origem_latitude, c.origem_longitude, d.latitude, d.longitude
                     FROM embarques_cargas c
                     JOIN embarques_cargas_destinos d ON d.carga_id = c.id
                    WHERE NOT COALESCE(c.viagem_vazia, FALSE) AND c.data_saida_real IS NOT NULL
                      AND c.no_local_desde IS NOT NULL
                      AND c.data_saida_real >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '25 days'""")
    import geocoding
    import placas as pl
    ruins, total = [], 0
    hoje = (datetime.now(timezone.utc).replace(tzinfo=None) - BRT).date()
    quebra_ini = datetime.combine(hoje, datetime.min.time()) + timedelta(hours=7) + BRT
    for num, placa, ini, fim, olat, olng, dlat, dlng in cur.fetchall():
        cur.execute("""SELECT data_posicao, velocidade, latitude, longitude FROM embarques_posicoes_historico
                        WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s ORDER BY 1""",
                    (pl.grafias(placa), ini, fim))
        pts = cur.fetchall()
        total += 1
        parado_desde = None
        for t, v, la, ln in pts + [(fim, 99, None, None)]:
            if (v or 0) <= 3 and parado_desde is None:
                parado_desde = (t, la, ln)
            elif (v or 0) > 3 and parado_desde is not None:
                t0, la, ln = parado_desde
                parado_desde = None
                longe = (la is not None and olat is not None and dlat is not None
                         and geocoding.km_entre(float(la), float(ln), float(olat), float(olng)) > 30
                         and geocoding.km_entre(float(la), float(ln), float(dlat), float(dlng)) > 30)
                # horas desta parada que caem entre 06 h e 20 h (Brasília)
                dia_h, h = 0.0, t0
                while h < t:
                    passo = min(t, h + timedelta(minutes=10))
                    if 6 <= (h - BRT).hour < 20:
                        dia_h += (passo - h).total_seconds() / 3600
                    h = passo
                quebra = quebra_ini <= t0 < quebra_ini + timedelta(hours=1)   # a plantada
                if longe and dia_h >= 2 and not quebra:
                    ruins.append((num, f'{t0 - BRT:%d/%m %H:%M}', round(dia_h, 1)))
    ok(not ruins, 'nenhuma parada ≥ 2 h de dia no meio da viagem',
       f'{len(ruins)} em {total} viagens: {ruins[:5]}')


def main():
    con = conn()
    cur = con.cursor()
    ao_vivo(cur)
    retratos(cur)
    jornada(cur)
    cur.close()
    con.close()
    print()
    if falhas:
        print(f'{len(falhas)} FALHA(S): ' + '; '.join(falhas))
        return 1
    print('TORRE OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
