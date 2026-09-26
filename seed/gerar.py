"""Gerador da base demo — a cadeia documental.

Gera **viagens**, não telas. Para cada viagem emite o conjunto que o SSW emitiria
— manifesto, CTRB, CTe(s), a linha da Auditoria Receita — e depois os fatos de
custo do período (abastecimento, pedágio, folha, despesa do 477). A trilha de GPS
sai do `trilha.py`.

Por que assim: as 12 abas não leem tabela, leem CRUZAMENTO. Se a mesma viagem não
for a mesma nos quatro documentos, a Auditoria mostra um número, o Faturamento
mostra outro e a demo morre na primeira pergunta. Gerando a viagem uma vez e
derivando os documentos dela, o cruzamento bate por construção — e os robôs do
projeto (robô do manifesto, fita, CIOT, PGR, Verda) rodam em cima disso como
rodam em produção.

**O esqueleto de cada tabela vem do `shape/`**, não de uma lista escrita à mão:
metade das telas faz `EVALUATE 'public X'` e renderiza o que vier, então coluna
que falta é coluna vazia na tela. O gerador preenche o que tem significado e
deixa o resto no default do tipo — mas a coluna existe, sempre.

Uso:
    python -X utf8 _seed_demo/gerar.py --resumo      # conta o que geraria
    python -X utf8 _seed_demo/gerar.py               # grava fixtures/
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from empresa import (CIDADES, EMPRESA, UNIDADES, cidade_uf, dv_cnpj,  # noqa: E402
                     para_mercosul, universo)
import narrativa  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))
SHAPE = os.path.join(AQUI, 'shape')
FIXTURES = os.path.join(AQUI, 'fixtures')

# Janela da demo. `REFERENCIA` é "hoje" para a base gerada: as telas abrem no mês
# corrente, então a base precisa ter mês corrente com movimento.
# "Hoje" da base gerada. As telas abrem no mês corrente e o card "Cargas hoje"
# conta o dia — base parada no passado mostra zero e parece sistema sem uso.
REFERENCIA = date.today()
MESES = 9                       # jan..set/2026 — a matriz do Faturamento
                                # (tomador x mes) fica vazia nos meses sem viagem,
                                # e meia tabela em branco lê como base incompleta
VIAGENS_MES = 190


# ── Esqueleto vindo do shape ──────────────────────────────────────────────
_CACHE_SHAPE = {}


def shape(tabela):
    if tabela not in _CACHE_SHAPE:
        with open(os.path.join(SHAPE, f'{tabela}.json'), encoding='utf-8') as fh:
            _CACHE_SHAPE[tabela] = json.load(fh)
    return _CACHE_SHAPE[tabela]


def esqueleto(tabela):
    """Todas as colunas da tabela real, no default do tipo.

    Coluna 100% nula na amostra continua nula — é informação do shape, não
    descuido: `manifesto_rateado` e `frete_tabela` são nulas na base também.
    """
    cols = shape(tabela)['colunas']
    out = {}
    for c, i in cols.items():
        t = i.get('tipo')
        if i.get('nulo_pct') == 100 or t == 'desconhecido':
            out[c] = None
        elif t == 'float':
            out[c] = 0.0
        elif t == 'int':
            out[c] = 0
        elif t == 'bool':
            out[c] = False
        else:
            out[c] = ''
    return out


# ── Utilidades ────────────────────────────────────────────────────────────
def haversine(a, b):
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a[2], a[3], b[2], b[3]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def km_rodoviario(a, b):
    """Haversine não é estrada: o fator 1,27 é a folga média da malha."""
    return round(haversine(a, b) * 1.27)


def iso(dt):
    """O shape mostrou que as datas chegam como TEXTO ISO, não datetime."""
    return dt.strftime('%Y-%m-%dT%H:%M:%S')


def hash_estavel(texto):
    """Inteiro estável a partir de um texto (o `hash()` do Python varia por
    processo, o que faria a base mudar a cada execução)."""
    import zlib
    return zlib.crc32(str(texto).encode('utf-8'))


def _dv(n):
    """Dígito do número de documento do SSW (módulo 11 simples)."""
    s = sum(int(d) * (i % 9 + 2) for i, d in enumerate(reversed(str(n))))
    r = 11 - (s % 11)
    return 0 if r > 9 else r


class Numerador:
    """Numeração por unidade, como o SSW faz — cada filial tem a sua sequência."""

    def __init__(self, inicio=10000):
        self.seq = {}
        self.inicio = inicio

    def proximo(self, unidade, especie):
        k = (unidade, especie)
        self.seq[k] = self.seq.get(k, self.inicio) + 1
        n = self.seq[k]
        return n, f'{n:06d}-{_dv(n)}'


def _mapa_dre():
    """O MAPA_DRE do `server.py`, lido do CÓDIGO — não copiado.

    Copiar os ~97 de-paras para cá criaria duas verdades: a DRE agruparia por uma
    e o gerador emitiria pela outra, e o erro só apareceria como grupo vazio na
    tela. Lendo da fonte, evento novo no server entra na demo sozinho.
    """
    import re
    caminho = os.path.join(os.path.dirname(AQUI), 'server.py')
    with open(caminho, encoding='utf-8') as fh:
        src = fh.read()
    i = src.index('MAPA_DRE')
    pares = re.findall(r"^\s*'([^']{3,60})':\s*\('([^']+)',\s*'([^']+)'\)",
                       src[i:i + 14000], re.M)
    return {n: (g, s) for n, g, s in pares}


# ── O motor ───────────────────────────────────────────────────────────────
class Gerador:
    def __init__(self, referencia=REFERENCIA, meses=MESES, viagens_mes=VIAGENS_MES):
        self.rnd = random.Random(20260922)
        self.ref = referencia
        self.meses = meses
        self.viagens_mes = viagens_mes
        self.u = universo()
        self.num = Numerador()
        self.t = {k: [] for k in (
            'manifestos', 'ctrbs_oss', 'conhecimentos_emitidos', 'manifestos_ctrc',
            'Auditoria Receita', 'veiculos_045', 'motoristas_047', 'tarifas_frete',
            'rotas_km', 'abastecimentos_valecard', 'semparar_lancamentos',
            'custo_pessoal', 'consulta_despesas_477', 'coletas_0157')}
        self.viagens = []          # a verdade: o que a trilha de GPS vai seguir
        self._hodometro = {}
        self._ocupado = {}
        self.ref_min = datetime(2000, 1, 1)
        self._rotas = {}

        self.cavalos = [v for v in self.u['veiculos'] if v['tipo'].startswith('CAVALO')]
        self.carretas = [v for v in self.u['veiculos'] if v['tipo'] == 'CARRETA']
        self.trucks = [v for v in self.u['veiculos'] if v['tipo'] == 'TRUCK']
        self.por_placa = {v['placa']: v for v in self.u['veiculos']}

    # ── janela ──
    def inicio(self):
        m, a = self.ref.month, self.ref.year
        m -= self.meses - 1
        while m <= 0:
            m += 12
            a -= 1
        return date(a, m, 1)

    # ── cadastros ──
    def cadastros(self):
        for v in self.u['veiculos']:
            r = esqueleto('veiculos_045')
            r.update({
                'placa': v['placa'], 'tipo': v['tipo'],
                'relacionamento': v['relacionamento'],
                'proprietario': v['proprietario'],
                'disponivel': 'S',
                'marca': v['modelo'].split()[0],
                'modelo': v['modelo'],
                # o shape flagrou: `ano` e `capacidade` são TEXTO, e o ano tem
                # 2 dígitos. Emitir int aqui quebraria a leitura silenciosamente.
                'ano': f'{v["ano"] % 100:02d}',
                'capacidade': f'{self.rnd.uniform(25, 45):.2f}'.replace('.', ','),
                'carroceria': 'TRACAO' if v['tipo'] != 'CARRETA' else 'SIDER',
                'eixos': v['eixos'],
                'data_hora_cadastro': f'{self.rnd.randint(1,28):02d}/'
                                      f'{self.rnd.randint(1,12):02d}/{v["ano"]} 08:00',
            })
            self.t['veiculos_045'].append(r)

        for m in self.u['motoristas']:
            r = esqueleto('motoristas_047')
            cid = self.rnd.choice(CIDADES)
            r.update({'cpf': m['cpf'], 'nome': m['nome'],
                      'cidade': cid[0], 'uf': cid[1]})
            self.t['motoristas_047'].append(r)

    # ── rede de rotas + tarifas ──
    def rota(self, o, d):
        k = (o[0], o[1], d[0], d[1])
        if k not in self._rotas:
            self._rotas[k] = km_rodoviario(o, d)
            r = esqueleto('rotas_km')
            r.update({'cidade_uf_origem': cidade_uf(o),
                      'cidade_uf_destino': cidade_uf(d), 'km': float(self._rotas[k])})
            self.t['rotas_km'].append(r)
        return self._rotas[k]

    def tarifas(self):
        """Tarifa por cliente × rota × tipo de veículo, na cascata que a aba lê.

        A tarifa tem de existir para as rotas que as viagens realmente fazem:
        rota sem tarifa cai em `ROTA SEM TARIFA` na Auditoria — o que é ótimo em
        pequena dose (mostra a auditoria funcionando) e péssimo em grande.
        """
        vistos = set()
        for v in self.viagens:
            chave = (v['tomador']['nome'], v['origem'][0], v['origem'][1],
                     v['destino'][0], v['destino'][1])
            if chave in vistos:
                continue
            vistos.add(chave)
            # 1 rota em 12 fica sem tarifa de propósito.
            if self.rnd.random() < 0.08:
                continue
            km = self.rota(v['origem'], v['destino'])
            base = round(km * self.rnd.uniform(4.1, 6.3), 2)
            r = esqueleto('tarifas_frete')
            r.update({
                'cliente_nome': v['tomador']['nome'],
                'cliente_id': v['tomador']['cnpj'][:8],
                'cidade_origem': v['origem'][0], 'uf_origem': v['origem'][1],
                'cidade_destino': v['destino'][0], 'uf_destino': v['destino'][1],
                'tipo_veiculo': self.rnd.choice(['CARRETA', 'CARRETA', 'TRUCK',
                                                 'BITREM', 'RODOTREM']),
                'frete_liquido': base,
                # GRIS e Ad Valorem são **frações**, não reais: o simulador da
                # tela faz `valor_da_nota * gris`. Emitir R$ aqui fazia o
                # adicional explodir (0,3% virava 36 "vezes" o valor da carga).
                # A variação vem do nome do cliente, não do sorteio, para não
                # deslocar a sequência aleatória do resto do gerador.
                'gris': round(0.0025 + (hash_estavel(v['tomador']['nome']) % 12) / 10000, 4),
                'ad_valorem': round(0.0008 + (hash_estavel(v['tomador']['cnpj']) % 9) / 10000, 4),
                'adicional_entrega': float(self.rnd.choice([0, 0, 150, 250])),
                'pedagio': float(round(km * 0.11, 2)),
                'valor_multiparada': float(self.rnd.choice([0, 0, 180])),
                'balsa': 0.0,
                'icms_incluso': self.rnd.choice(['Sim', 'Nao', 'Isento']),
                'pedagio_incluso': self.rnd.choice(['Sim', 'Nao', 'TAG']),
                'prazo_recebimento': self.rnd.choice([28, 30, 35, 45]),
                'icms_valor': 0.0,
                'versao_id': 3,
            })
            self.t['tarifas_frete'].append(r)

    # ── uma viagem ──
    def _sortear_conjunto(self, saida, km):
        """Cavalo, carreta e motorista LIVRES na hora da saída.

        Sortear sem olhar disponibilidade põe a mesma carreta em duas viagens ao
        mesmo tempo — fisicamente impossível, e o estrago não fica no documento:
        a trilha de GPS grava dois pontos da mesma placa no mesmo instante
        (a tabela tem `UNIQUE(placa, data_posicao)`, então a carga aborta) e o
        odômetro, que é cumulativo no aparelho, anda para trás. Foram exatamente
        essas as duas falhas do gate. A ocupação também é o que dá sentido ao
        conflito de CPF/placa que o Embarques checa no lançamento.
        """
        def livre(chave):
            return self._ocupado.get(chave, self.ref_min) <= saida

        cavs = [c for c in self.cavalos if livre(c['placa'])]
        cars = [c for c in self.carretas if livre(c['placa'])]
        mots = [m for m in self.u['motoristas'] if livre(m['cpf'])]
        if not (cavs and cars and mots):
            return None                      # frota toda na estrada: não sai viagem

        cav, car = self.rnd.choice(cavs), self.rnd.choice(cars)
        mot = self.rnd.choice(mots)
        # Ocupado até voltar: ida a ~550 km/dia, mais a volta e uma folga.
        dias = max(1, round(km / 550)) * 2 + 1
        ate = saida + timedelta(days=dias)
        for k in (cav['placa'], car['placa'], mot['cpf']):
            self._ocupado[k] = ate
        return cav, car, mot

    def viagem(self, dia):
        rnd = self.rnd
        sigla = rnd.choice(list(UNIDADES))
        o_nome, o_uf = UNIDADES[sigla]
        origem = next(c for c in CIDADES if c[0] == o_nome and c[1] == o_uf)
        destino = rnd.choice([c for c in CIDADES if c[0] != o_nome])
        # O destino define a unidade de destino quando existe filial na ponta.
        sigla_dest = next((s for s, (n, uf) in UNIDADES.items()
                           if n == destino[0]), rnd.choice(list(UNIDADES)))

        km = self.rota(origem, destino)
        saida = datetime.combine(dia, datetime.min.time()) + timedelta(
            hours=rnd.randint(5, 19), minutes=rnd.choice([0, 10, 20, 30, 40, 50]))

        conjunto = self._sortear_conjunto(saida, km)
        if conjunto is None:
            return None
        cav, car, mot = conjunto

        # Frota × Agregado × Carreteiro: a regra é a do projeto (dono das placas).
        dono_cav = self.por_placa[cav['placa']]['proprietario']
        dono_car = self.por_placa[car['placa']]['proprietario']
        n_rizza = (dono_cav == EMPRESA['nome']) + (dono_car == EMPRESA['nome'])
        tipo_op = 'FROTA' if n_rizza == 2 else ('AGREGADO' if n_rizza == 1 else 'CARRETEIRO')

        n_man, man = self.num.proximo(sigla, 'M')
        n_ctrb, _ = self.num.proximo(sigla, 'C')
        ctrb_num = f'{n_ctrb:06d}'

        tomador = rnd.choice(self.u['tomadores'])
        n_ctes = rnd.choices([1, 2, 3], weights=[70, 22, 8])[0]
        frete_total = round(km * rnd.uniform(6.1, 8.9), 2)
        peso = rnd.randint(18000, 31000)

        v = {
            'dia': dia, 'saida': saida,
            'sigla': sigla, 'sigla_dest': sigla_dest,
            'manifesto_num': man, 'manifesto': f'{sigla}{man}',
            'ctrb_num': ctrb_num, 'ctrb': f'{sigla}{ctrb_num}-{_dv(n_ctrb)}',
            'chave_ctrb': f'{sigla}{ctrb_num}',
            'cavalo': cav['placa'], 'carreta': car['placa'], 'motorista': mot,
            'origem': origem, 'destino': destino, 'km': km,
            'tipo_op': tipo_op, 'tomador': tomador, 'n_ctes': n_ctes,
            'frete': frete_total, 'peso': peso,
            'ctes': [],
            # defeitos plantados são marcados aqui e honrados pelos emissores
            'defeito': None,
            # preenchido depois, quando esta viagem for a 2ª perna de uma
            # continuação: o CTe dela aponta para o manifesto da 1ª
            'continua_de': None,
        }
        self.viagens.append(v)
        return v

    # ── documentos de uma viagem ──
    def emitir(self, v):
        rnd = self.rnd
        mot = v['motorista']
        cav = self.por_placa[v['cavalo']]

        # 1. MANIFESTO — quem abre a viagem (é dele que o robô nasce)
        r = esqueleto('manifestos')
        r.update({
            'sigla_manifesto': v['sigla'], 'numero_manifesto': v['manifesto_num'],
            'data_emissao': iso(v['saida']),
            'unidade_origem': v['sigla'], 'unidade_destino': v['sigla_dest'],
            'placa_cavalo': v['cavalo'], 'proprietario_cavalo': cav['proprietario'],
            'placa_carreta': v['carreta'],
            'cpf_motorista': mot['cpf'], 'nome_motorista': mot['nome'],
            'peso_total': float(v['peso']),
            'valor_total_mercadoria': round(v['frete'] * rnd.uniform(8, 25), 2),
            'valor_total_frete': v['frete'],
            'sigla_ctrb_os': v['sigla'], 'numero_ctrb_os': v['ctrb_num'],
            'data_importacao': iso(v['saida'] + timedelta(hours=rnd.randint(4, 9))),
            # As duas chaves calculadas do modelo — CIOT, Jornada e Verda leem daqui
            'CHAVE_MANIFESTO': v['manifesto'],
            'CHAVE_CTRB': v['chave_ctrb'],
        })
        if v['defeito'] == 'manifesto_sem_ctrb':
            r['numero_ctrb_os'] = '000000'
            r['CHAVE_CTRB'] = ''
        self.t['manifestos'].append(r)

        # 2. CTRB — a ordem de pagamento; é dele que sai a CIDADE da viagem
        if v['defeito'] != 'manifesto_sem_ctrb':
            pagar = 0.0 if v['tipo_op'] == 'FROTA' else round(v['frete'] * rnd.uniform(0.62, 0.78), 2)
            ciot = ''.join(str(rnd.randint(0, 9)) for _ in range(12))
            if v['defeito'] == 'ciot_erro':
                # O `ciot_erro()` do projeto só reconhece a falha se o texto
                # tiver "Mensagem recebida" — é o prefixo com que o SSW grava a
                # resposta da ANTT/Pamcard no lugar do código. Sem ele, a régua
                # classifica como "campo vazio", que é outra pendência.
                ciot = rnd.choice([
                    'Mensagem recebida: CPF/CNPJ do contratado nao habilitado na ANTT',
                    'Mensagem recebida: <br/>Veiculo nao vinculado ao RNTRC informado',
                    'Mensagem recebida: falha na comunicacao com o provedor (timeout)',
                ])
            elif v['defeito'] == 'sem_ciot':
                ciot = ''
            c = esqueleto('ctrbs_oss')
            c.update({
                'ctrb': v['ctrb'], 'tipo': 'NORMAL',
                'emissao': iso(v['saida'] + timedelta(hours=rnd.randint(1, 20))),
                'cidade_uf_origem': cidade_uf(v['origem']),
                'cidade_uf_destino': cidade_uf(v['destino']),
                'unidade_destino': v['sigla_dest'],
                'placa_cavalo': v['cavalo'], 'placa_carreta': v['carreta'],
                'propriedade': v['tipo_op'],
                'proprietario': cav['proprietario'],
                'cnpj_cpf_proprietario': EMPRESA['cnpj'] if v['tipo_op'] == 'FROTA'
                                         else dv_cnpj(''.join(str(rnd.randint(0, 9)) for _ in range(12))),
                'cpf_motorista': mot['cpf'], 'motorista': mot['nome'],
                'distancia_km': float(v['km']),
                'valor_a_pagar': pagar,
                'pedagio': float(round(v['km'] * 0.11, 2)),
                'vale_pedagio': float(round(v['km'] * 0.09, 2)),
                'ciot': ciot,
                'tabela_antt': f'{rnd.randint(1, 9)}' if v['tipo_op'] != 'FROTA' else '0',
                'manifesto': v['manifesto'],
                'peso_manifesto': float(v['peso']),
                'frete_manifesto': v['frete'],
                'total_eixos': 5.0,
                'Gera Custo': 'NAO' if v['tipo_op'] == 'FROTA' else 'SIM',
                'Tipo Operação': v['tipo_op'],
                'data_importacao': iso(v['saida'] + timedelta(hours=rnd.randint(5, 12))),
                'previsao_chegada': iso(v['saida'] + timedelta(days=4)),
            })
            ret = round(pagar * 0.0865, 2) if pagar else 0.0
            c.update({'sest_senat': round(pagar * 0.025, 2) if pagar else 0.0,
                      'inss': round(pagar * 0.045, 2) if pagar else 0.0,
                      'irrf': round(pagar * 0.015, 2) if pagar else 0.0,
                      'total_retencoes': ret,
                      'valor_liquido': round(pagar - ret, 2) if pagar else 0.0})
            self.t['ctrbs_oss'].append(c)

        # 3. CTe(s) — o faturamento ao tomador
        resto = v['frete']
        for i in range(v['n_ctes']):
            valor = round(resto / (v['n_ctes'] - i), 2)
            resto = round(resto - valor, 2)
            n_cte, cte = self.num.proximo(v['sigla'], 'T')
            tom = v['tomador'] if i == 0 else self.rnd.choice(self.u['tomadores'])
            dest_cid = v['destino'] if i == 0 else self.rnd.choice(CIDADES)
            aut = v['saida'] + timedelta(hours=self.rnd.randint(2, 26))
            tipo_doc, obs, fantasma = 'NORMAL', '', None
            if self.rnd.random() < 0.07:
                tipo_doc = self.rnd.choice(['COMPLEMENTAR FRETE', 'SUBC REC FORM LISO',
                                            'SUBSTITUTO'])
            if tipo_doc == 'SUBSTITUTO':
                tipo_doc, obs, fantasma = self._substituto(self.num, v['sigla'], self.rnd)
            cte_ref = f'{v["sigla"]}{cte}'
            v['ctes'].append(cte_ref)

            linha = {
                'serie_numero_ctrc': cte_ref, 'serie_numero_cte': cte_ref,
                'tipo_documento': tipo_doc,
                'unidade_emissora': v['sigla'], 'praca_expedidora': v['origem'][0],
                'data_emissao': iso(aut), 'hora_emissao': aut.strftime('%H:%M'),
                'data_autorizacao': iso(aut), 'hora_autorizacao': aut.strftime('%H:%M'),
                'cnpj_pagador': tom['cnpj'], 'cliente_pagador': tom['nome'],
                'cidade_pagador': tom['cidade'][0], 'uf_pagador': tom['cidade'][1],
                'cnpj_remetente': tom['cnpj'], 'cliente_remetente': tom['nome'],
                'cidade_remetente': v['origem'][0], 'uf_remetente': v['origem'][1],
                'cnpj_destinatario': dv_cnpj(''.join(str(self.rnd.randint(0, 9)) for _ in range(12))),
                'cliente_destinatario': f'{dest_cid[0]} DISTRIBUICAO LTDA',
                'cidade_destinatario': dest_cid[0], 'uf_destinatario': dest_cid[1],
                'cidade_entrega': dest_cid[0], 'uf_entrega': dest_cid[1],
                'uf_origem_prestacao': v['origem'][1],
                'cidade_origem_prestacao': v['origem'][0],
                # Numa continuação o CTe nasce no manifesto da PERNA 1 e termina
                # no da perna 2 — é essa diferença que o robô lê para ligar as
                # duas cargas e marcar a primeira como `Desengatada`.
                'primeiro_manifesto': (v['continua_de']['manifesto']
                                       if v.get('continua_de') else v['manifesto']),
                'ultimo_manifesto': v['manifesto'],
                'data_primeiro_manifesto': iso(v['continua_de']['saida']
                                               if v.get('continua_de') else v['saida']),
                'data_ultimo_manifesto': iso(v['saida']),
                'unidade_origem_primeiro_manifesto': v['sigla'],
                'unidade_destino_ultimo_manifesto': v['sigla_dest'],
                'placa_cavalo': v['cavalo'], 'placa_carreta': v['carreta'],
                'valor_frete': valor,
                'valor_frete_sem_icms': round(valor * 0.88, 2),
                'valor_icms': round(valor * 0.12, 2),
                'aliquota': 12.0,
                'base_calculo_icms_iss': valor,
                'peso_real_kg': float(v['peso'] // v['n_ctes']),
                'peso_calculado_kg': float(v['peso'] // v['n_ctes']),
                'frete_peso': round(valor * 0.9, 2),
                'gris': round(valor * 0.003, 2),
                'pedagio': round(v['km'] * 0.11 / v['n_ctes'], 2),
                'tipo_frete': self.rnd.choice(['CP', 'CV', 'FP', 'FV']),
                'tipo_calculo': 'PESO',
                'mercadoria': self.rnd.choice(['ALIMENTOS', 'BEBIDAS', 'EMBALAGENS',
                                               'PAPEL', 'QUIMICOS', 'CERAMICA']),
                'especie': 'PALLETS',
                'quantidade_volumes': float(self.rnd.randint(12, 34)),
                'numero_nota_fiscal': str(self.rnd.randint(100000, 999999)),
                'valor_mercadoria': round(valor * self.rnd.uniform(9, 22), 2),
                'distancia_km': float(v['km']),
                'UF_Origem_Brasil': v['origem'][1], 'UF_Destino_Brasil': dest_cid[1],
                'observacao': obs,
                'data_importacao': iso(aut + timedelta(hours=4)),
            }
            if fantasma:
                # o original esquecido: só no dataset da DRE, dias antes do substituto
                self._cte_historico(fantasma, v['sigla'], v['origem'], dest_cid, tom, v['km'],
                                    aut - timedelta(days=self.rnd.randint(2, 9)),
                                    round(valor * self.rnd.uniform(0.95, 1.05), 2),
                                    v['manifesto'], v['saida'], 'NORMAL', '')
            # A tabela existe nos DOIS datasets com shapes diferentes (102 × 149
            # colunas): a fixture é por (tabela, dataset), e o mesmo fato entra
            # nas duas com o esqueleto de cada uma.
            for variante in ('conhecimentos_emitidos.main', 'conhecimentos_emitidos.dre'):
                r = esqueleto(variante)
                r.update({k: val for k, val in linha.items() if k in r})
                self.t['conhecimentos_emitidos'].append((variante, r))

            mc = esqueleto('manifestos_ctrc')
            mc.update({
                'sigla_manifesto': v['sigla'], 'numero_manifesto': v['manifesto_num'],
                'sigla_ctrc': v['sigla'], 'numero_ctrc': cte,
                'valor_frete': valor, 'peso': float(v['peso'] // v['n_ctes']),
                'peso_real': float(v['peso'] // v['n_ctes']),
                'vlr_mercad': round(valor * 12, 2),
                'nome_destinatario': f'{dest_cid[0]} DISTRIBUICAO LTDA',
                'placa_veiculo': v['carreta'], 'praca_destino': dest_cid[0],
                'icms': round(valor * 0.12, 2),
                'CHAVE_CTRC': cte_ref, 'CHAVE_MANIFESTO': v['manifesto'],
                'data_importacao': iso(aut + timedelta(hours=4)),
            })
            self.t['manifestos_ctrc'].append(mc)

        # 4. AUDITORIA RECEITA — 1 linha por CTRB (o shape confirmou: 5.224 = 5.224)
        if v['defeito'] != 'manifesto_sem_ctrb':
            pagar = 0.0 if v['tipo_op'] == 'FROTA' else round(v['frete'] * rnd.uniform(0.62, 0.78), 2)
            tab = round(v['km'] * rnd.uniform(4.1, 6.3), 2)
            dif = round(v['frete'] - tab, 2)
            status = 'OK' if abs(dif) < tab * 0.03 else (
                'COBRADO A MAIOR' if dif > 0 else 'COBRADO A MENOR')
            if v['n_ctes'] > 2:
                status = 'MULTI-DESTINO - VALIDAR MANUALMENTE'
            a = esqueleto('Auditoria Receita')
            a.update({
                'CTRB': v['ctrb'], 'CTRC': v['ctes'][0] if v['ctes'] else '',
                'Manifesto': v['manifesto'],
                'Todos CTRCs da Viagem': ', '.join(v['ctes']),
                'Tipo Operacao': v['tipo_op'],
                'Gera Custo': 'NAO' if v['tipo_op'] == 'FROTA' else 'SIM',
                'data_ref_ctrc': iso(v['saida']),
                'receita_rateada': v['frete'],
                'valor_frete_sem_icms': round(v['frete'] * 0.88, 2),
                'valor_a_pagar': pagar,
                'frete_motorista_total': pagar,
                'resultado': round(v['frete'] - pagar, 2),
                'margem': round((v['frete'] - pagar) / v['frete'], 4) if v['frete'] else 0.0,
                'pedagio': float(round(v['km'] * 0.11, 2)),
                'vale_pedagio': float(round(v['km'] * 0.09, 2)),
                'distancia_km': v['km'],
                'motorista': mot['nome'],
                'placa_cavalo': v['cavalo'], 'placa_carreta': v['carreta'],
                'cnpj_pagador': v['tomador']['cnpj'],
                'cliente_pagador': v['tomador']['nome'],
                'cliente_tarifa': v['tomador']['nome'],
                'cidade_uf_origem': cidade_uf(v['origem']),
                'cidade_uf_destino': cidade_uf(v['destino']),
                'cidade_uf_origem_tarifa': cidade_uf(v['origem']),
                'cidade_uf_destino_tarifa': cidade_uf(v['destino']),
                'frete_tabela_gris': round(tab * 0.003, 2),
                'frete_tabela_pedagio': float(round(v['km'] * 0.11, 2)),
                'status_auditoria_frete': status,
            })
            self.t['Auditoria Receita'].append(a)

    # ── custos da viagem: abastecimento e pedágio ──
    def custos_viagem(self, v):
        rnd = self.rnd
        cav = self.por_placa[v['cavalo']]
        frota = cav['proprietario'] == EMPRESA['nome']
        if not frota:
            return                     # agregado abastece por conta própria

        # Hodômetro por placa, cumulativo — é dele que sai o km/L da aba Veículos.
        hodo = self._hodometro.setdefault(v['cavalo'], rnd.randint(180_000, 620_000))
        litros_km = rnd.uniform(2.2, 3.1)         # km por litro, por veículo/idade
        for i in range(rnd.choices([1, 2, 3], weights=[35, 45, 20])[0]):
            quando = v['saida'] + timedelta(hours=rnd.randint(1, 30) + i * 14)
            if quando.date() > self.ref:
                break
            trecho = v['km'] / rnd.uniform(1.4, 3.0)
            litros = round(trecho / litros_km, 2)
            hodo += int(trecho)
            cid = rnd.choice(CIDADES)
            preco = round(rnd.uniform(5.7, 6.9), 3)
            produto = rnd.choice(['DIESEL S-10', 'DIESEL S-10', 'DIESEL COMUM',
                                  'DIESEL S-50', 'DIESEL S10 ADITIVADO', 'ARLA 32'])
            eh_arla = produto.startswith('ARLA')
            r = esqueleto('abastecimentos_valecard')
            r.update({
                'placa': v['cavalo'],
                'motorista': v['motorista']['nome'],
                # O ValeCard NÃO tem hora — o dia é a resolução em que as fontes
                # se encontram (é o que a consolidação placa+dia explora).
                'dch_data': quando.strftime('%Y-%m-%d'),
                'produto': produto,
                # ARLA é aditivo, não combustível: sai em ~1 de cada 6 passagens,
                # a ~30% do volume de um tanque de diesel e mais barato por litro. Emitido com o
                # mesmo volume do diesel, ele respondia por até 28% do custo de
                # abastecimento e derrubava o resultado da frota sozinho.
                'ncd_quantidade': round(litros * 0.30, 2) if eh_arla else litros,
                'mcd_valor_unitario': round(preco * 0.72, 3) if eh_arla else preco,
                'mcd_valor_total': round((litros * 0.30 if eh_arla else litros)
                                         * (preco * 0.72 if eh_arla else preco), 2),
                'nsd_hodometro': float(hodo),
                'estabelecimento': f'POSTO {rnd.choice(["AVENIDA", "TRES IRMAOS", "BR", "PLANALTO"])}',
                'cidade': cid[0], 'uf': cid[1],
                'unidade': 'LT', 'filial': v['sigla'],
                'numero_cartao': f'****{rnd.randint(1000, 9999)}',
                'data_importacao': iso(quando + timedelta(days=1)),
            })
            self._hodometro[v['cavalo']] = hodo
            self.t['abastecimentos_valecard'].append(r)

        for _ in range(rnd.randint(1, 4)):
            quando = v['saida'] + timedelta(hours=rnd.randint(1, 40))
            if quando.date() > self.ref:
                break
            r = esqueleto('semparar_lancamentos')
            r.update({
                'numero_fatura': f'{rnd.randint(70000, 79999)}',
                'data': quando.strftime('%d/%m/%Y'),      # texto BR, não ISO
                'horario': quando.strftime('%H:%M'),
                'placa_veiculo': v['cavalo'],
                'tipo_veiculo': '5 EIXOS',
                'descricao': f'PRACA {rnd.choice(CIDADES)[0]}',
                'tipo_uso': 'PASSAGENS',
                'valor': round(rnd.uniform(9.4, 38.7), 2),
                'debito_credito': 'DB',
                'sentido_praca': rnd.choice(['Norte', 'Sul']),
                'embarcador': v['tomador']['nome'],
                'data_importacao': iso(quando + timedelta(days=2)),
            })
            self.t['semparar_lancamentos'].append(r)

    # ── folha dos motoristas ──
    def folha(self):
        """Uma linha por motorista por competência.

        Duas coisas de propósito: a **grafia do nome difere** da do manifesto
        (é por isso que o projeto casa por similaridade 0,82), e a **última
        competência fica sem lançar** — é o caso que exercita a provisão da aba
        Veículos, que usa o mês anterior quando o RH ainda não fechou.
        """
        rnd = self.rnd
        comp = []
        d = self.inicio()
        while d <= self.ref:
            comp.append(f'{d.year}-{d.month:02d}')
            d = (d.replace(day=28) + timedelta(days=5)).replace(day=1)
        comp = comp[:-1] or comp          # o mês corrente não tem folha lançada

        for i, m in enumerate(self.u['motoristas']):
            for c in comp:
                base = round(rnd.uniform(2400, 3600), 2)
                he = round(base * rnd.uniform(0.05, 0.38), 2)
                diarias = round(rnd.uniform(600, 2100), 2)
                inss = round(base * 0.11, 2)
                fgts = round(base * 0.08, 2)
                r = esqueleto('custo_pessoal')
                r.update({
                    'id': i * 100 + comp.index(c),
                    'competencia': c,
                    'nome': m['nome_folha'],        # grafia diferente de propósito
                    'funcao': m['funcao'],
                    'emp': 'NV', 'unidade': rnd.choice(list(UNIDADES)),
                    'area': 'Operacao', 'centro_custo': 'Transporte',
                    'salario_fixo': base, 'he': he, 'dsr': round(he * 0.18, 2),
                    'salario_liq': round(base + he - inss, 2),
                    'inss': inss, 'fgts': fgts,
                    'decimo_terceiro': round(base / 12, 2),
                    'ferias_mais_terco': round(base / 12 * 1.33, 2),
                    'diarias': diarias,
                    'alimentacao_dif': round(rnd.uniform(200, 460), 2),
                    'c_medico': round(rnd.uniform(180, 320), 2),
                    'c_odontologico': round(rnd.uniform(20, 48), 2),
                    'total_mes': round(base + he + diarias + inss + fgts, 2),
                    'inserted_at': iso(datetime(2026, 1, 5, 9, 0)),
                })
                self.t['custo_pessoal'].append(r)

    # ── despesa do 477 ──
    # Código do evento → descrição, com a descrição EXISTINDO no MAPA_DRE do
    # server.py. O código é o que a aba Veículos filtra (5150/5154 manutenção de
    # cavalo, 5153/5155 de carreta, 5402 seguro, 5411/5412 pneu, 55xx
    # financiamento); a descrição é o que a DRE agrupa. Os dois têm de casar, ou
    # o custo aparece numa tela e some na outra.
    EVENTOS_CUSTO = [
        ('5150', 'SERVICO MANUTENCAO CAVALOS'),
        ('5154', 'PECAS MANUTENCAO CAVALO'),
        ('5153', 'SERVICOS MANUTENCAO CARRETA'),
        ('5155', 'PECAS MANUTENCAO CARRETA'),
        ('5402', 'SEGURO DE VEICULOS'),
        ('5411', 'PNEUS E CAMARAS'),
        ('5412', 'RECAPAGEM DE PNEUS'),
        ('5512', 'INVESTIMENTO- CDC'),
        ('5513', 'INVESTIMENTO- FINAME'),
        ('5515', 'INVESTIMENTO - CONSORCIO'),
        ('5517', 'ATIVO IMOBILIZADO- VEICULOS'),
    ]

    # Quanto cada grupo da DRE pesa sobre a receita do mês. Sem isto a despesa
    # é sorteada solta e a demo mostra uma transportadora perdendo R$ 1 mi/mês —
    # ninguém se reconhece nisso, e a primeira pergunta do cliente destrói a
    # conversa. Os pesos deixam a margem final positiva e magra, que é o retrato
    # do setor. O combustível e o pedágio NÃO entram aqui: vêm do ValeCard e do
    # Sem Parar, fora do 477.
    PESO_GRUPO = {
        'Deduções':       0.135,   # ICMS/PIS/COFINS sobre o frete
        'Operacional':    0.56,    # manutenção, pneu, seguro, frete a terceiros
        'Administrativo': 0.175,
        'Financeiro':     0.015,
        'Impostos':       0.012,
        'Investimento':   0.030,   # fica FORA do resultado operacional
        'Retirada':       0.020,
    }

    def _receita_do_mes(self, ano, mes):
        return sum(v['frete'] for v in self.viagens
                   if v['dia'].year == ano and v['dia'].month == mes)

    # ── a história financeira (narrativa.py) ──
    def _fator_mes(self, d):
        """Volume do mês em relação ao mês de referência: tendência × sazonalidade × choque."""
        m, ref = date(d.year, d.month, 1), date(self.ref.year, self.ref.month, 1)
        return narrativa.fator(m) * narrativa.tendencia(m) / narrativa.tendencia(ref)

    def _base_mes(self, d, nivel):
        """Receita ESTRUTURAL do mês (sem sazonalidade nem choque): o que dimensiona a
        despesa fixa. Administrativo não cai em fevereiro porque o faturamento caiu."""
        ref = date(self.ref.year, self.ref.month, 1)
        return nivel * narrativa.tendencia(d) / narrativa.tendencia(ref)

    def _nivel_referencia(self):
        """Receita estrutural do mês de referência, medida nos meses FECHADOS da janela:
        é o que costura o histórico sintético à janela sem degrau na emenda."""
        fechados = [d for d in narrativa.meses(self.inicio(), self.ref)
                    if narrativa.proximo_mes(d) <= date(self.ref.year, self.ref.month, 1)]
        amostras = [self._receita_do_mes(d.year, d.month) / self._fator_mes(d) for d in fechados]
        return sum(amostras) / len(amostras) if amostras else VIAGENS_MES * 5000.0

    # Colunas que o histórico preenche. É o que as telas e a projeção leem da
    # receita; as outras ~100 colunas do dataset ficam fora da linha, e o demo_dax
    # as lê como vazias — sem isso seriam +30 MB de JSON só de colunas em branco.
    def historico_receita(self, nivel):
        """CTe do dataset da DRE de jan/2022 até o mês anterior à janela.

        Só o dataset da DRE: o robô do manifesto, o mapa e as telas operacionais leem o
        outro dataset e continuam com a janela — nada do que já funciona muda. Semente
        por mês: o passado sai igual todo dia. Devolve {mês: receita}.
        """
        fim = self.inicio()
        ref = date(self.ref.year, self.ref.month, 1)
        num = Numerador(inicio=0)             # abaixo da numeração da janela (10001+)
        novo = self.u['tomadores'][7]['raiz']   # o cliente que entra em mar/2025
        cidades_uni = [next(c for c in CIDADES if c[0] == n and c[1] == uf)
                       for n, uf in UNIDADES.values()]
        receita = {}
        for d in narrativa.meses(narrativa.INICIO_HISTORICO, fim - timedelta(days=1)):
            rnd = narrativa.rnd_mes(d, 'receita')
            alvo = nivel * narrativa.tendencia(d) / narrativa.tendencia(ref) * narrativa.fator(d)
            toms = [t for t in self.u['tomadores']
                    if d >= narrativa.CLIENTE_NOVO_DESDE or t['raiz'] != novo]
            dias = [d + timedelta(days=i) for i in range(31) if (d + timedelta(days=i)).month == d.month]
            peso_dia = [{5: 0.5, 6: 0.15}.get(x.weekday(), 1.0) for x in dias]
            soma = 0.0
            while soma < alvo:
                sigla = rnd.choice(list(UNIDADES))
                origem = cidades_uni[list(UNIDADES).index(sigla)]
                destino = rnd.choice([c for c in CIDADES if c[0] != origem[0]])
                km = self.rota(origem, destino)
                frete = round(km * rnd.uniform(6.1, 8.9), 2)
                dia = rnd.choices(dias, weights=peso_dia)[0]
                saida = datetime.combine(dia, datetime.min.time()) + timedelta(hours=rnd.randint(5, 19))
                _nm, man = num.proximo(sigla, 'M')
                n_ctes = rnd.choices([1, 2], weights=[80, 20])[0]
                resto = frete
                for i in range(n_ctes):
                    valor = round(resto / (n_ctes - i), 2)
                    resto = round(resto - valor, 2)
                    tom = rnd.choice(toms)
                    aut = saida + timedelta(hours=rnd.randint(2, 26))
                    _nc, cte = num.proximo(sigla, 'T')
                    tipo, obs = 'NORMAL', ''
                    sorteio = rnd.random()
                    if sorteio < 0.02:
                        tipo = 'COMPLEMENTAR FRETE'
                    elif sorteio < 0.03:
                        tipo, obs, fantasma = self._substituto(num, sigla, rnd)
                        if fantasma:
                            self._cte_historico(fantasma, sigla, origem, destino, tom, km,
                                                aut - timedelta(days=rnd.randint(2, 9)),
                                                round(valor * rnd.uniform(0.95, 1.05), 2),
                                                f'{sigla}{man}', saida, 'NORMAL', '')
                    self._cte_historico(f'{sigla}{cte}', sigla, origem, destino, tom, km, aut,
                                        valor, f'{sigla}{man}', saida, tipo, obs)
                soma += frete
            receita[d] = soma
        return receita

    def _substituto(self, num, sigla, rnd):
        """CTe substituto: a observação cita o original, como o ERP grava. Em ~1/3 dos
        casos o original fica esquecido na base — acontece na operação real, e é o que
        a projeção desconta (o cliente paga só o substituto)."""
        _n, orig = num.proximo(sigla, 'T')
        obs = f'CTRC EMITIDO PARA SUBSTITUIR O CTRC {sigla} {orig}'
        return 'SUBSTITUTO', obs, (f'{sigla}{orig}' if rnd.random() < 0.35 else None)

    def _cte_historico(self, ctrc, sigla, origem, destino, tom, km, aut, valor,
                       manifesto, saida, tipo, obs):
        self.t['conhecimentos_emitidos'].append(('conhecimentos_emitidos.dre', {
            'serie_numero_ctrc': ctrc, 'serie_numero_cte': ctrc, 'tipo_documento': tipo,
            'unidade_emissora': sigla, 'praca_expedidora': origem[0],
            'data_emissao': iso(aut), 'hora_emissao': aut.strftime('%H:%M'),
            'data_autorizacao': iso(aut), 'hora_autorizacao': aut.strftime('%H:%M'),
            'cnpj_pagador': tom['cnpj'], 'cliente_pagador': tom['nome'],
            'cidade_pagador': tom['cidade'][0], 'uf_pagador': tom['cidade'][1],
            'cnpj_remetente': tom['cnpj'], 'cliente_remetente': tom['nome'],
            'cidade_remetente': origem[0], 'uf_remetente': origem[1],
            'cliente_destinatario': f'{destino[0]} DISTRIBUICAO LTDA',
            'cidade_destinatario': destino[0], 'uf_destinatario': destino[1],
            'cidade_entrega': destino[0], 'uf_entrega': destino[1],
            'uf_origem_prestacao': origem[1], 'cidade_origem_prestacao': origem[0],
            'primeiro_manifesto': manifesto, 'ultimo_manifesto': manifesto,
            'data_primeiro_manifesto': iso(saida), 'data_ultimo_manifesto': iso(saida),
            'valor_frete': valor, 'valor_frete_sem_icms': round(valor * 0.88, 2),
            'valor_icms': round(valor * 0.12, 2), 'aliquota': 12.0,
            'base_calculo_icms_iss': valor, 'distancia_km': float(km),
            'mercadoria': 'CARGA GERAL', 'especie': 'PALLETS',
            'observacao': obs, 'data_importacao': iso(aut + timedelta(hours=4)),
        }))

    def _lanc477(self, mapa_dre, d, evento, descr, valor, fornecedor, rnd, hist='',
                 sit='LIQU', inclusao=None):
        """Uma linha do 477 na competência `d` (primeiro dia do mês)."""
        self._n477 = getattr(self, '_n477', 0) + 1
        grupo, sub = mapa_dre.get(descr, ('Operacional', 'Outros'))
        dia = d.replace(day=min(rnd.randint(2, 27), 28))
        emissao = datetime.combine(dia, datetime.min.time())
        inc = datetime.combine(inclusao, datetime.min.time()) if inclusao else emissao
        ref = f'{d.year}/{d.month:02d}'
        r = esqueleto('consulta_despesas_477')
        r.update({
            'empresa': '1', 'numlancto': f'{20000 + self._n477}', 'parcela': '01',
            'evento': evento, 'descr_evento': descr,
            'nome_fornecedor': fornecedor,
            'cnpj_fornecedor': dv_cnpj(''.join(str(rnd.randint(0, 9)) for _ in range(12))),
            'vlr_nota': valor, 'vlr_parcela': valor, 'vlr_final': valor,
            'valor_total_produtos': valor,
            'emissao': iso(emissao),
            'vencimen': iso(emissao + timedelta(days=28)),
            'inclusao': iso(inc),
            'mes_competencia': f'{d.month:02d}/{d.year % 100:02d}',   # MM/AA, como na base
            'REF': ref,                                               # AAAA/MM — o filtro da DRE
            'sit_des': sit,
            'uni': rnd.choice(list(UNIDADES)),
            'grupo': grupo, 'subgrupo': sub, 'classificacao_dre': grupo,
            'grupo_evento': f'{evento[0]} {grupo.upper()}',
            'fixo_variavel': 'Variavel' if grupo in ('Operacional', 'Deduções') else 'Fixo',
            'custo_despesa': ('Custo' if grupo == 'Operacional' else
                              'Investimento' if grupo == 'Investimento' else 'Despesa'),
            'historico_despesa': hist,
            'data_importacao': iso(inc),
            'periodo_relatorio': ref,
        })
        self.t['consulta_despesas_477'].append(r)
        return grupo

    # Custo que não acompanha a receita do mês: pico sazonal sobre a receita
    # ESTRUTURAL. É o que faz o EBITDA de dezembro e do 1º trimestre cair na demo
    # como cai na operação real (13º, férias, IPVA) — sem isso a margem era uma reta.
    PICOS = {12: [('13O SALARIOS', 0.045)],
             1: [('FERIAS', 0.020), ('IPVA', 0.009)],
             2: [('IPVA', 0.009)],
             3: [('IPVA', 0.009)]}

    def despesas(self, mapa_dre, meses_receita):
        """Despesa mensal do 477 para cada (mês, receita do mês, receita estrutural).

        O 477 **não traz placa**: a manutenção real só se liga ao veículo pelo
        texto do `historico_despesa` (match parcial, como em produção), e pneu é
        pool rateado. A demo reproduz isso — inventar uma coluna de placa aqui
        faria a tela mentir para melhor.

        Perfil: operacional e deduções acompanham a receita do mês (com ruído);
        administrativo acompanha a receita ESTRUTURAL (o porte da empresa, não o mês);
        investimento e dívida saem dos contratos de `narrativa.CONTRATOS`. Semente por
        mês: a despesa de um mês passado não muda de um dia para o outro.
        """
        placas_cav = [v['placa'] for v in self.cavalos
                      if v['proprietario'] == EMPRESA['nome']]
        placas_car = [v['placa'] for v in self.carretas
                      if v['proprietario'] == EMPRESA['nome']]
        eventos_contrato = {'5512', '5513', '5515', '5517'}
        for d, receita, base in meses_receita:
            rnd = narrativa.rnd_mes(d, 'despesa')
            liquidado = (self.ref - d).days > 45
            sit = 'LIQU' if liquidado else 'PEND'
            gasto = {}

            def lanc(evento, descr, valor, fornecedor, hist='', inclusao=None,
                     _d=d, _rnd=rnd, _sit=sit, _gasto=gasto):
                g = self._lanc477(mapa_dre, _d, evento, descr, valor, fornecedor, _rnd,
                                  hist=hist, sit=_sit, inclusao=inclusao)
                _gasto[g] = _gasto.get(g, 0.0) + valor

            orcamento = {g: receita * p for g, p in self.PESO_GRUPO.items()}
            orcamento['Operacional'] *= 1 + rnd.gauss(0, 0.025)
            orcamento['Deduções'] *= 1 + rnd.gauss(0, 0.02)
            orcamento['Administrativo'] = base * self.PESO_GRUPO['Administrativo'] * (1 + rnd.gauss(0, 0.03))
            orcamento['Investimento'] = receita * 0.003      # o grosso vem dos contratos

            for cod, descr in self.EVENTOS_CUSTO:
                if cod in eventos_contrato:
                    continue
                if cod in ('5150', '5154'):
                    for _ in range(rnd.randint(6, 14)):
                        p = rnd.choice(placas_cav)
                        lanc(cod, descr, round(rnd.uniform(280, 4200), 2),
                             'OFICINA ' + rnd.choice(['CENTRAL', 'DIESEL SUL', 'MECANICA NORTE']),
                             hist=f'OS {rnd.randint(1000,9999)} {p} REVISAO')
                elif cod in ('5153', '5155'):
                    for _ in range(rnd.randint(4, 9)):
                        p = rnd.choice(placas_car)
                        lanc(cod, descr, round(rnd.uniform(190, 2600), 2),
                             'OFICINA REBOQUES ' + rnd.choice(['LESTE', 'OESTE']),
                             hist=f'OS {rnd.randint(1000,9999)} {p} FREIO')
                elif cod == '5402':
                    lanc(cod, descr, round(receita * rnd.uniform(0.010, 0.014), 2), 'SEGURADORA MERIDIONAL S/A')
                    lanc(cod, descr, round(receita * rnd.uniform(0.004, 0.006), 2), 'TELEMETRIA VIA SAT LTDA')
                elif cod in ('5411', '5412'):
                    for _ in range(rnd.randint(3, 8)):
                        lanc(cod, descr, round(rnd.uniform(1200, 7800), 2),
                             'PNEUS ' + rnd.choice(['BRASIL', 'VIA SUL', 'RODOMAR']))

            # parcelas dos contratos ativos no mês (+ a entrada, no mês da compra)
            for cid, ev, descr, k, n_parc, valor, ini_c in narrativa.parcelas_do_mes(d):
                lanc(ev, descr, valor, 'BANCO ' + ('PLANALTO' if int(cid) % 2 else 'MERIDIONAL'),
                     hist=f'PARCELA {k}/{n_parc} CONTRATO {cid}', inclusao=ini_c)
                if k == 1 and cid in narrativa.ENTRADA_VEICULO:
                    lanc('5517', 'ATIVO IMOBILIZADO- VEICULOS', narrativa.ENTRADA_VEICULO[cid],
                         'CONCESSIONARIA RODOVIA NORTE', hist=f'ENTRADA CONTRATO {cid}')
                    orcamento['Investimento'] += narrativa.ENTRADA_VEICULO[cid]

            # picos sazonais: entram no orçamento E como lançamento próprio
            for descr, peso in self.PICOS.get(d.month, []):
                valor = round(base * peso * rnd.uniform(0.9, 1.1), 2)
                g = mapa_dre.get(descr, ('Administrativo', ''))[0]
                orcamento[g] = orcamento.get(g, 0.0) + valor
                lanc(f'{rnd.randint(5100, 5990)}', descr, valor, 'FOLHA DE PAGAMENTO')

            # O resto do plano divide o SALDO do orçamento do grupo, para que todos os
            # grupos apareçam na DRE sem estourar a margem. Peso aleatório por evento:
            # divisão igual deixaria o Pareto reto, e é o Pareto que mostra a
            # concentração — a informação que a tela vende.
            restantes = [(nome, g) for nome, (g, _s) in mapa_dre.items()
                         if nome not in {x for _c, x in self.EVENTOS_CUSTO}]
            pesos = {}
            for descr, grupo in restantes:
                pesos.setdefault(grupo, []).append((descr, rnd.uniform(0.4, 3.2)))
            for grupo, itens in pesos.items():
                saldo = max(0.0, orcamento.get(grupo, 0.0) - gasto.get(grupo, 0.0))
                total_peso = sum(p for _d, p in itens) or 1.0
                for descr, peso in itens:
                    valor = round(saldo * peso / total_peso, 2)
                    if valor < 1:
                        continue          # evento sem movimento no mês: não lança
                    lanc(f'{rnd.randint(5100, 5990)}', descr, valor,
                         'FORNECEDOR ' + rnd.choice(['ALFA', 'BETA', 'GAMA', 'DELTA']))

    # Provisões que o financeiro lança para os meses à frente e troca pelo custo real
    # quando ele chega: (descrição no MAPA_DRE, texto do histórico, fração da receita
    # estrutural, até quantos meses à frente o financeiro costuma lançar).
    PROVISOES = [
        ('FRETE TRANSFERENCIA C/ AGREGADOS', 'PROVISAO FRETE AGREGADOS', 0.16, 3),
        ('SALARIO MENSAL - OPERACIONAL',     'PREVISAO FOLHA OPERACIONAL', 0.05, 6),
        ('SALARIOS ADMINISTRATIVOS - APOIO', 'PREVISAO FOLHA ADMINISTRATIVA', 0.035, 12),
        ('ALUGUEL DO IMOVEL',                'PREVISAO ALUGUEL', 0.012, 12),
        ('SOFTWARE E LICENCAS',              'PREVISAO SISTEMAS', 0.006, 12),
        ('PLANO DE SAUDE',                   'PREVISAO PLANO DE SAUDE', 0.009, 6),
        ('COFINS',                           'PREVISAO COFINS', 0.050, 6),
        ('ICMS',                             'PREVISAO ICMS', 0.060, 6),
        ('PIS',                              'PREVISAO PIS', 0.011, 6),
    ]

    def despesas_futuras(self, mapa_dre, nivel):
        """O que o ERP já mostra para os meses à frente: parcelas de contrato (fato) e
        provisões do financeiro (estimativa em valor redondo, que ele troca pelo real)."""
        mes_ref = date(self.ref.year, self.ref.month, 1)
        for i, d in enumerate(narrativa.meses(narrativa.proximo_mes(mes_ref),
                                              narrativa.fim_dos_contratos()), start=1):
            rnd = narrativa.rnd_mes(d, 'futuro')
            for cid, ev, descr, k, n_parc, valor, ini_c in narrativa.parcelas_do_mes(d):
                self._lanc477(mapa_dre, d, ev, descr, valor,
                              'BANCO ' + ('PLANALTO' if int(cid) % 2 else 'MERIDIONAL'), rnd,
                              hist=f'PARCELA {k}/{n_parc} CONTRATO {cid}', sit='PEND', inclusao=ini_c)
            base = self._base_mes(d, nivel)
            lancado_em = self.ref - timedelta(days=rnd.randint(5, 40))
            for descr, hist, frac, alcance in self.PROVISOES:
                if i <= alcance and descr in mapa_dre:
                    valor = float(round(base * frac * rnd.uniform(0.9, 1.1), -3))
                    self._lanc477(mapa_dre, d, f'{rnd.randint(5100, 5990)}', descr, valor,
                                  'PROVISAO FINANCEIRO', rnd, hist=hist, sit='PEND',
                                  inclusao=lancado_em)

    # ── ordens de coleta (a fita monta a aba /embarques/ordens a partir daqui) ──
    def coletas(self):
        rnd = self.rnd
        recentes = [v for v in self.viagens if (self.ref - v['dia']).days <= 12]
        for v in recentes:
            if rnd.random() > 0.55:
                continue
            tom = v['tomador']
            abertura = v['saida'] - timedelta(hours=rnd.randint(8, 40))
            # A `situacao` do SSW não fecha sozinha — é por isso que a aba deriva
            # o estado. A demo mantém o vício: COMANDADA que já virou carga.
            sit = rnd.choices(['COMANDADA', 'COLETADA', 'CADASTRADA', 'CANCELADA'],
                              weights=[50, 30, 15, 5])[0]
            n_col = self.num.proximo(v['sigla'], 'O')[0]
            r = esqueleto('coletas_0157')
            r.update({
                'unidade': v['sigla'], 'numero': f'{n_col:06d}', 'tipo': 'NORMAL',
                'situacao': sit,
                'situacao_em': iso(abertura + timedelta(hours=2)),
                'data_limite_inicial': iso(v['saida']),
                'limite_em': iso(v['saida'] + timedelta(hours=6)),
                'reme_cnpj': tom['cnpj'], 'reme_nome': tom['nome'],
                'reme_endereco': f'ROD BR {rnd.randint(100,499)} KM {rnd.randint(2,180)}',
                'reme_bairro': 'DISTRITO INDUSTRIAL',
                'reme_cep': f'{rnd.randint(1000000, 9999999):08d}',
                'reme_cidade': v['origem'][0],
                'dest_cnpj': dv_cnpj(''.join(str(rnd.randint(0, 9)) for _ in range(12))),
                'dest_nome': f'{v["destino"][0]} DISTRIBUICAO LTDA',
                'dest_endereco': f'AV DAS INDUSTRIAS {rnd.randint(100,4000)}',
                'dest_cidade': v['destino'][0], 'dest_uf': v['destino'][1],
                'dest_cep': f'{rnd.randint(1000000, 9999999):08d}',
                'solicitante': rnd.choice(['renato', 'pablo', 'rafael']),
                'motorista': v['motorista']['nome'],
                'veiculo': v['cavalo'], 'veiculo_2': v['carreta'],
                'peso_kg': float(v['peso']), 'val_merc': round(v['frete'] * 12, 2),
                'qtde_vol': float(rnd.randint(12, 34)),
                'mercadoria': 'CARGA GERAL', 'tipo_frete': 'CIF',
                'cadastrada_em': iso(abertura), 'cadastrada_por': 'integracao',
                'comandada_em': iso(abertura + timedelta(hours=1)),
                'comandada_por': rnd.choice(['renato', 'pablo']),
                'coletada_em': iso(v['saida']) if sit == 'COLETADA' else '',
                'ctrc_gerado': v['ctes'][0] if (sit == 'COLETADA' and v['ctes']) else '',
                'qtd_ocorrencias': 0.0,
                'data_importacao': iso(v['saida'] + timedelta(hours=6)),
            })
            self.t['coletas_0157'].append(r)

    def _plantar_continuacoes(self):
        """Liga pares de viagens em que a MESMA mercadoria seguiu em duas cargas.

        Em campo isso é ~8% das viagens: o cavalo larga a carreta carregada na
        filial e outro a leva, ou o conjunto ganha manifesto novo no hub. O CTe
        é quem prova — ele nasce no primeiro manifesto e termina no último. É
        desse par que saem os cards `Desengatadas` e `Vazias`, e sem ele os dois
        ficam zerados para sempre.
        """
        rnd = self.rnd
        por_dia, self._por_carreta = {}, {}
        for v in self.viagens:
            por_dia.setdefault(v['dia'], []).append(v)
            self._por_carreta.setdefault(v['carreta'], []).append(v)
        pares = 0
        for dia in sorted(por_dia):
            candidatas = por_dia[dia]
            if len(candidatas) < 4:
                continue
            for a in candidatas:
                if pares >= len(self.viagens) // 13:      # ~8% das viagens
                    break
                if a.get('continua_de') or a.get('_continuada'):
                    continue
                # Só serve A que TERMINA numa filial: toda viagem parte de uma
                # das cinco sedes, então é lá que existe perna seguinte — e é o
                # pátio onde a carreta carregada realmente fica esperando outro
                # cavalo. Sem este filtro, quase nenhum par casava.
                if a['destino'][0] not in {n for n, _uf in UNIDADES.values()}:
                    continue
                # a 2ª perna sai do DESTINO da 1ª, dias depois, com outra carreta
                # A régua do projeto exige a MESMA carreta nas duas pernas: o
                # desengate troca o CAVALO e a carreta segue com a mercadoria.
                # Procurar carreta diferente (como eu fazia) cai em "transbordo
                # — sem ligação", que é justamente o caso que o desenho recusa.
                # `self._ocupado` reserva a carreta pela ida E pela volta — mas
                # num desengate ela não volta, fica no pátio esperando outro
                # cavalo. Usar aquela reserva descartava quase todos os pares.
                # O que importa de fato é não haver OUTRA viagem usando essa
                # carreta no intervalo: duas viagens da mesma placa ao mesmo
                # tempo fazem o odômetro andar para trás (o gate pega).
                chegada_a = a['saida'] + timedelta(days=max(1, round(a['km'] / 550)))
                usos = self._por_carreta.get(a['carreta'], ())
                segs = []
                for b in self.viagens:
                    if (b is a or b.get('continua_de') or b.get('_continuada')
                            or b['origem'] != a['destino']
                            or b['cavalo'] == a['cavalo']
                            or not (chegada_a <= b['saida'])
                            or (b['dia'] - a['dia']).days > 12):
                        continue
                    fim_b = b['saida'] + timedelta(days=max(1, round(b['km'] / 550)) + 1)
                    if any(c is not a and a['saida'] < c['saida'] < fim_b for c in usos):
                        continue        # a carreta tem outra viagem no meio
                    segs.append(b)
                if not segs:
                    continue
                b = rnd.choice(segs)
                # a carreta de A segue na perna B — é o que prova a continuação.
                # A troca é segura porque B parte depois de A ter chegado, então
                # as duas trilhas de GPS não se sobrepõem no tempo.
                b['carreta'] = a['carreta']
                a['_continuada'] = True
                b['continua_de'] = a
                pares += 1
        return pares

    # ── plantio de defeitos ──
    def _plantar_defeitos(self):
        """A demo mostra o sistema PEGANDO problema — base toda verde não vende.

        Os defeitos são plantados na viagem, não na tabela: assim o CIOT acha a
        pendência pela régua dele, não porque alguém escreveu a pendência.
        """
        recentes = [v for v in self.viagens
                    if (self.ref - v['dia']).days <= 20]
        self.rnd.shuffle(recentes)
        plano = (['sem_ciot'] * 4 + ['ciot_erro'] * 3 + ['manifesto_sem_ctrb'] * 2)
        for v, d in zip(recentes, plano):
            v['defeito'] = d
        return dict((d, plano.count(d)) for d in set(plano))

    # ── laço principal ──
    def rodar(self):
        self.cadastros()
        dia = self.inicio()
        n_dias = (self.ref - dia).days + 1
        por_dia = max(1, round(self.viagens_mes * self.meses / n_dias))
        while dia <= self.ref:
            # Sábado tem metade do movimento; domingo quase nada.
            fator = {5: 0.5, 6: 0.15}.get(dia.weekday(), 1.0)
            # E cada mês segue a história da empresa (tendência × sazonalidade ×
            # choque) — com a janela plana, a projeção via uma reta e acertava 99%.
            # Arredondamento sorteado: com ~6 viagens/dia, `round` comeria a variação.
            x = por_dia * fator * self._fator_mes(dia)
            for _ in range(int(x) + (self.rnd.random() < x - int(x))):
                self.viagem(dia)
            dia += timedelta(days=1)

        pares = self._plantar_continuacoes()
        defeitos = self._plantar_defeitos()
        defeitos['continuacoes'] = pares
        for v in self.viagens:
            self.emitir(v)
            self.custos_viagem(v)
        self.tarifas()
        self.folha()
        self.coletas()

        # O passado e o futuro financeiro (aba Projeção): histórico de receita e
        # despesa desde jan/2022 e as parcelas/provisões dos meses à frente. A
        # despesa da janela e a do histórico saem da MESMA função e do mesmo perfil.
        mapa = _mapa_dre()
        nivel = self._nivel_referencia()
        rec_hist = self.historico_receita(nivel)
        meses_desp = [(d, r, self._base_mes(d, nivel)) for d, r in sorted(rec_hist.items())]
        meses_desp += [(d, self._receita_do_mes(d.year, d.month) or 1.0, self._base_mes(d, nivel))
                       for d in narrativa.meses(self.inicio(), self.ref)]
        self.despesas(mapa, meses_desp)
        self.despesas_futuras(mapa, nivel)
        return defeitos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--resumo', action='store_true', help='conta, sem gravar')
    ap.add_argument('--meses', type=int, default=MESES)
    args = ap.parse_args()

    g = Gerador(meses=args.meses)
    defeitos = g.rodar()

    from trilha import Trilha
    tr = Trilha(g)
    resumo_gps = tr.rodar()
    g.t['embarques_posicoes_historico'] = tr.historico
    g.t['embarques_simulacao'] = tr.simulacao
    g.t['embarques_veiculos_rastreio'] = [
        {'placa': k, 'id_veiculo_3s': i} for k, i in sorted(tr.cadastro.items())]

    print(f"{EMPRESA['nome']} — {g.inicio()} .. {g.ref}")
    print(f"  {len(g.viagens)} viagens")
    for k, v in sorted(g.t.items()):
        if v:
            print(f"    {k:28s} {len(v):>7d}")
    print(f"  defeitos plantados: {defeitos}")
    print(f"  GPS: {resumo_gps}")

    if args.resumo:
        return 0

    os.makedirs(FIXTURES, exist_ok=True)

    def _gravar(nome, dados):
        # Atômico (temporário + os.replace): em produção o servidor está no ar enquanto a
        # atualização diária regera tudo, e o demo_dax relê a fixture quando ela muda —
        # sem isso ele podia pegar um JSON pela metade.
        destino = os.path.join(FIXTURES, f'{nome}.json')
        with open(destino + '.tmp', 'w', encoding='utf-8') as fh:
            json.dump(dados, fh, ensure_ascii=False)
        os.replace(destino + '.tmp', destino)

    for nome, linhas in g.t.items():
        if not linhas:
            continue
        if nome == 'conhecimentos_emitidos':          # sai em duas variantes
            for variante in ('conhecimentos_emitidos.main', 'conhecimentos_emitidos.dre'):
                _gravar(variante, [r for v, r in linhas if v == variante])
            continue
        _gravar(nome, linhas)
    print(f'\nGravado em {FIXTURES}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
