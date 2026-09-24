"""Atualiza a vitrine para o dia de hoje, do começo ao fim.

Existe porque a base tem data: as telas abrem no mês corrente, o card "Cargas
hoje" conta o dia e o mapa mostra quem está na estrada. Uma base parada no
passado mostra zero em tudo isso e parece sistema sem uso.

O ciclo, na ordem — e cada passo depende do anterior:

  1. gera as fixtures com "hoje" = hoje         (seed/gerar.py)
  2. recarrega a trilha de GPS                  (bootstrap --so-gps)
  3. roda o robô do manifesto pela janela       → cria as cargas
     (a continuação roda dentro do passo 3 — Desengatadas e Vazias)
  5. apura o PGR                                → excessos do período retido
  6. confere o CIOT                             → as pendências plantadas
  7. tira o retrato da fita                     → aba de Coletas
  8. monta o inventário de CO₂e                 → aba Carbono

Rodar diariamente enquanto a vitrine estiver de pé:

    python -X utf8 seed/atualizar.py              # ciclo completo (~4 min)
    python -X utf8 seed/atualizar.py --rapido     # sem regerar: só os robôs do dia

No Windows, agendar com o Agendador de Tarefas apontando para o `--rapido`
uma vez por dia; o ciclo completo vale quando se quer a janela inteira
redesenhada (por exemplo, virada de mês).
"""

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, RAIZ)

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(RAIZ, '.env'))


def passo(n, titulo):
    print(f'\n{n}. {titulo}')


