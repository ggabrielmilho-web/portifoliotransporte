"""A transportadora fictícia da base demo.

Tudo aqui é inventado com semente fixa: rodar duas vezes produz exatamente a
mesma empresa, o que faz a demo ser reproduzível e os prints continuarem
valendo. Nenhum nome, CNPJ, CPF ou placa tem relação com cliente real.

Duas coisas são REAIS de propósito, e não são dado de ninguém:

* **as cidades e suas coordenadas** — o mapa, a rota do ORS e o geocoding por
  centroide do IBGE precisam de município que exista. Cidade inventada deixaria
  a tela de rastreamento sem linha e sem ETA, que é justamente o que a demo tem
  de mostrar funcionando;
* **os dígitos verificadores de CPF/CNPJ** — o `verda_payload.py` valida DV
  antes de montar o envio. Documento com DV errado apareceria como pendência na
  aba Verda e leria como defeito do sistema.

A rede de rotas é um corredor Sudeste–Centro-Oeste com pontas no Nordeste e no
Sul: é o que produz viagem longa (para o PGR ter estrada), viagem curta (para
haver mais de uma por semana no mesmo cavalo) e retorno vazio.
"""

import random

SEMENTE = 20260922

# ── A empresa ─────────────────────────────────────────────────────────────
EMPRESA = {
    'nome': 'NORTEVIA TRANSPORTES LTDA',
    'curto': 'Nortevia',
    'cnpj': '31447902000164',          # fictício, DV válido (conferido abaixo)
}

# Filiais: sigla de 3 letras (o SSW numera documento por unidade) + cidade sede.
UNIDADES = {
    'CAM': ('CAMPINAS', 'SP'),
    'BHO': ('BETIM', 'MG'),
    'GOI': ('APARECIDA DE GOIANIA', 'GO'),
    'VIT': ('SERRA', 'ES'),
    'CUR': ('SAO JOSE DOS PINHAIS', 'PR'),
}

# ── Cidades reais (nome, UF, lat, lon) ────────────────────────────────────
# Centroides aproximados. O `import_municipios.py` do projeto traz os oficiais
# do IBGE para o banco; estes são só a semente do gerador.
CIDADES = [
    ('CAMPINAS', 'SP', -22.9099, -47.0626),
    ('SAO PAULO', 'SP', -23.5505, -46.6333),
    ('GUARULHOS', 'SP', -23.4538, -46.5333),
    ('RIBEIRAO PRETO', 'SP', -21.1775, -47.8103),
    ('SAO JOSE DO RIO PRETO', 'SP', -20.8113, -49.3758),
    ('BAURU', 'SP', -22.3147, -49.0606),
    ('SOROCABA', 'SP', -23.5015, -47.4526),
    ('SANTOS', 'SP', -23.9608, -46.3336),
    ('JUNDIAI', 'SP', -23.1857, -46.8978),
    ('BETIM', 'MG', -19.9678, -44.1983),
    ('BELO HORIZONTE', 'MG', -19.9167, -43.9345),
    ('UBERLANDIA', 'MG', -18.9186, -48.2772),
    ('UBERABA', 'MG', -19.7472, -47.9381),
    ('JUIZ DE FORA', 'MG', -21.7642, -43.3503),
    ('MONTES CLAROS', 'MG', -16.7350, -43.8617),
    ('GOVERNADOR VALADARES', 'MG', -18.8511, -41.9494),
    ('APARECIDA DE GOIANIA', 'GO', -16.8239, -49.2439),
    ('GOIANIA', 'GO', -16.6869, -49.2648),
    ('ANAPOLIS', 'GO', -16.3267, -48.9528),
    ('RIO VERDE', 'GO', -17.7975, -50.9300),
    ('CATALAO', 'GO', -18.1661, -47.9414),
    ('BRASILIA', 'DF', -15.7939, -47.8828),
    ('SERRA', 'ES', -20.1211, -40.3078),
    ('VILA VELHA', 'ES', -20.3297, -40.2925),
    ('CARIACICA', 'ES', -20.2639, -40.4200),
    ('RIO DE JANEIRO', 'RJ', -22.9068, -43.1729),
    ('DUQUE DE CAXIAS', 'RJ', -22.7858, -43.3117),
    ('CAMPOS DOS GOYTACAZES', 'RJ', -21.7622, -41.3181),
    ('SAO JOSE DOS PINHAIS', 'PR', -25.5305, -49.2064),
    ('CURITIBA', 'PR', -25.4284, -49.2733),
    ('LONDRINA', 'PR', -23.3045, -51.1696),
    ('MARINGA', 'PR', -23.4253, -51.9386),
    ('JOINVILLE', 'SC', -26.3044, -48.8456),
    ('PORTO ALEGRE', 'RS', -30.0346, -51.2177),
    ('SALVADOR', 'BA', -12.9777, -38.5016),
    ('FEIRA DE SANTANA', 'BA', -12.2664, -38.9663),
    ('RECIFE', 'PE', -8.0476, -34.8770),
    ('FORTALEZA', 'CE', -3.7319, -38.5267),
    ('CUIABA', 'MT', -15.6014, -56.0979),
    ('RONDONOPOLIS', 'MT', -16.4673, -54.6372),
]
POR_NOME = {(c[0], c[1]): c for c in CIDADES}


