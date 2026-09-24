"""Trilha de GPS da base demo.

Gera o rastro das viagens recentes. É o que acende três telas que nenhuma
fixture de Power BI acende: o **Mapa** (linha e KPIs ao vivo), o **PGR**
(episódios de excesso) e a **consolidação diária** (km com e sem documento).

Duas gravações, de propósito — e é a pegadinha que a leitura do passo 1 achou:

* `embarques_posicoes_historico` — é daqui que o **PGR** lê. O caminho normal
  seria o backfill da 3S, mas `tres_s_client` em `MODO_SIMULADO` responde que
  "o simulador não tem histórico denso" e o backfill vira **no-op**. Então a
  trilha entra direto na tabela;
* `embarques_simulacao` — é daqui que o **worker** lê a "última posição" a cada
  ciclo, que é o que move o mapa ao vivo.

O que o gerador NÃO faz: decidir status de carga. Quem carimba saída, chegada e
entrega é o worker, pela mesma régua de produção. A trilha só põe o caminhão na
estrada.
"""

import math
import random
from datetime import timedelta

from empresa import CIDADES
from gerar import haversine

# Cadência do aparelho: 2–5 min em movimento, 1 h parado (é o que a §PGR chama
# de "lacuna normal" — parado, lacuna longa não é cegueira).
INTERVALO_MOV = (2, 5)
INTERVALO_PARADO = 60
VEL_CRUZEIRO = (62, 88)
HORAS_DIA = 9                      # jornada: o caminhão não anda 24 h
RETENCAO_DIAS = 33                 # a base só guarda ~30 d; gerar mais é lixo


def _interpolar(o, d, f):
    """Ponto a `f` do caminho entre duas cidades, com desvio de estrada."""
    lat = o[2] + (d[2] - o[2]) * f
    lon = o[3] + (d[3] - o[3]) * f
    # A estrada não é reta: uma senoide suave afasta a linha da geodésica.
    desvio = math.sin(f * math.pi) * 0.35
    return lat + desvio * 0.3, lon - desvio * 0.2


def _cidade_mais_perto(lat, lon):
    return min(CIDADES, key=lambda c: (c[2] - lat) ** 2 + (c[3] - lon) ** 2)


