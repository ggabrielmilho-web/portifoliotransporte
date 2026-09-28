"""Monta o banco da vitrine do zero.

Existe porque `init_db.py` **não basta**: ele cria 23 tabelas, e outras nove
nascem dentro dos módulos que as usam (`ciot_*`, `fita_documentos`,
`locais_fontes`/`locais`, `embarques_programacao`, `verda_envios`,
`icms_aliquota`). Numa base nova, quem não chamar cada módulo uma vez descobre
isso pela aba abrindo vazia.

Ordem — e cada passo depende do anterior:

  1. cria o banco (se não existir) e roda `init_db.py`
  2. acorda as 9 tabelas de fora do init_db
  3. carrega os centroides do IBGE (o mapa e o geocoding precisam)
  4. carrega a matriz de ICMS (Tarifas)
  5. carrega a trilha de GPS gerada (posições, simulação e cadastro de rastreio)
  6. cria os usuários da demonstração

Idempotente: pode rodar de novo. O passo 5 apaga e recarrega as posições, que
são derivadas do gerador — nunca há dado a preservar ali.

    python -X utf8 seed/bootstrap.py
    python -X utf8 seed/bootstrap.py --so-gps      # só recarrega a trilha
"""

import argparse
import json
import os
import subprocess
import sys

import psycopg2
from dotenv import load_dotenv

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
FIXTURES = os.path.join(AQUI, 'fixtures')
sys.path.insert(0, RAIZ)
load_dotenv(os.path.join(RAIZ, '.env'))

BANCO = os.getenv('DB_NAME', 'vitrine_demo')


def _conn(dbname=None):
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', '5432'),
        dbname=dbname or BANCO, user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'))


