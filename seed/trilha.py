"""Trilha de GPS da base demo.

Gera o rastro dos veículos na janela recente. É o que acende três telas que
nenhuma fixture de Power BI acende: o **Mapa** (linha e KPIs ao vivo), o **PGR**
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

**Linha do tempo por VEÍCULO, não por viagem (28/09/2026).** A primeira versão
desenhava cada viagem isolada: o caminhão aparecia na origem, andava em linha reta,
parava no destino e... sumia. Três defeitos visíveis saíam disso:

* a carga nunca virava "Entregue" — o worker fecha quando a carreta SAI do destino,
  e ela nunca saía (21 cargas em "No destino" há 4,6 dias em média, "há 7d");
* a carreta se teletransportava: terminava no Rio e reaparecia em Serra/ES para a
  viagem seguinte (108 de 118 encadeamentos, salto mediano de 750 km);
* o rastreador mudava de veículo a cada viagem (sorteio por viagem), então a tela
  seguia a carreta parada enquanto quem andava era o cavalo.

Agora cada veículo com rastreador tem uma vida contínua: espera na origem →
carrega e sai → estrada (de dia, com pausas curtas, pernoite das 20:30 às ~05:30)
→ descarrega (2–8 h) → segue VAZIO para a origem da próxima viagem (ou para a
filial mais próxima, se não há próxima) → espera. Parado, o aparelho reporta de
hora em hora, como o de verdade. O rastreador é do veículo, e na vitrine todo
veículo tem (cavalo e carreta).

**Estrada, não reta.** O trajeto passa pelas cidades do meio do caminho (um grafo
das 40 cidades da base, cada uma ligada às vizinhas), então BH → Porto Alegre desce
por São Paulo e Curitiba em vez de cortar o mapa na diagonal.

**Passado × futuro.** O histórico recebe só o que já aconteceu; a simulação recebe
a última posição passada de cada placa e o resto da estrada, que o `simulador_3s`
libera conforme o relógio (o ponto mais recente que não esteja no futuro). Até
28/09/2026 o simulador recebia o destino, dias à frente, e a viagem nova nascia
"No destino" sem trajeto nem km.

**Fuso.** O app guarda `data_posicao` em UTC naive (o worker grava `NOW()` do
container e o `server.py` subtrai 3 h para exibir). A vida do veículo é pensada no
relógio de Brasília — sai de dia, como o manifesto — e a conversão acontece só na
gravação do ponto.
"""

import heapq
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from empresa import CIDADES, UNIDADES
from gerar import haversine

BRT_UTC = timedelta(hours=3)       # Brasília sem horário de verão desde 2019

# Cadência do aparelho: 2–5 min em movimento, 1 h parado (é o que a §PGR chama
# de "lacuna normal" — parado, lacuna longa não é cegueira).
INTERVALO_MOV = (2, 5)
INTERVALO_PARADO = 60
VEL_CRUZEIRO = (62, 88)
# Jornada de motorista (28/09/2026): dirige de dia, pernoita à noite. A 1ª versão
# parava 8–11 h depois de 9 h ao volante, a qualquer hora — às 10 h da manhã o
# caminhão estava "dormindo" na estrada, e a torre de controle (que só aceita
# parada longa entre 22 h e 6 h) acendia "parada na estrada" em alerta alto.
FIM_JORNADA_H = 20.5               # 20:30 encosta para dormir (antes das 22 h, dentro do limite de 2 h)
INICIO_JORNADA_H = (5.0, 5.75)     # e sai entre 05:00 e 05:45 (antes das 6 h)
PAUSA_A_CADA_H = (3.5, 5.0)        # café/almoço/posto a cada 3,5–5 h ao volante
PAUSA_MIN = (20, 50)               # curta: bem abaixo das 2 h que viram exceção
RETENCAO_DIAS = 33                 # a base só guarda ~30 d; gerar mais é lixo
CARRETA_COM_RASTREADOR = 1.0       # vitrine: tudo rastreado (decisão de 28/09/2026)
DESCARGA_H = (2, 8)                # parado no destino antes de ir embora
FATOR_ESTRADA = 1.25               # km de estrada ÷ km em linha reta (perna vazia)
HORIZONTE_H = 30                   # o futuro vai até o próximo ciclo das 04:00 com folga
# Excesso de velocidade (PGR): sorteado por DIA DE ESTRADA de cada veículo, não por viagem.
# Por viagem dava ~1 episódio a cada 7 viagens — menos de um veículo por dia na tela do PGR.
P_EXCESSO_DIA = 0.65               # chance de um trecho acima de 95 km/h num dia de estrada
EPISODIO_MIN = (3, 25)             # duração do trecho: 1–5 leituras (2+ = "sustentado")
# Incidentes plantados (28/09/2026) — a torre de controle só mostra o que sabe fazer se algo
# fugir do normal. Nascem no GPS, não no status: a torre os acha pela própria régua, como o
# CIOT acha os defeitos plantados no documento. Horários em Brasília, no dia da base.
QUEBRA_H = (7.0, 17.5)             # carreta carregada parada no meio da viagem: "precisa olhar"
PARTIDA_ATRASADA_H = 20.5          # manifesto de ontem à tarde que só sai hoje à noite
VIZINHAS = 2                       # arestas mínimas por cidade no grafo de estradas
ALCANCE_KM = 450                   # e todas as cidades até esta distância