def _py(script, *args, silencioso=False):
    r = subprocess.run([sys.executable, '-X', 'utf8', script, *args], cwd=RAIZ,
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode:
        print((r.stdout or '')[-800:])
        print((r.stderr or '')[-800:])
        raise SystemExit(f'{script} falhou ({r.returncode})')
    if not silencioso:
        for l in (r.stdout or '').strip().splitlines()[-3:]:
            print('   ' + l)
    return r.stdout or ''


def gerar():
    passo(1, 'fixtures (hoje = %s)' % date.today())
    _py('seed/gerar.py')


def gps():
    passo(2, 'trilha de GPS')
    _py('seed/bootstrap.py', '--so-gps')


def limpar_derivadas():
    """Cargas e pendências nascem das fixtures — fixture nova, derivadas velhas
    não valem mais (apontam para manifestos que deixaram de existir)."""
    import psycopg2
    con = psycopg2.connect(host=os.getenv('DB_HOST', 'localhost'),
                           dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
                           password=os.getenv('DB_PASSWORD'))
    cur = con.cursor()
    cur.execute('TRUNCATE embarques_cargas_destinos, embarques_cargas_rota, '
                'embarques_cargas_log, embarques_cargas_rastreio_kpi, '
                'embarques_cargas RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE pgr_eventos, pgr_cobertura, ciot_pendencias, ciot_rodadas, '
                'ciot_envios, fita_documentos, embarques_programacao RESTART IDENTITY')
    con.commit()
    cur.close()
    con.close()


def robo(desde, ate):
    passo(3, f'robô do manifesto ({desde} → {ate})')
    criadas, d = 0, desde
    # O último passo tem de cair EXATAMENTE em `ate`: com passo de 4 dias o laço
    # parava antes e os dias mais recentes ficavam sem varrer — o card "Cargas
    # hoje" mostrava zero com manifesto do dia existindo.
    dias = []
    while d <= ate:
        dias.append(d)
        d += timedelta(days=4)
    if dias[-1] != ate:
        dias.append(ate)
    # A última varredura fecha em AMANHÃ de propósito: o filtro do robô é
    # `data_emissao <= DATE(fim)`, e no DAX isso é meia-noite — manifesto
    # emitido às 14h de hoje fica de fora e o card "Cargas hoje" mostra zero.
    # Em produção o mesmo acontece, e o robô só pega o dia na rodada seguinte.
    dias.append(ate + timedelta(days=1))
    for d in dias:
        # passo de 4 dias porque a janela do robô é de 5: menor repete trabalho,
        # maior deixa manifesto sem varrer
        saida = _py('embarques_auto.py', '--dia', d.isoformat(), silencioso=True)
        for l in saida.splitlines():
            if l.startswith('CRIADAS'):
                try:
                    criadas += int(l.split('.')[-1].strip() or 0)
                except ValueError:
                    pass
    print(f'   {criadas} cargas criadas')


# A continuação NÃO tem passo próprio: ela roda dentro do robô do manifesto
# (`embarques_auto.ligar_continuacoes`), porque precisa do id da carga B, que só
# existe depois de criar. A chave `EMBARQUES_CONTINUACAO=true` no .env é o que
# a liga — é dela que saem os cards "Desengatadas" e "Vazias".


def atemporal():
    passo(4, 'robô atemporal (pernas vazias)')
    # A perna vazia é DERIVADA: o cavalo termina em A e aparece em B sem carga
    # no meio. Quem a cria é o robô atemporal, depois que as cargas existem —
    # sem este passo o card "Vazias no mês" fica zerado para sempre.
    # `--aplicar`: sem ele o robô só emite o CSV de análise (é dry-run por
    # padrão, porque em produção ele mexe em carga já lançada). `--desde/--ate`
    # porque os defaults dele são de agosto/2026, congelados de um estudo.
    # Três passos, nesta ordem — é a mesma sequência que o servidor roda sozinho:
    #   motor   → prova chegada/saída pelas posições
    #   pernas  → CRIA a viagem vazia de reposicionamento (é o card "Vazias")
    #   janela  → rederiva o início/fim da perna vazia a partir das cargas
    # `--aplicar` porque os três são dry-run por padrão (em produção mexem em
    # carga lançada), e `--desde/--ate` porque os defaults deles são de um
    # estudo de agosto/2026, congelados.
    jan = ('--desde', date(date.today().year, 1, 1).isoformat(),
           '--ate', date.today().isoformat())
    for script, marcador in (('_robo_atemporal.py', 'GRAVADO'),
                             ('_regerar_vazias_agosto.py', 'vazias regeradas'),
                             ('_rederivar_vazias.py', 'GRAVADO')):
        saida = _py(script, '--aplicar', *jan, silencioso=True)
        ult = [l for l in saida.strip().splitlines() if marcador in l]
        print(f'   {script:28s} {ult[-1].strip() if ult else "(sem resultado)"}')


def pgr_apurar():
    passo(5, 'PGR')
    import psycopg2
    import pgr
    import server
    try:
        server.sincronizar_cadastro_pgr()
        server.sincronizar_manifestos_pgr(dias=40)
    except Exception as e:
        print(f'   !! caches: {str(e)[:120]}')
    con = psycopg2.connect(host=os.getenv('DB_HOST', 'localhost'),
                           dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
                           password=os.getenv('DB_PASSWORD'))
    cur = con.cursor()
    fim = date.today()
    d = fim - timedelta(days=33)
    while d <= fim:
        try:
            pgr.apurar_dia(cur, d)
            con.commit()
        except Exception as e:
            con.rollback()
            print(f'   !! {d}: {str(e)[:100]}')
            break
        d += timedelta(days=1)
    cur.execute('SELECT COUNT(*) FROM pgr_eventos')
    print(f'   {cur.fetchone()[0]} episódios')
    cur.close()
    con.close()


def ciot():
    passo(6, 'conferência CIOT')
    saida = _py('ciot_conferencia.py', silencioso=True)
    for l in saida.strip().splitlines()[-1:]:
        print('   ' + l)


def fita():
    passo(7, 'fita documental (Coletas)')
    os.environ['EMBARQUES_FITA'] = 'true'
    import _fita_documentos as f
    import _locais
    import _programacao
    import server
    tok = server.get_token()
    dados = f.coletar(tok, date(date.today().year, 1, 1))
    con = server.get_db()
    f.gravar(con, datetime.now().replace(microsecond=0), dados)
    con.commit()
    _locais.atualizar(con, dados)
    con.commit()
    _programacao.atualizar(con, dados)
    con.commit()
    cur = con.cursor()
    cur.execute('SELECT COUNT(*) FROM embarques_programacao')
    print(f'   {cur.fetchone()[0]} ordens')
    cur.close()
    con.close()


def carbono():
    passo(8, 'inventário de CO₂e')
    primeiro = date.today().replace(day=1)
    saida = _py('verda_job.py', '--desde', primeiro.isoformat(),
                '--ate', date.today().isoformat(), silencioso=True)
    for l in saida.strip().splitlines()[-4:]:
        print('   ' + l.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rapido', action='store_true',
                    help='não regera as fixtures; só reprocessa os robôs do dia')
    a = ap.parse_args()

    inicio = datetime.now()
    if not a.rapido:
        gerar()
        gps()
        limpar_derivadas()
        robo(date(date.today().year, 1, 5), date.today())
    else:
        # janela curta: o robô varre 5 dias para trás, então uma semana cobre
        robo(date.today() - timedelta(days=7), date.today())
    atemporal()
    pgr_apurar()
    ciot()
    fita()
    carbono()
    print(f'\npronto em {(datetime.now() - inicio).seconds}s — '
          f'base até {date.today()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