def _rodar(script, *args):
    print(f'  $ python {script} {" ".join(args)}')
    r = subprocess.run([sys.executable, '-X', 'utf8', os.path.join(RAIZ, script), *args],
                       cwd=RAIZ, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    if r.returncode:
        print((r.stdout or '')[-1500:])
        print((r.stderr or '')[-1500:])
        raise SystemExit(f'{script} falhou ({r.returncode})')
    return r.stdout


def criar_banco():
    print(f'1. banco {BANCO}')
    c = _conn('postgres')
    c.autocommit = True
    cur = c.cursor()
    cur.execute('SELECT 1 FROM pg_database WHERE datname=%s', (BANCO,))
    if cur.fetchone():
        print('   já existe')
    else:
        cur.execute(f'CREATE DATABASE {BANCO}')
        print('   criado')
    cur.close()
    c.close()

    # `clientes` é pré-requisito do `init_db.py` (a FK de `embarques_cargas`
    # aponta para ela) e ele NÃO a cria — na Rizza ela já existia de antes. Numa
    # base nova isso aparece como erro no meio do init, então nasce aqui.
    con = _conn()
    cur = con.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id           SERIAL PRIMARY KEY,
            nome         VARCHAR(180) NOT NULL,
            importado_em TIMESTAMP DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_clientes_nome
            ON clientes (LOWER(TRIM(nome)));
    ''')
    con.commit()
    cur.close()
    con.close()
    print('   clientes ok')

    _rodar('init_db.py')


def clientes_da_demo():
    """Os tomadores do gerador viram cadastro local (o formulário de carga usa)."""
    with open(os.path.join(FIXTURES, 'Auditoria Receita.json'), encoding='utf-8') as fh:
        nomes = sorted({r['cliente_pagador'] for r in json.load(fh) if r.get('cliente_pagador')})
    con = _conn()
    cur = con.cursor()
    for n in nomes:
        cur.execute('INSERT INTO clientes (nome) VALUES (%s) '
                    'ON CONFLICT DO NOTHING', (n,))
    con.commit()
    cur.close()
    con.close()
    print(f'   {len(nomes)} clientes no cadastro local')


def acordar_modulos():
    """As nove tabelas que o init_db não conhece.

    Cada módulo cria a sua na primeira chamada. Importar e chamar o criador é
    mais honesto que duplicar o DDL aqui: duplicado, ele envelhece em silêncio.
    """
    print('2. tabelas que nascem fora do init_db')
    import ciot_conferencia
    import _locais
    import _programacao
    import verda_estado
    import _fita_documentos

    with _conn() as con:
        cur = con.cursor()
        # Cada módulo guarda o próprio DDL: executar o dele é mais honesto que
        # copiar o SQL para cá, porque cópia envelhece em silêncio.
        for rotulo, mod in (('locais_fontes/locais', _locais),
                            ('embarques_programacao', _programacao),
                            ('fita_documentos', _fita_documentos)):
            cur.execute(mod.DDL)
            print(f'   ok {rotulo}')
        ciot_conferencia.garantir_tabelas(cur)
        print('   ok ciot_*')
        con.commit()
    verda_estado.garantir_tabela()
    print('   ok verda_envios')


def apoio():
    print('3. centroides do IBGE')
    with _conn() as con:
        cur = con.cursor()
        cur.execute('SELECT COUNT(*) FROM municipios_ibge')
        n = cur.fetchone()[0]
    if n > 1000:
        print(f'   já tem {n} municípios')
    else:
        _rodar('import_municipios.py')

    print('4. matriz de ICMS')
    _rodar('seed_icms.py')


def carregar_gps():
    """Trilha → `embarques_posicoes_historico` + `embarques_simulacao`.

    As duas, porque são dois leitores: o PGR lê o histórico (o backfill da 3S é
    no-op em modo simulado) e o worker lê a simulação para mover o mapa.
    """
    print('5. trilha de GPS')
    with open(os.path.join(FIXTURES, 'embarques_posicoes_historico.json'), encoding='utf-8') as fh:
        hist = json.load(fh)
    with open(os.path.join(FIXTURES, 'embarques_simulacao.json'), encoding='utf-8') as fh:
        sim = json.load(fh)
    with open(os.path.join(FIXTURES, 'embarques_veiculos_rastreio.json'), encoding='utf-8') as fh:
        cad = json.load(fh)

    con = _conn()
    cur = con.cursor()
    # O cadastro entra no TRUNCATE porque ele tem UNIQUE no `id_veiculo_3s`: ao
    # regerar a base o conjunto de placas rastreadas muda, e o id que sobrou de
    # uma placa antiga colide com o da nova. Tudo aqui é derivado do gerador —
    # não há dado a preservar.
    cur.execute('TRUNCATE embarques_posicoes_historico, embarques_simulacao, '
                'embarques_posicoes_atuais, embarques_veiculos_rastreio '
                'RESTART IDENTITY CASCADE')
    cur.executemany(
        'INSERT INTO embarques_posicoes_historico '
        '(placa, id_veiculo_3s, data_posicao, latitude, longitude, velocidade, '
        ' ignicao, uf, cidade, endereco, odometer) '
        'VALUES (%(placa)s,%(id_veiculo_3s)s,%(data_posicao)s,%(latitude)s,%(longitude)s,'
        '%(velocidade)s,%(ignicao)s,%(uf)s,%(cidade)s,%(endereco)s,%(odometer)s) '
        'ON CONFLICT (placa, data_posicao) DO NOTHING', hist)
    cur.executemany(
        'INSERT INTO embarques_simulacao '
        '(placa, id_veiculo_3s, data_posicao, latitude, longitude, velocidade, '
        ' ignicao, uf, cidade, bairro, endereco, odometer) '
        'VALUES (%(placa)s,%(id_veiculo_3s)s,%(data_posicao)s,%(latitude)s,%(longitude)s,'
        '%(velocidade)s,%(ignicao)s,%(uf)s,%(cidade)s,%(bairro)s,%(endereco)s,%(odometer)s)',
        [dict(s, odometer=s.get('odometer')) for s in sim])
    for v in cad:
        cur.execute(
            'INSERT INTO embarques_veiculos_rastreio (placa, id_veiculo_3s) '
            'VALUES (%s,%s) ON CONFLICT (placa) DO UPDATE SET id_veiculo_3s=EXCLUDED.id_veiculo_3s',
            (v['placa'], v['id_veiculo_3s']))
    con.commit()
    cur.execute('SELECT COUNT(*) FROM embarques_posicoes_historico')
    print(f'   {cur.fetchone()[0]} posições · {len(sim)} pontos no simulador (a estrada à frente) '
          f'· {len(cad)} no cadastro de rastreio')
    cur.close()
    con.close()


def usuarios():
    """Um admin e um operacional — o RBAC por aba é argumento de venda."""
    print('6. usuários')
    from werkzeug.security import generate_password_hash
    con = _conn()
    cur = con.cursor()
    for nome, email, senha, role, abas in (
        ('Diretoria', 'diretor@vitrine.demo', 'demo123', 'admin', None),
        ('Operação', 'operacao@vitrine.demo', 'demo123', 'viewer',
         ['embarques', 'ciot', 'pgr']),
    ):
        cur.execute(
            'INSERT INTO auditoria_users (nome, email, password_hash, role, ativo, '
            ' paginas_permitidas) VALUES (%s,%s,%s,%s,TRUE,%s) '
            'ON CONFLICT (email) DO UPDATE SET password_hash=EXCLUDED.password_hash, '
            ' role=EXCLUDED.role, paginas_permitidas=EXCLUDED.paginas_permitidas',
            (nome, email, generate_password_hash(senha), role, abas))
        print(f'   {email} / {senha} ({role})')
    con.commit()
    cur.close()
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--so-gps', action='store_true')
    a = ap.parse_args()
    if a.so_gps:
        carregar_gps()
        return 0
    criar_banco()
    acordar_modulos()
    apoio()
    clientes_da_demo()
    carregar_gps()
    usuarios()
    print('\npronto:  python server.py   →  http://localhost:5000')
    return 0


if __name__ == '__main__':
    sys.exit(main())
