"""Cronologia e rota planejada das cargas — o passo que o ciclo roda depois do robô.

Existe por causa da **torre de controle** (28/09/2026), a primeira tela que conta
as cargas pela HORA em que as coisas aconteceram. Dois buracos da base diária:

1. **Toda carga nascia às 04:00 de hoje.** O robô do manifesto roda uma vez, no
   ciclo, e cria o ano inteiro de uma vez — `criado_em` = agora. A torre então dizia
   "1.114 cargas entraram hoje", e o retrato de qualquer dia anterior saía vazio
   (nenhuma carga "existia" antes de hoje). Em produção o robô roda várias vezes ao
   dia e a carga nasce minutos depois do manifesto; aqui o `criado_em` é trazido
   para a hora do manifesto + o atraso de uma rodada do robô. A carga cujo manifesto
   ainda nem foi emitido (hoje, mais tarde) fica com `criado_em` no futuro — e a
   torre, que só olha o que já existe no instante, a vê aparecer na hora certa.

2. **Sem rota planejada.** A rota vem do OpenRouteService, e a vitrine não tem
   chave: `distancia_planejada_km` vazio zerava o km roteirizado da torre, desligava
   o "atrasando" (600 km/dia) e tirava a linha azul do mapa da carga. Aqui a rota é
   desenhada pelo mesmo grafo de estradas da trilha de GPS (`trilha._caminho`), só
   para as cargas sem rota — se um dia a chave existir, a do ORS prevalece.

Tudo em UTC naive, como o resto do app. Idempotente.

    python -X utf8 seed/cronologia.py
"""

import json
import os
import sys
import zlib
from datetime import datetime, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)
sys.path.insert(0, AQUI)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(RAIZ, '.env'))

from gerar import haversine  # noqa: E402
from trilha import BRT_UTC, CIDADES, _caminho  # noqa: E402

ATRASO_ROBO_MIN = (5, 25)      # uma rodada do robô depois do manifesto (a partida vem 30+ min depois)
FATOR_ESTRADA = 1.15           # km de estrada ÷ comprimento do desenho
VEL_MEDIA_KMH = 65             # duração estimada, como a do ORS para caminhão


def _conn():
    import psycopg2
    return psycopg2.connect(host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', '5432'),
                            dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
                            password=os.getenv('DB_PASSWORD'))


def _atraso(chave):
    """Atraso determinístico por carga: a base regerada sai igual."""
    a, b = ATRASO_ROBO_MIN
    return timedelta(minutes=a + zlib.crc32(chave.encode()) % (b - a + 1))


# ── 1. cronologia ────────────────────────────────────────────────────────
def cronologia(cur):
    with open(os.path.join(AQUI, 'fixtures', 'manifestos.json'), encoding='utf-8') as fh:
        emissao = {m['CHAVE_MANIFESTO']: datetime.fromisoformat(m['data_emissao'])
                   for m in json.load(fh)}
    cur.execute("""SELECT id, manifesto_origem FROM embarques_cargas
                    WHERE NOT COALESCE(viagem_vazia, FALSE) AND manifesto_origem IS NOT NULL""")
    novos = [(emissao[m] + BRT_UTC + _atraso(m), i) for i, m in cur.fetchall() if m in emissao]
    cur.executemany('UPDATE embarques_cargas SET criado_em = %s WHERE id = %s', novos)
    # A perna vazia é DERIVADA pelo motor, depois do fato: nasce quando o cavalo sai.
    cur.execute("""UPDATE embarques_cargas
                      SET criado_em = COALESCE(data_saida_real, inicio_viagem,
                                               data_carregamento + INTERVAL '3 hours')
                    WHERE COALESCE(viagem_vazia, FALSE)""")
    # Fechada por "sequência de viagem" (a carreta pegou outra carga) conclui quando a
    # carga seguinte nasceu, não na hora do ciclo: a C-876 de 01/08 aparecia "aberta" em
    # todos os retratos porque o robô a fechou com o carimbo de hoje.
    cur.execute("""UPDATE embarques_cargas c SET data_conclusao = n.prox
                     FROM (SELECT c2.id, (SELECT MIN(x.criado_em) FROM embarques_cargas x
                                           WHERE x.carreta1_placa = c2.carreta1_placa AND x.id <> c2.id
                                             AND NOT COALESCE(x.viagem_vazia, FALSE)
                                             AND x.criado_em > c2.criado_em) AS prox
                             FROM embarques_cargas c2
                            WHERE c2.encerrada_motivo = 'sequencia_viagem') n
                    WHERE c.id = n.id AND n.prox IS NOT NULL AND n.prox < c.data_conclusao""")
    # "Atualizado" = o último fato da carga (o worker volta a carimbar ao vivo).
    cur.execute("""UPDATE embarques_cargas
                      SET atualizado_em = GREATEST(criado_em, COALESCE(data_conclusao, no_local_desde,
                                                   data_saida_real, criado_em))""")
    return len(novos)


# ── 2. rota planejada ────────────────────────────────────────────────────
def _codificar(pontos):
    """Polyline do Google, precisão 5 — o formato que o `mapa-carga.html` decodifica."""
    out, plat, plng = [], 0, 0
    for lat, lng in pontos:
        ilat, ilng = round(lat * 1e5), round(lng * 1e5)
        for v in (ilat - plat, ilng - plng):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plng = ilat, ilng
    return ''.join(out)


def _no(lat, lng):
    return min(CIDADES, key=lambda c: (c[2] - lat) ** 2 + (c[3] - lng) ** 2)


def rota_planejada(cur):
    cur.execute("""SELECT c.id, c.origem_latitude, c.origem_longitude,
                          ARRAY_AGG(d.latitude ORDER BY d.ordem), ARRAY_AGG(d.longitude ORDER BY d.ordem)
                     FROM embarques_cargas c JOIN embarques_cargas_destinos d ON d.carga_id = c.id
                    WHERE c.rota_planejada_polyline IS NULL AND c.origem_latitude IS NOT NULL
                      AND d.latitude IS NOT NULL
                    GROUP BY c.id""")
    feitas = []
    for cid, olat, olng, dlats, dlngs in cur.fetchall():
        paradas = [(float(olat), float(olng))] + [(float(a), float(b)) for a, b in zip(dlats, dlngs)]
        pontos = [paradas[0]]
        for (a_lat, a_lng), (b_lat, b_lng) in zip(paradas, paradas[1:]):
            seq = _caminho(_no(a_lat, a_lng), _no(b_lat, b_lng))
            pontos += [(c[2], c[3]) for c in seq[1:-1]] + [(b_lat, b_lng)]
        km = FATOR_ESTRADA * sum(haversine(('', '', *p), ('', '', *q)) for p, q in zip(pontos, pontos[1:]))
        km = max(km, 5.0)
        feitas.append((_codificar(pontos), round(km, 1), round(km / VEL_MEDIA_KMH * 60), cid))
    cur.executemany("""UPDATE embarques_cargas SET rota_planejada_polyline = %s,
                              distancia_planejada_km = %s, duracao_estimada_min = %s
                        WHERE id = %s""", feitas)
    return len(feitas)


def main():
    con = _conn()
    cur = con.cursor()
    n1 = cronologia(cur)
    n2 = rota_planejada(cur)
    con.commit()
    cur.close()
    con.close()
    print(f'   {n1} cargas datadas pelo manifesto · {n2} rotas planejadas')
    return 0


if __name__ == '__main__':
    sys.exit(main())
