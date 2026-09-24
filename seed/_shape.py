"""Coletor de SHAPE das tabelas do Power BI — passo 1 da base demo.

O que ele faz: para cada tabela do escopo da vitrine, descobre QUAIS COLUNAS
existem, de que TIPO são, com que FORMATO e em que VOLUME. Grava isso em
`_seed_demo/shape/<tabela>.json`.

O que ele NÃO faz, por desenho: **não guarda valor de cliente.** A demo é
sintética; o único motivo de olhar a base real é copiar o contrato (o shape),
porque metade das telas faz `EVALUATE 'public X'` e renderiza o que vier —
gerar dado sem o shape certo entrega tela com coluna vazia.

Três regras que o código aplica sozinho:

  1. **Valor de linha nunca é persistido.** A amostra é lida, resumida em
     tipo/tamanho/máscara e descartada na mesma função. A máscara troca dígito
     por 9, maiúscula por A e minúscula por a — ela diz "placa tem 7 caracteres
     AAA9A99", não qual placa é.
  2. **Coluna sensível não ganha nem máscara.** Nome, CNPJ, CPF, endereço,
     telefone, razão social, número de documento: só tipo, comprimento e % de
     nulo. A máscara de um nome ("Aaaaa Aaaaa") é inofensiva, mas a regra fica
     mais fácil de auditar sendo cega.
  3. **Distinto só de rótulo de sistema.** Tipo de operação, situação, UF,
     produto, evento — o vocabulário que o gerador precisa reproduzir para as
     telas casarem. Vem de uma lista explícita (ALLOW), com teto de cardinalidade;
     acima do teto guarda só a contagem. Nada que identifique pessoa ou empresa.

Uso:
    python -X utf8 _seed_demo/_shape.py --plano          # o que faria, sem rede
    python -X utf8 _seed_demo/_shape.py                  # tudo
    python -X utf8 _seed_demo/_shape.py --tabela manifestos
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import CONFIG, clean_rows, execute_dax, get_token  # noqa: E402

DESTINO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shape')

# ── Escopo: as 15 tabelas das 12 abas da vitrine ──────────────────────────
# `dataset`: 'main' usa POWERBI_DATASET_ID; 'dre' usa POWERBI_DRE_DATASET_ID.
# `data`: coluna de data usada para medir a janela da base (agregado, não valor).
# `ambos`: a tabela existe nos dois datasets (o código lê dos dois) — sonda os dois.
TABELAS = [
    {'nome': 'Auditoria Receita',           'publica': False, 'dataset': 'main', 'data': 'data_ref_ctrc'},
    {'nome': 'conhecimentos_emitidos',      'publica': True,  'dataset': 'dre',  'data': 'data_autorizacao', 'ambos': True},
    {'nome': 'consulta_despesas_477',       'publica': True,  'dataset': 'dre',  'data': None},
    {'nome': 'manifestos',                  'publica': True,  'dataset': 'main', 'data': 'data_emissao'},
    {'nome': 'manifestos_ctrc',             'publica': True,  'dataset': 'main', 'data': None},
    {'nome': 'ctrbs_oss',                   'publica': True,  'dataset': 'main', 'data': 'emissao'},
    {'nome': 'coletas_0157',                'publica': True,  'dataset': 'main', 'data': None},
    {'nome': 'rotas_km',                    'publica': True,  'dataset': 'main', 'data': None},
    {'nome': 'veiculos_045',                'publica': True,  'dataset': 'main', 'data': None},
    {'nome': 'motoristas_047',              'publica': True,  'dataset': 'main', 'data': None},
    {'nome': 'tarifas_frete',               'publica': True,  'dataset': 'main', 'data': None},
    {'nome': 'abastecimentos_valecard',     'publica': True,  'dataset': 'main', 'data': 'dch_data'},
    {'nome': 'semparar_lancamentos',        'publica': True,  'dataset': 'main', 'data': None},
    {'nome': 'custo_pessoal',               'publica': True,  'dataset': 'main', 'data': None},
]

# ── Rótulos de sistema: o vocabulário que o gerador precisa copiar ────────
# Só entra aqui coluna que é CLASSIFICAÇÃO. Nome de gente, de empresa e número
# de documento não entram nem por engano — quem decide é esta lista, não o tipo.
ALLOW = {
    'Auditoria Receita':       ['Tipo Operacao', 'Gera Custo', 'status_auditoria_frete'],
    'conhecimentos_emitidos':  ['tipo_documento', 'tipo_frete', 'cat',
                                'uf_origem_prestacao', 'uf_destinatario'],
    'consulta_despesas_477':   ['evento', 'descr_evento', 'sit_des'],
    'manifestos':              ['unidade_origem', 'unidade_destino', 'situacao', 'tipo_frete'],
    'ctrbs_oss':               ['situacao', 'tipo_frete', 'tabela_antt'],
    'coletas_0157':            ['situacao', 'unidade', 'tipo'],
    'veiculos_045':            ['tipo', 'modelo', 'disponivel', 'relacionamento'],
    'motoristas_047':          [],
    'tarifas_frete':           ['tipo_veiculo', 'uf_origem', 'uf_destino',
                                'icms_incluso', 'pedagio_incluso'],
    'abastecimentos_valecard': ['produto', 'uf'],
    'semparar_lancamentos':    ['tipo_uso', 'sentido_praca', 'debito_credito'],
    'custo_pessoal':           ['funcao', 'competencia'],
    'rotas_km':                [],
    'manifestos_ctrc':         [],
}

TETO_DISTINTOS = 80       # acima disso guarda só a contagem
# Exceções de teto: rótulo de sistema que o gerador precisa INTEIRO. O plano de
# eventos do 477 alimenta o `MAPA_DRE` (~80 de-paras) — truncar em 80 entregaria
# uma DRE com grupo faltando, que é erro silencioso na tela.
TETO_POR_COLUNA = {'evento': 400, 'descr_evento': 400}
AMOSTRA = 25              # linhas lidas para inferir formato (descartadas)

# Coluna sensível: só tipo, comprimento e % de nulo. Sem máscara, sem distinto.
SENSIVEL = re.compile(
    r'nome|razao|fantasia|cliente|pagador|fornecedor|motorista|proprietario|'
    r'remetente|destinatario|expedidor|recebedor|embarcador|solicitante|usuario|'
    r'cnpj|cpf|_doc|documento|inscricao|rg\b|cnh|'
    r'chassi|renavam|apolice|agencia|conta|pix|cartao|'
    r'endereco|logradouro|bairro|cep|telefone|fone|email|contato|'
    r'observacao|historico|descricao|obs\b',
    re.I,
)
# `placa` fica FORA da lista de propósito: a máscara dela é justamente o que o
# gerador precisa (as duas grafias, antiga e Mercosul, convivem na mesma coluna),
# e placa não identifica pessoa. Quem identifica é o cadastro, que não vem junto.

# Rótulo de sistema cujo NOME cai na regra acima por acidente: `tipo_documento`
# casa com `_doc`, mas é a classe do CTe (é ela que separa viagem de
# `SUBC REC FORM LISO`, §27.17) — sem ela o gerador não reproduz a mistura.
# A exceção é nominal e curta de propósito: regra que cega demais vira regra que
# alguém desliga inteira.
ROTULOS_SEGUROS = {'tipo_documento'}


def _mascara(v):
    """'HNL0A70' -> 'AAA9A99'. Diz o formato, nunca o conteúdo."""
    s = str(v)
    if len(s) > 60:
        return f'<texto {len(s)} caracteres>'
    m = re.sub(r'[0-9]', '9', s)
    m = re.sub(r'[A-ZÀ-Ý]', 'A', m)
    m = re.sub(r'[a-zà-ÿ]', 'a', m)
    return m


def _resumir(linhas, colunas):
    """Lê a amostra e devolve só o resumo. As linhas morrem nesta função."""
    out = {}
    for col in colunas:
        vals = [r.get(col) for r in linhas]
        nao_nulos = [v for v in vals if v is not None and v != '']
        info = {
            'tipo': type(nao_nulos[0]).__name__ if nao_nulos else 'desconhecido',
            'nulo_pct': round(100 * (len(vals) - len(nao_nulos)) / len(vals)) if vals else None,
            'sensivel': bool(SENSIVEL.search(col)),
        }
        if nao_nulos:
            if isinstance(nao_nulos[0], str):
                tam = [len(str(v)) for v in nao_nulos]
                info['len_min'], info['len_max'] = min(tam), max(tam)
                if not info['sensivel']:
                    padroes = sorted({_mascara(v) for v in nao_nulos})
                    info['formato'] = padroes[:4]
            elif isinstance(nao_nulos[0], (int, float)) and not isinstance(nao_nulos[0], bool):
                # Ordem de grandeza, não os valores: o gerador precisa saber se a
                # coluna é centavo, milhar ou milhão para o número sair plausível.
                mag = [len(str(abs(int(v)))) for v in nao_nulos if abs(v) >= 1]
                info['digitos_int'] = [min(mag), max(mag)] if mag else [0, 0]
                info['tem_negativo'] = any(v < 0 for v in nao_nulos)
                info['tem_decimal'] = any(float(v) != int(v) for v in nao_nulos)
        out[col] = info
    return out


def _ref(t):
    return f"'{'public ' if t['publica'] else ''}{t['nome']}'"


def _ds(t, forcar=None):
    chave = forcar or t['dataset']
    return CONFIG['dre_dataset_id'] if chave == 'dre' else CONFIG['dataset_id']


def _consultar(token, dax, dataset):
    r = execute_dax(token, dax, dataset_id=dataset)
    tab = r.get('results', [{}])[0].get('tables', [{}])[0]
    return clean_rows(tab.get('rows', []))


def coletar(token, t, dataset_chave=None):
    ref = _ref(t)
    ds = _ds(t, dataset_chave)
    nome = t['nome']
    res = {
        'tabela': nome,
        'dataset': dataset_chave or t['dataset'],
        'referencia_dax': ref,
        'coletado_em': datetime.now().isoformat(timespec='seconds'),
        'nota': 'Somente shape. Nenhum valor de linha foi persistido.',
    }

    # 1. Volume — quantas linhas o gerador tem de imitar em ordem de grandeza.
    try:
        n = _consultar(token, f'EVALUATE ROW("n", COUNTROWS({ref}))', ds)
        res['linhas'] = n[0].get('n') if n else None
    except Exception as e:
        res['linhas'] = None
        res['erro_contagem'] = str(e)[:200]

    # 2. Janela — de quando até quando a base vai (agregado, não valor de linha).
    if t.get('data'):
        col = t['data']
        try:
            d = _consultar(
                token,
                f'EVALUATE ROW("ini", MIN({ref}[{col}]), "fim", MAX({ref}[{col}]))',
                ds,
            )
            if d:
                res['janela'] = {'coluna': col, 'de': str(d[0].get('ini')), 'ate': str(d[0].get('fim'))}
        except Exception as e:
            res['janela'] = {'coluna': col, 'erro': str(e)[:200]}

    # 3. Shape — amostra lida, resumida e descartada aqui dentro.
    linhas = _consultar(token, f'EVALUATE TOPN({AMOSTRA}, {ref})', ds)
    if not linhas:
        res['colunas'] = {}
        res['aviso'] = 'tabela vazia ou sem permissão'
        return res
    colunas = list(linhas[0].keys())
    res['n_colunas'] = len(colunas)
    res['colunas'] = _resumir(linhas, colunas)
    del linhas  # explícito: a amostra não sobrevive a esta linha

    # 4. Vocabulário — só os rótulos de sistema da lista ALLOW.
    vocab = {}
    for col in ALLOW.get(nome, []):
        if col not in colunas:
            continue                      # a coluna pode não existir; não é erro
        if SENSIVEL.search(col) and col not in ROTULOS_SEGUROS:
            continue                      # cinto e suspensório
        teto = TETO_POR_COLUNA.get(col, TETO_DISTINTOS)
        try:
            d = _consultar(
                token,
                f'EVALUATE TOPN({teto + 1}, '
                f'DISTINCT(SELECTCOLUMNS({ref}, "v", {ref}[{col}])))',
                ds,
            )
            vals = [r.get('v') for r in d]
            if len(vals) > teto:
                vocab[col] = {'distintos': f'>{teto}', 'valores': None}
            else:
                vocab[col] = {'distintos': len(vals),
                              'valores': sorted(str(v) for v in vals if v is not None)}
        except Exception as e:
            vocab[col] = {'erro': str(e)[:200]}
    res['vocabulario'] = vocab
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tabela', help='coleta só esta (nome sem "public ")')
    ap.add_argument('--plano', action='store_true', help='mostra o que faria, sem rede')
    args = ap.parse_args()

    alvos = [t for t in TABELAS if not args.tabela or t['nome'] == args.tabela]
    if not alvos:
        print(f'Tabela desconhecida: {args.tabela}')
        print('Conhecidas: ' + ', '.join(t['nome'] for t in TABELAS))
        return 1

    if args.plano:
        print(f'{len(alvos)} tabela(s); por tabela: 1 contagem + '
              f'1 janela (se houver data) + 1 amostra de {AMOSTRA} + N distintos\n')
        for t in alvos:
            v = [c for c in ALLOW.get(t['nome'], [])]
            print(f"  {_ref(t):42s} dataset={t['dataset']:4s} "
                  f"data={t.get('data') or '-':18s} vocabulario={len(v)}")
        print('\nNenhum valor de linha é gravado. Rode sem --plano para coletar.')
        return 0

    os.makedirs(DESTINO, exist_ok=True)
    token = get_token()
    falhas = []

    for t in alvos:
        chaves = ['main', 'dre'] if t.get('ambos') else [t['dataset']]
        for chave in chaves:
            rotulo = f"{t['nome']}" + (f'.{chave}' if t.get('ambos') else '')
            print(f'→ {rotulo} ... ', end='', flush=True)
            try:
                res = coletar(token, t, chave)
            except Exception as e:
                print(f'FALHOU: {str(e)[:120]}')
                falhas.append((rotulo, str(e)[:200]))
                continue
            caminho = os.path.join(DESTINO, f'{rotulo}.json')
            with open(caminho, 'w', encoding='utf-8') as fh:
                json.dump(res, fh, ensure_ascii=False, indent=2)
            print(f"{res.get('n_colunas', 0)} colunas · "
                  f"{res.get('linhas') or '?'} linhas · "
                  f"{len(res.get('vocabulario') or {})} vocabulário(s)")

    print(f'\nGravado em {DESTINO}')
    if falhas:
        print('\nFalhas:')
        for nome, err in falhas:
            print(f'  {nome}: {err}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