def cidade_uf(c):
    """('CAMPINAS','SP') -> 'CAMPINAS/SP' — o formato que as telas usam."""
    return f'{c[0]}/{c[1]}'


# ── Dígito verificador (CPF/CNPJ) ─────────────────────────────────────────
# O verda_payload valida antes de montar o envio; documento com DV torto
# apareceria como pendência e leria como defeito do sistema.
def dv_cpf(base9):
    d = [int(x) for x in base9]
    for peso_ini in (10, 11):
        s = sum(v * (peso_ini - i) for i, v in enumerate(d))
        r = (s * 10) % 11
        d.append(0 if r == 10 else r)
    return ''.join(map(str, d))


def dv_cnpj(base12):
    d = [int(x) for x in base12]
    for pesos in ([5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
                  [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]):
        s = sum(v * p for v, p in zip(d, pesos))
        r = s % 11
        d.append(0 if r < 2 else 11 - r)
    return ''.join(map(str, d))


# ── Placas ────────────────────────────────────────────────────────────────
# O shape mostrou que `placa_cavalo` vem só em Mercosul e `placa_carreta` nas
# DUAS grafias, na mesma coluna. A demo reproduz a mistura de propósito: é ela
# que exercita a normalização (`_placa_mercosul`), e é um diferencial que se
# mostra na tela de Veículos.
_L = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
_MERC = 'ABCDEFGHIJ'          # 0=A … 9=J, a conversão do 5º caractere


def _placa_antiga(rnd):
    return ''.join(rnd.choice(_L) for _ in range(3)) + f'{rnd.randint(0, 9999):04d}'


def para_mercosul(placa):
    """'ABC1234' -> 'ABC1C34' (só o 5º caractere muda: dígito -> letra)."""
    if len(placa) != 7 or not placa[4].isdigit():
        return placa
    return placa[:4] + _MERC[int(placa[4])] + placa[5:]


# ── Nomes fictícios ───────────────────────────────────────────────────────
_PRE = ['Adriano', 'Benedito', 'Caio', 'Divino', 'Edson', 'Fabio', 'Gilmar',
        'Hamilton', 'Ivan', 'Jair', 'Kleber', 'Luciano', 'Marcelo', 'Nilson',
        'Osmar', 'Paulo', 'Reinaldo', 'Sebastiao', 'Tarcisio', 'Valdir',
        'Wagner', 'Anderson', 'Cleber', 'Douglas', 'Elias', 'Joel']
_SOB = ['Alves', 'Barbosa', 'Cardoso', 'Dias', 'Estevam', 'Ferreira', 'Gomes',
        'Henrique', 'Inacio', 'Junqueira', 'Lima', 'Moreira', 'Nogueira',
        'Oliveira', 'Pacheco', 'Queiroz', 'Ramos', 'Silveira', 'Teixeira',
        'Vasconcelos', 'Xavier', 'Zanetti']

_TOM_A = ['Alimentos', 'Bebidas', 'Quimica', 'Papel', 'Ceramica', 'Metais',
          'Agro', 'Laticinios', 'Embalagens', 'Higiene', 'Rações', 'Moveis',
          'Ferragens', 'Plasticos', 'Graos']
_TOM_B = ['Aurora', 'Bandeirante', 'Cristalina', 'Dourado', 'Esmeralda',
          'Farol', 'Guapore', 'Horizonte', 'Ipiranga Sul', 'Jacaranda',
          'Lumiar', 'Marajo', 'Norte Claro', 'Ouro Fino', 'Pampa']


def universo():
    """Monta o universo inteiro de uma vez, determinístico pela semente."""
    rnd = random.Random(SEMENTE)

    # ── Motoristas: nome + CPF com DV válido ──
    motoristas = []
    usados = set()
    for i in range(26):
        nome = f'{_PRE[i]} {rnd.choice(_SOB)} {rnd.choice(_SOB)}'
        while True:
            base = ''.join(str(rnd.randint(0, 9)) for _ in range(9))
            cpf = dv_cpf(base)
            if cpf not in usados:
                usados.add(cpf)
                break
        motoristas.append({
            'nome': nome.upper(),
            'nome_folha': nome,            # a folha vem com capitalização diferente
            'cpf': cpf,
            'funcao': 'Motorista' if i % 5 else 'Motorista de Caminhao Truck',
        })

    # ── Proprietários: a frota + agregados + carreteiros ──
    # `FROTA_PROPRIETARIO_PATTERN` (env, passo do layout) casa com este nome.
    proprietarios = [EMPRESA['nome']]
    for i in range(8):
        nome = f"TRANSPORTES {rnd.choice(_TOM_B).upper()} LTDA"
        if nome not in proprietarios:
            proprietarios.append(nome)

    # ── Frota ──
    veiculos = []
    placas = set()

    def _nova_placa(mercosul):
        while True:
            p = _placa_antiga(rnd)
            p = para_mercosul(p) if mercosul else p
            if p not in placas:
                placas.add(p)
                return p

    # Cavalos: 22 da frota + 10 de agregado. Sempre Mercosul (como na base real).
    for i in range(32):
        frota = i < 22
        veiculos.append({
            'placa': _nova_placa(True),
            'tipo': 'CAVALO' if i % 4 else 'CAVALO TRUCADO',
            'relacionamento': 'FROTA' if frota else 'AGREGADO',
            'proprietario': EMPRESA['nome'] if frota else rnd.choice(proprietarios[1:]),
            'modelo': rnd.choice(['FH 460', 'FH 540', 'R 450', 'ACTROS 2651',
                                  'CONSTELLATION 25.460', 'AXOR 2544']),
            'ano': rnd.randint(2016, 2025),
            'eixos': rnd.choice(['6X2', '6X4']),
        })
    # Carretas: 34, metade na grafia antiga (a mistura é o ponto).
    for i in range(34):
        frota = i < 26
        veiculos.append({
            'placa': _nova_placa(i % 2 == 0),
            'tipo': 'CARRETA',
            'relacionamento': 'FROTA' if frota else 'AGREGADO',
            'proprietario': EMPRESA['nome'] if frota else rnd.choice(proprietarios[1:]),
            'modelo': rnd.choice(['SR GRANELEIRO', 'SR BAU', 'SR SIDER',
                                  'SR TANQUE', 'SR PORTA CONTAINER']),
            'ano': rnd.randint(2014, 2025),
            'eixos': '3 EIXOS',
        })
    # Trucks: rodam sem manifesto (é o caso que a Jornada cobre pelo ValeCard).
    for i in range(6):
        veiculos.append({
            'placa': _nova_placa(True),
            'tipo': 'TRUCK',
            'relacionamento': 'FROTA',
            'proprietario': EMPRESA['nome'],
            'modelo': rnd.choice(['ATEGO 2426', 'CONSTELLATION 24.280']),
            'ano': rnd.randint(2015, 2024),
            'eixos': '6X2',
        })

    # ── Tomadores ──
    tomadores = []
    vistos = set()
    for i in range(15):
        nome = f'{_TOM_B[i]} {_TOM_A[i]} S/A'.upper()
        raiz = ''.join(str(rnd.randint(0, 9)) for _ in range(8))
        # Duas filiais para um deles: é o caso que prova a consolidação por raiz
        # de CNPJ no Faturamento (a mesma viagem faturada em duas inscrições).
        filiais = ['0001', '0002'] if i == 3 else ['0001']
        for f in filiais:
            cnpj = dv_cnpj(raiz + f)
            if cnpj in vistos:
                continue
            vistos.add(cnpj)
            tomadores.append({
                'nome': nome, 'cnpj': cnpj, 'raiz': raiz,
                'cidade': rnd.choice(CIDADES),
            })

    return {
        'empresa': EMPRESA,
        'unidades': UNIDADES,
        'cidades': CIDADES,
        'motoristas': motoristas,
        'veiculos': veiculos,
        'tomadores': tomadores,
        'proprietarios': proprietarios,
    }


if __name__ == '__main__':
    u = universo()
    print(f"{u['empresa']['nome']} — CNPJ {u['empresa']['cnpj']}")
    print(f"  {len(u['veiculos'])} veiculos "
          f"({sum(1 for v in u['veiculos'] if v['relacionamento'] == 'FROTA')} frota)")
    print(f"  {len(u['motoristas'])} motoristas · {len(u['tomadores'])} tomadores "
          f"· {len(u['cidades'])} cidades · {len(u['unidades'])} unidades")
    cav = [v['placa'] for v in u['veiculos'] if v['tipo'].startswith('CAVALO')][:3]
    car = [v['placa'] for v in u['veiculos'] if v['tipo'] == 'CARRETA'][:4]
    print(f"  exemplo cavalos: {cav}")
    print(f"  exemplo carretas (as duas grafias): {car}")