class Trilha:
    def __init__(self, gerador, semente=99):
        self.g = gerador
        self.rnd = random.Random(semente)
        self.historico = []          # embarques_posicoes_historico
        self.simulacao = []          # embarques_simulacao
        self.cadastro = {}           # placa -> id_veiculo_3s
        self._odo = {}
        self.mudas = set()
        self.episodios = 0

    # ── qual placa carrega o rastreador ──
    def _placa_rastreada(self, v):
        """O GPS costuma estar na CARRETA (o cavalo puxa qualquer uma).

        A demo mantém a proporção: 4 em 5 viagens rastreiam pela carreta. É o
        que faz a aba de Veículos e o PGR precisarem do par cavalo↔carreta, que
        é um detalhe que impressiona quem conhece a operação.
        """
        return v['carreta'] if self.rnd.random() < 0.8 else v['cavalo']

    def _id(self, placa):
        if placa not in self.cadastro:
            self.cadastro[placa] = 3000 + len(self.cadastro)
        return self.cadastro[placa]

    def _ponto(self, placa, quando, lat, lon, vel, odo, ignicao=True):
        cid = _cidade_mais_perto(lat, lon)
        p = {
            'placa': placa, 'id_veiculo_3s': self._id(placa),
            'data_posicao': quando.strftime('%Y-%m-%d %H:%M:%S'),
            'latitude': round(lat, 6), 'longitude': round(lon, 6),
            'velocidade': int(vel), 'ignicao': ignicao,
            'uf': cid[1], 'cidade': cid[0],
            'endereco': 'BR-' + self.rnd.choice(['050', '153', '262', '365', '381', '040'])
                        if vel > 5 else f'{cid[0]} - PATIO',
            'odometer': int(odo),
        }
        self.historico.append(p)
        return p

    # ── uma viagem ──
    def percorrer(self, v):
        rnd = self.rnd
        placa = self._placa_rastreada(v)
        if placa in self.mudas:
            return                      # carreta muda: existe e não fala

        odo = self._odo.setdefault(placa, rnd.randint(200_000, 900_000))
        km = v['km']
        # Sai algumas horas depois do manifesto (o documento nasce antes da roda
        # girar) — é o que dá trabalho ao robô e o que o `inicio_viagem` detecta.
        t = v['saida'] + timedelta(minutes=rnd.randint(20, 240))

        # 1. parado na origem: o worker precisa ver a saída acontecer
        for _ in range(rnd.randint(2, 4)):
            lat, lon = _interpolar(v['origem'], v['destino'], 0.0)
            self._ponto(placa, t, lat + rnd.uniform(-.01, .01),
                        lon + rnd.uniform(-.01, .01), 0, odo, ignicao=False)
            t += timedelta(minutes=INTERVALO_PARADO)

        # 2. estrada
        percorrido = 0.0
        # Um episódio de excesso a cada ~7 viagens: o PGR tem de ter o que achar,
        # mas base inteira acima de 95 viraria ruído e não conduta.
        excesso_em = rnd.uniform(0.2, 0.8) if rnd.random() < 0.14 else None
        horas_hoje = 0.0
        while percorrido < km:
            vel = rnd.uniform(*VEL_CRUZEIRO)
            f = percorrido / km if km else 1.0
            if excesso_em is not None and abs(f - excesso_em) < 0.03:
                vel = rnd.uniform(97, 118)          # o episódio
            passo_min = rnd.randint(*INTERVALO_MOV)
            avanco = vel * passo_min / 60.0
            percorrido += avanco
            odo += avanco
            horas_hoje += passo_min / 60.0
            lat, lon = _interpolar(v['origem'], v['destino'], min(1.0, percorrido / km))
            t += timedelta(minutes=passo_min)
            self._ponto(placa, t, lat, lon, vel, odo)

            if horas_hoje >= HORAS_DIA and percorrido < km:
                # pernoite: parado, reportando de hora em hora
                for _ in range(rnd.randint(8, 11)):
                    t += timedelta(minutes=INTERVALO_PARADO)
                    self._ponto(placa, t, lat, lon, 0, odo, ignicao=False)
                horas_hoje = 0.0

        if excesso_em is not None:
            self.episodios += 1

        # 3. parado no destino — é a parada sustentada que prova a chegada
        lat, lon = _interpolar(v['origem'], v['destino'], 1.0)
        for _ in range(rnd.randint(3, 14)):
            t += timedelta(minutes=INTERVALO_PARADO)
            self._ponto(placa, t, lat + rnd.uniform(-.008, .008),
                        lon + rnd.uniform(-.008, .008), 0, odo, ignicao=False)
        self._odo[placa] = odo
        v['placa_rastreada'] = placa
        v['chegada_gps'] = t

    # ── laço ──
    def rodar(self):
        g = self.g
        recentes = [v for v in g.viagens if (g.ref - v['dia']).days <= RETENCAO_DIAS]

        # Uma carreta muda: o `V1` do aferidor mistura cobertura de sensor com
        # defeito de documento, e a demo mostra o sistema dizendo "não sei" em
        # vez de inventar — que é o comportamento correto e vende confiança.
        carretas = sorted({v['carreta'] for v in recentes})
        if carretas:
            self.mudas.add(self.rnd.choice(carretas))

        for v in sorted(recentes, key=lambda x: x['saida']):
            self.percorrer(v)

        # A última posição de cada placa alimenta o simulador (mapa ao vivo).
        ultimo = {}
        for p in self.historico:
            ultimo[p['placa']] = p
        for p in ultimo.values():
            q = dict(p)
            q.pop('odometer', None)
            q['bairro'] = ''
            self.simulacao.append(q)

        return {
            'viagens_com_trilha': sum(1 for v in recentes if v.get('placa_rastreada')),
            'posicoes': len(self.historico),
            'placas': len(self.cadastro),
            'episodios_excesso': self.episodios,
            'carreta_muda': sorted(self.mudas),
        }
