"""A história financeira da NORTEVIA — o que dá à base um PASSADO que a projeção lê.

A aba Projeção precisa de ~5 anos de receita e despesa (36 meses de janela + 24
pontos de teste às cegas) e de parcelas futuras já contratadas. A janela do gerador
tem 9 meses e desliza com a data, então o passado mora aqui: tudo o que é função do
MÊS, com semente POR MÊS — um mês que já passou sai idêntico todo dia, e a projeção
só se move com o mês corrente, como na vida real.

A história, calibrada para o modelo errar no passado mais ou menos o que erra na
operação real que inspirou a vitrine (6–9% no mês seguinte):

* crescimento orgânico de ~12% ao ano desde jan/2022;
* sazonalidade de transportadora: fevereiro e abril fracos, outubro/novembro fortes;
* choque mensal de ~5% (ninguém fatura em linha reta);
* um CLIENTE NOVO grande a partir de mar/2025 (+8% de patamar) — é o degrau que
  nenhum modelo prevê e que a tela da projeção admite não enxergar.
"""

import random
import zlib
from datetime import date

INICIO_HISTORICO = date(2022, 1, 1)
CRESCIMENTO_ANO = 0.12
CLIENTE_NOVO_DESDE = date(2025, 3, 1)
CLIENTE_NOVO_PESO = 0.08          # patamar que o cliente novo acrescenta
CHOQUE_DESVIO = 0.05

# Fator de cada mês (média 1): fevereiro curto e pós-carnaval, abril com feriados,
# outubro e novembro com a safra de fim de ano da indústria.
SAZONALIDADE = {1: 0.97, 2: 0.90, 3: 1.04, 4: 0.95, 5: 1.02, 6: 0.98,
                7: 1.00, 8: 1.03, 9: 1.02, 10: 1.07, 11: 1.06, 12: 0.96}


def semente(*partes):
    """Semente estável (o `hash()` do Python muda por processo)."""
    return zlib.crc32('|'.join(str(p) for p in partes).encode('utf-8'))


def rnd_mes(d, tema):
    """Gerador próprio de cada (mês, tema): o passado não muda quando a janela anda."""
    return random.Random(semente(tema, d.year, d.month))


def meses_desde_inicio(d):
    return (d.year - INICIO_HISTORICO.year) * 12 + (d.month - INICIO_HISTORICO.month)


def tendencia(d):
    """Nível estrutural do mês (sem sazonalidade nem choque), em múltiplos de jan/2022."""
    t = meses_desde_inicio(d) / 12
    degrau = (1 + CLIENTE_NOVO_PESO) if d >= CLIENTE_NOVO_DESDE else 1.0
    return (1 + CRESCIMENTO_ANO) ** t * degrau


def choque(d):
    return rnd_mes(d, 'choque').gauss(0, CHOQUE_DESVIO)


def fator(d):
    """Quanto o mês fatura em relação à tendência: sazonalidade × choque."""
    return SAZONALIDADE[d.month] * (1 + choque(d))


def proximo_mes(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def meses(ini, fim):
    """Primeiros dias dos meses de `ini` a `fim`, inclusive."""
    out, d = [], date(ini.year, ini.month, 1)
    while d <= fim:
        out.append(d)
        d = proximo_mes(d)
    return out


# ── Contratos de financiamento ────────────────────────────────────────────
# As parcelas mensais saem daqui para o 477 — as passadas como despesa liquidada, as
# futuras como PENDENTE lançada no início do contrato (é o "já contratado" da
# projeção e a escada de compromissos). O evento casa com o MAPA_DRE do server.py:
# Investimento para a compra de veículo, Financeiro para dívida de capital de giro.
CONTRATOS = [
    # (id,     evento, descrição no MAPA_DRE,         início,           parcelas, valor)
    ('482113', '5513', 'INVESTIMENTO- FINAME',        date(2022, 3, 1), 60, 8500.00),
    ('517760', '5512', 'INVESTIMENTO- CDC',           date(2023, 6, 1), 48, 6200.00),
    ('533904', '5211', 'EMPRESTIMOS',                 date(2023, 1, 1), 36, 4000.00),
    ('560218', '5515', 'INVESTIMENTO - CONSORCIO',    date(2024, 1, 1), 72, 4100.00),
    ('594471', '5513', 'INVESTIMENTO- FINAME',        date(2025, 4, 1), 60, 9800.00),
    ('601395', '5212', 'CAPITAL DE GIRO',             date(2025, 9, 1), 24, 7500.00),
    ('618842', '5512', 'INVESTIMENTO- CDC',           date(2026, 2, 1), 36, 5400.00),
]
# Entrada paga na compra (mês de início dos contratos de veículo)
ENTRADA_VEICULO = {'482113': 38000.00, '594471': 45000.00, '618842': 22000.00}


def parcelas_do_mes(d):
    """[(contrato, evento, descr, nº da parcela, total, valor)] ativas no mês `d`."""
    out = []
    for cid, ev, descr, ini, n, valor in CONTRATOS:
        k = meses_desde_inicio(d) - meses_desde_inicio(ini) + 1
        if 1 <= k <= n:
            out.append((cid, ev, descr, k, n, valor, ini))
    return out


def fim_dos_contratos():
    ult = INICIO_HISTORICO
    for _cid, _ev, _d, ini, n, _v in CONTRATOS:
        d = ini
        for _ in range(n - 1):
            d = proximo_mes(d)
        ult = max(ult, d)
    return ult