# ── estradas ─────────────────────────────────────────────────────────────
def _grafo():
    """Cada cidade ligada a todas até ALCANCE_KM, e às VIZINHAS mais próximas.

    Não é a malha rodoviária — é o bastante para o trajeto passar pelas cidades
    do meio do caminho, que é o que o olho confere no mapa. Só as vizinhas não
    bastava: o Rio ficava sem ligação com São Paulo e BH → Porto Alegre subia por
    Montes Claros e Brasília. As vizinhas seguram as isoladas (Fortaleza, Recife)."""
    viz = defaultdict(set)
    for i, a in enumerate(CIDADES):
        perto = sorted((haversine(a, b), j) for j, b in enumerate(CIDADES) if j != i)
        for k, (d, j) in enumerate(perto):
            if d <= ALCANCE_KM or k < VIZINHAS:
                viz[i].add(j)
                viz[j].add(i)
    # Ilhas (o Nordeste fica a mais de ALCANCE_KM do resto): liga cada uma ao
    # continente pela aresta mais curta, senão Campinas → Fortaleza sai em reta.
    while True:
        visto, fila = {0}, [0]
        while fila:
            for j in viz[fila.pop()]:
                if j not in visto:
                    visto.add(j)
                    fila.append(j)
        if len(visto) == len(CIDADES):
            return viz
        _d, i, j = min((haversine(CIDADES[i], CIDADES[j]), i, j)
                       for i in visto for j in range(len(CIDADES)) if j not in visto)
        viz[i].add(j)
        viz[j].add(i)


_VIZ = _grafo()
_IDX = {(c[0], c[1]): i for i, c in enumerate(CIDADES)}
_CAMINHOS = {}


def _caminho(o, d):
    """Sequência de cidades de `o` a `d` pelo grafo (Dijkstra, custo = km)."""
    chave = ((o[0], o[1]), (d[0], d[1]))
    if chave in _CAMINHOS:
        return _CAMINHOS[chave]
    io, id_ = _IDX[chave[0]], _IDX[chave[1]]
    dist, ant, fila = {io: 0.0}, {}, [(0.0, io)]
    while fila:
        custo, i = heapq.heappop(fila)
        if i == id_:
            break
        if custo > dist.get(i, math.inf):
            continue
        for j in _VIZ[i]:
            # custo superlinear: prefere dois trechos de 300 km a um de 600 — é o
            # que faz o trajeto passar pelas cidades do meio em vez de pular
            c = custo + haversine(CIDADES[i], CIDADES[j]) ** 1.5
            if c < dist.get(j, math.inf):
                dist[j], ant[j] = c, i
                heapq.heappush(fila, (c, j))
    if id_ not in dist:                       # grafo desconexo: vai reto
        seq = [o, d]
    else:
        seq, i = [], id_
        while i != io:
            seq.append(CIDADES[i])
            i = ant[i]
        seq.append(CIDADES[io])
        seq.reverse()
    _CAMINHOS[chave] = seq
    return seq


def _ponto_no_caminho(seq, f):
    """Ponto a fração `f` (0..1) do comprimento do caminho."""
    trechos = [haversine(a, b) for a, b in zip(seq, seq[1:])]
    total = sum(trechos) or 1.0
    alvo = f * total
    for (a, b), km in zip(zip(seq, seq[1:]), trechos):
        if alvo <= km or b is seq[-1]:
            g = min(1.0, alvo / km) if km else 1.0
            return a[2] + (b[2] - a[2]) * g, a[3] + (b[3] - a[3]) * g
        alvo -= km
    return seq[-1][2], seq[-1][3]


def _cidade_mais_perto(lat, lon):
    return min(CIDADES, key=lambda c: (c[2] - lat) ** 2 + (c[3] - lon) ** 2)


_FILIAIS = [c for c in CIDADES if (c[0], c[1]) in set(UNIDADES.values())]


class Trilha:
    def __init__(self, gerador, semente=99, agora_utc=None):
        self.g = gerador
        self.rnd = random.Random(semente)
        # Corte entre o que já aconteceu e o que o caminhão ainda vai fazer.
        self.agora = agora_utc or datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        self.fim = self.agora - BRT_UTC + timedelta(hours=HORIZONTE_H)   # relógio BRT
        self.historico = []          # embarques_posicoes_historico (só até `agora`)
        self.simulacao = []          # embarques_simulacao (último ponto passado + o futuro)
        self._pontos = []            # a trilha inteira, antes do corte
        self.cadastro = {}           # placa -> id_veiculo_3s
        self._odo = {}
        self._rastreador = {}        # placa -> tem aparelho?
        self._ultimo_t = {}          # placa -> hora do último ponto
        self.mudas = set()
        self.episodios = 0

    # ── quem tem rastreador ──
    def _tem_rastreador(self, placa, eh_carreta):
        """O aparelho é do VEÍCULO: sorteado uma vez, vale para todas as viagens."""
        if placa not in self._rastreador:
            self._rastreador[placa] = (not eh_carreta) or self.rnd.random() < CARRETA_COM_RASTREADOR
        return self._rastreador[placa]

    def _principal(self, v):
        """A placa que a tela segue: a carreta, se tem aparelho; senão o cavalo.

        É a mesma ordem do worker (`_placa_tracking`: carreta1 → cavalo), e é o
        que faz a aba de Veículos e o PGR precisarem do par cavalo↔carreta."""
        return v['carreta'] if self._tem_rastreador(v['carreta'], True) else v['cavalo']

    def _id(self, placa):
        if placa not in self.cadastro:
            self.cadastro[placa] = 3000 + len(self.cadastro)
        return self.cadastro[placa]

    def _ponto(self, placa, quando, lat, lon, vel, odo, ignicao=True):
        # A hora do aparelho só anda para a frente: a chegada e o primeiro ponto
        # parado caíam no mesmo minuto, e a tabela tem UNIQUE(placa, data_posicao).
        ult = self._ultimo_t.get(placa)
        if ult is not None and quando <= ult:
            quando = ult + timedelta(minutes=1)
        if vel > 5 and ult is not None and quando - ult > timedelta(minutes=30):
            # Rodando depois de um silêncio longo (saiu do pátio, do pernoite, da pausa): o
            # aparelho fala quando o motor liga. Sem este ponto o PGR lia a hora parada como
            # "sem sinal ENQUANTO rodava" e listava a placa em "cobertura insuficiente".
            self._ponto(placa, quando - timedelta(minutes=2), lat, lon, 0, odo, ignicao=True)
            ult = self._ultimo_t[placa]
        self._ultimo_t[placa] = quando
        cid = _cidade_mais_perto(lat, lon)
        p = {
            'placa': placa, 'id_veiculo_3s': self._id(placa),
            'data_posicao': (quando + BRT_UTC).strftime('%Y-%m-%d %H:%M:%S'),
            'latitude': round(lat, 6), 'longitude': round(lon, 6),
            'velocidade': int(vel), 'ignicao': ignicao,
            'uf': cid[1], 'cidade': cid[0],
            'endereco': 'BR-' + self.rnd.choice(['050', '153', '262', '365', '381', '040', '116', '101'])
                        if vel > 5 else f'{cid[0]} - PATIO',
            'odometer': int(odo),
        }
        self._pontos.append(p)
        return p

    # ── blocos da vida do veículo ──
    def _parado(self, placa, cidade, t, ate):
        """Parado em `cidade` de `t` até `ate`, reportando de hora em hora."""
        odo = self._odo[placa]
        lat, lon = cidade[2], cidade[3]
        while t < ate:
            lat, lon = cidade[2] + self.rnd.uniform(-.008, .008), cidade[3] + self.rnd.uniform(-.008, .008)
            self._ponto(placa, t, lat, lon, 0, odo, ignicao=False)
            t += timedelta(minutes=INTERVALO_PARADO)
        # O aparelho fala na hora de sair: sem este ponto a partida vinha até 60 min depois do
        # último "parado", e o PGR lia a lacuna como "sem sinal ENQUANTO rodava".
        self._ponto(placa, ate, lat, lon, 0, odo, ignicao=True)
        return max(t, ate)

    def _sortear_excesso(self, t, pode):
        """(início, fim) de um trecho acima de 95 km/h nas próximas horas, ou None."""
        if not pode or self.rnd.random() >= P_EXCESSO_DIA:
            return None
        ini = t + timedelta(hours=self.rnd.uniform(0.5, 6))
        return ini, ini + timedelta(minutes=self.rnd.randint(*EPISODIO_MIN))

    def _estrada(self, placa, o, d, km, t, pode_exceder=False, v=None, principal=False):
        """Dirige de `o` a `d` (`km` de estrada) a partir de `t`; devolve a chegada.

        `pode_exceder` liga o sorteio de excesso do PGR — só na placa principal da
        viagem carregada (o cavalo que a puxa não repete o episódio, senão o PGR o
        contaria duas vezes) e no cavalo sozinho na perna vazia.
        `v` (viagem carregada) liga a QUEBRA: a placa principal decide quando ela
        acontece e grava em `v`; a outra placa da viagem só obedece."""
        rnd = self.rnd
        quebrou = False
        excesso = self._sortear_excesso(t, pode_exceder)
        contado = False
        seq = _caminho(o, d)
        odo = self._odo[placa]
        percorrido, ao_volante = 0.0, 0.0
        proxima_pausa = rnd.uniform(*PAUSA_A_CADA_H)
        # O km da estrada nunca é menor que o desenho: senão o ponto anda no mapa
        # mais do que o velocímetro diz, e a régua de posição falsa (distância entre
        # pontos × odômetro) passa a desconfiar do próprio gerador.
        km = max(km, 1.05 * sum(haversine(a, b) for a, b in zip(seq, seq[1:])), 1.0)
        while percorrido < km:
            hora = t.hour + t.minute / 60.0
            q = v.get('_quebra') if v is not None else None
            if (q is None and v is not None and principal and self._cota['quebra']
                    and self._q_ini <= t < self._q_ini + timedelta(hours=1)
                    and 0.15 < percorrido / km < 0.85):
                q = v['_quebra'] = (t, self._q_fim)
                self._cota['quebra'] -= 1
            if q and not quebrou and q[0] <= t < q[1]:
                # quebra: parada longa, de dia, longe da origem e do destino
                lat, lon = _ponto_no_caminho(seq, min(1.0, percorrido / km))
                self._ponto(placa, t, lat, lon, 0, odo, ignicao=False)   # parou
                while t + timedelta(minutes=INTERVALO_PARADO) < q[1]:
                    t += timedelta(minutes=INTERVALO_PARADO)
                    self._ponto(placa, t, lat, lon, 0, odo, ignicao=False)
                t, quebrou = q[1], True
                self._ponto(placa, t, lat, lon, 0, odo, ignicao=True)   # religou: volta a andar
                ao_volante, proxima_pausa = 0.0, rnd.uniform(*PAUSA_A_CADA_H)
                continue
            if hora >= FIM_JORNADA_H or hora < INICIO_JORNADA_H[0]:
                # pernoite: parado até a manhã, reportando de hora em hora
                manha = t.replace(hour=0, minute=0, second=0) + timedelta(
                    days=1 if hora >= FIM_JORNADA_H else 0,
                    hours=rnd.uniform(*INICIO_JORNADA_H))
                lat, lon = _ponto_no_caminho(seq, min(1.0, percorrido / km))
                # encostou: sem este ponto o 1º "parado" vinha 1 h depois do último "andando",
                # e o PGR lia a hora como "sem sinal enquanto rodava"
                self._ponto(placa, t, lat, lon, 0, odo, ignicao=False)
                while t + timedelta(minutes=INTERVALO_PARADO) < manha:
                    t += timedelta(minutes=INTERVALO_PARADO)
                    self._ponto(placa, t, lat, lon, 0, odo, ignicao=False)
                t = manha
                self._ponto(placa, t, lat, lon, 0, odo, ignicao=True)   # ligou para sair
                ao_volante, proxima_pausa = 0.0, rnd.uniform(*PAUSA_A_CADA_H)
                excesso = self._sortear_excesso(t, pode_exceder)       # dia novo, sorteio novo
                contado = False
            elif ao_volante >= proxima_pausa:
                lat, lon = _ponto_no_caminho(seq, min(1.0, percorrido / km))
                self._ponto(placa, t, lat, lon, 0, odo, ignicao=False)   # encostou
                t += timedelta(minutes=rnd.randint(*PAUSA_MIN))
                self._ponto(placa, t, lat, lon, 0, odo, ignicao=True)    # saiu
                ao_volante, proxima_pausa = 0.0, rnd.uniform(*PAUSA_A_CADA_H)
            vel = rnd.uniform(*VEL_CRUZEIRO)
            if excesso and excesso[0] <= t < excesso[1]:
                vel = rnd.uniform(97, 118)          # o episódio
                if not contado:
                    self.episodios, contado = self.episodios + 1, True
            passo_min = rnd.randint(*INTERVALO_MOV)
            avanco = vel * passo_min / 60.0
            percorrido += avanco
            odo += avanco
            ao_volante += passo_min / 60.0
            lat, lon = _ponto_no_caminho(seq, min(1.0, percorrido / km))
            t += timedelta(minutes=passo_min)
            self._ponto(placa, t, lat + rnd.uniform(-.003, .003),
                        lon + rnd.uniform(-.003, .003), vel, odo)

        self._odo[placa] = odo
        return t

    # ── a vida de um veículo ──
    def _viver(self, placa, viagens):
        rnd = self.rnd
        self._odo.setdefault(placa, rnd.randint(200_000, 900_000))
        onde, t = None, None
        for v in viagens:
            # O caminhão encosta na origem antes do manifesto e sai depois dele:
            # o documento nasce antes da roda girar — é o que o `inicio_viagem` detecta.
            partida = v['saida'] + timedelta(minutes=rnd.randint(30, 150))
            if onde is None:
                onde = v['origem']
                t = v['saida'] - timedelta(hours=12)
            elif onde[:2] != v['origem'][:2]:
                # perna vazia: sai logo depois de descarregar, rumo à próxima origem
                km = haversine(onde, v['origem']) * FATOR_ESTRADA
                t = self._estrada(placa, onde, v['origem'], km, t,
                                  pode_exceder=placa not in self._carretas)
                onde = v['origem']
            principal = placa == v['placa_rastreada']
            if (principal and '_partida_min' not in v and self._cota['partida']
                    and self._ontem_15 <= v['saida'] < self._ontem_15 + timedelta(hours=5)
                    and self._livre_depois(v)):
                # documento sem saída: o manifesto saiu ontem à tarde e o caminhão não
                v['_partida_min'] = self._partida_atrasada
                self._cota['partida'] -= 1
            partida = max(partida, v.get('_partida_min', partida))
            t = self._parado(placa, onde, t, max(partida, t + timedelta(minutes=30)))

            t = self._estrada(placa, v['origem'], v['destino'], v['km'], t,
                              pode_exceder=principal, v=v, principal=principal)
            # descarga: a parada sustentada que prova a chegada ao worker
            t = self._parado(placa, v['destino'], t,
                             t + timedelta(hours=rnd.uniform(*DESCARGA_H)))
            onde = v['destino']

        # Sem próxima viagem na janela: volta para a filial mais perto e espera lá.
        # É a saída do destino que fecha a carga como "Entregue".
        base = min(_FILIAIS, key=lambda c: haversine(onde, c))
        if base[:2] != onde[:2] and t < self.fim:
            t = self._estrada(placa, onde, base, haversine(onde, base) * FATOR_ESTRADA, t,
                              pode_exceder=placa not in self._carretas)
            onde = base
        self._parado(placa, onde, t, self.fim)

    def _livre_depois(self, v):
        """Nem a carreta nem o cavalo têm outra viagem antes de amanhã — atrasar a partida
        não empurra a vida deles para cima de outra carga."""
        amanha = datetime.combine(self.g.ref + timedelta(days=1), datetime.min.time())
        return all(x is v or x['saida'] < v['saida'] or x['saida'] >= amanha
                   for p in (v['carreta'], v['cavalo']) for x in self._agenda.get(p, ()))

    # ── laço ──
    def rodar(self):
        g = self.g
        hoje = datetime.combine(g.ref, datetime.min.time())
        self._q_ini, self._q_fim = hoje + timedelta(hours=QUEBRA_H[0]), hoje + timedelta(hours=QUEBRA_H[1])
        self._ontem_15 = hoje - timedelta(hours=9)
        self._partida_atrasada = hoje + timedelta(hours=PARTIDA_ATRASADA_H)
        self._cota = {'quebra': 1, 'partida': 1}
        recentes = sorted((v for v in g.viagens if (g.ref - v['dia']).days <= RETENCAO_DIAS),
                          key=lambda x: x['saida'])

        for v in recentes:
            v['placa_rastreada'] = self._principal(v)

        # Sem carreta muda desde 28/09/2026: na vitrine tudo é rastreado (decisão do
        # Gabriel). `self.mudas` fica vazio, mas o caminho continua — é só sortear
        # uma aqui de novo para mostrar o sistema dizendo "não sei".

        agenda = self._agenda = defaultdict(list)
        for v in recentes:
            agenda[v['cavalo']].append(v)
            agenda[v['carreta']].append(v)
        # Carretas primeiro: a principal decide os incidentes e o cavalo da mesma viagem,
        # simulado depois, obedece — senão os dois contariam histórias diferentes.
        carretas = self._carretas = {v['carreta'] for v in recentes}
        for placa in sorted(agenda, key=lambda p: (p not in carretas, p)):
            eh_carreta = any(v['carreta'] == placa for v in agenda[placa])
            if placa in self.mudas or not self._tem_rastreador(placa, eh_carreta):
                continue                      # sem aparelho, ou aparelho mudo
            self._viver(placa, agenda[placa])

        # O que já aconteceu vai para o histórico; o simulador recebe a última
        # posição passada de cada placa (onde ela está AGORA) e o resto da estrada,
        # que ele vai liberando conforme o relógio passa.
        corte = self.agora.strftime('%Y-%m-%d %H:%M:%S')
        ultimo = {}
        for p in sorted(self._pontos, key=lambda p: (p['placa'], p['data_posicao'])):
            if p['data_posicao'] <= corte:
                self.historico.append(p)
                ultimo[p['placa']] = p
            else:
                self.simulacao.append(dict(p, bairro=''))
        self.simulacao.extend(dict(p, bairro='') for p in ultimo.values())

        return {
            'viagens_com_trilha': sum(1 for v in recentes if v['placa_rastreada'] not in self.mudas),
            'posicoes': len(self.historico),
            'posicoes_futuras': len(self.simulacao) - len(ultimo),
            'placas': len(self.cadastro),
            'episodios_excesso': self.episodios,
            'carreta_muda': sorted(self.mudas),
            'incidentes': {'quebra': [v['carreta'] for v in recentes if v.get('_quebra')],
                           'partida_atrasada': [v['carreta'] for v in recentes if v.get('_partida_min')]},
        }
