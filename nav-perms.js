// ── Navegação lateral centralizada + gating por permissão de aba ──
// Fonte ÚNICA de verdade das abas. As páginas só declaram um placeholder
//   <span data-nav-menu data-nav-active="dre"></span>
// e este script monta a BARRA LATERAL com as abas que o usuário pode ver
// (admin enxerga todas por bypass). Aba nova aparece sozinha em todas as telas.
//
// Por que lateral, e por que aqui: a barra superior empilhava 16 botões em três
// linhas e comia ~120 px antes de qualquer conteúdo. A lateral é injetada como
// primeiro filho do <body> e empurra a página com `body.tem-sidebar`, em vez de
// exigir reestruturação dos 21 HTML — o `body` deles não tinha layout próprio,
// só fundo e fonte. O placeholder some, então nenhuma tela precisou mudar.
//
// Os ícones são glifos geométricos monocromáticos, não emoji: emoji traz a
// própria paleta (a tela acabava com uma dúzia de tons que ninguém escolheu) e
// vários não têm glifo no Windows 10, saindo quadradinho vazio.
(function () {
  // Ordem canônica das abas. `page` = chave de permissão (paginas_permitidas);
  // `id` = identificador da aba ativa; `label` = texto exibido.
  var ABAS = [
    // Início não é aba concedível: é a porta de entrada, visível para quem está logado.
    // Fica na barra de todas as telas para haver caminho de volta ao menu.
    { id: 'inicio',        page: null,            href: '/inicio',            label: 'Início', ic: '⌂', sempre: true, grupo: null },
    { id: 'auditoria',     page: 'auditoria',     href: '/',                  label: 'Auditoria', ic: '◆' },
    { id: 'embarques',     page: 'embarques',     href: '/embarques',         label: 'Embarques', ic: '▤' },
    { id: 'mapa',          page: 'embarques',     href: '/embarques/mapa',    label: 'Mapa', ic: '◎' },
    // Ordens de coleta (ERP 157). Mesma permissão de Embarques: quem lança carga é quem
    // acompanha a ordem que a origina. Vive de `embarques_programacao`, que a fita alimenta.
    { id: 'ordens',        page: 'embarques',     href: '/embarques/ordens',  label: 'Coletas', ic: '⊞' },
    // Junto da família de rastreamento, não perto do DRE: é segurança
    // operacional, não financeiro.
    { id: 'pgr',           page: 'pgr',           href: '/pgr',               label: 'PGR', ic: '▲' },
    // Escala motorista × placa que o RH manda à empresa de controle de jornada.
    // ⏱ é bloco antigo (glifo no Windows 10), pela mesma razão do 🚛 abaixo.
    { id: 'jornada',       page: 'jornada',       href: '/jornada',           label: 'Jornada', ic: '◷' },
    // Conferência CTRB × manifesto × CIOT (pedido do diretor). 📋 é bloco antigo.
    { id: 'ciot',          page: 'ciot',          href: '/ciot',              label: 'CIOT', ic: '▦' },
    { id: 'tarifas',       page: 'tarifas',       href: '/tarifas',           label: 'Tarifas', ic: '◧' },
    { id: 'dre',           page: 'dre',           href: '/dre',               label: 'DRE', ic: '◪' },
    { id: 'projecao',      page: 'projecao',      href: '/projecao',          label: 'Projeção', ic: '◬' },
    { id: 'conhecimentos', page: 'conhecimentos', href: '/dre/conhecimentos', label: 'Conhecimentos', ic: '▥' },
    { id: 'despesas',      page: 'despesas',      href: '/dre/despesas',      label: 'Despesas', ic: '▨' },
    { id: 'faturamento',   page: 'faturamento',   href: '/faturamento',       label: 'Faturamento', ic: '▣' },
    // 🚛 (carreta articulada) e não 🚚 (baú, que fica com Embarques): esta aba analisa
    // cavalo + carreta. Nada de 🛞/🛻 — são do Emoji 13/14 e o Windows 10 não tem o
    // glifo, sai quadradinho vazio. Emoji desta lista: só de blocos antigos.
    { id: 'veiculos',      page: 'veiculos',      href: '/veiculos',          label: 'Veículos', ic: '◨' },
    // 🌱 e não ♻/🌍: é inventário de emissão, não reciclagem nem "planeta".
    // Emoji de bloco antigo, pela mesma razão do 🚛 acima (glifo no Windows 10).
    { id: 'carbono',       page: 'carbono',       href: '/carbono',           label: 'Carbono', ic: '⊕' },
    { id: 'admin',         page: 'admin',         href: '/admin',             label: 'Admin', ic: '⚙' }
    // O relatório de uso (/uso) NÃO entra aqui de propósito: é tela escondida,
    // acessada só por quem sabe a URL. Protegida por admin_required no servidor.
  ];

  function podeVer(aba, me) {
    if (aba.sempre) return true;             // Início: quem está logado enxerga
    if (me.role === 'admin') return true;    // admin vê tudo (bypass)
    if (aba.page === 'admin') return false;  // Admin é exclusivo de role=admin
    return (me.paginas_permitidas || []).indexOf(aba.page) !== -1;
  }

  // Os grupos são os MESMOS do /inicio, lidos de lá quando a tela é aquela.
  // Duplicar a lista faria a lateral e a tela de entrada divergirem no dia em
  // que alguém movesse uma aba de grupo.
  var GRUPOS = [
    { nome: 'Operação',   abas: ['embarques', 'ordens', 'mapa', 'pgr', 'jornada', 'ciot', 'veiculos', 'carbono'] },
    { nome: 'Comercial',  abas: ['tarifas', 'faturamento'] },
    { nome: 'Financeiro', abas: ['auditoria', 'dre', 'projecao', 'despesas', 'conhecimentos'] },
    { nome: 'Sistema',    abas: ['admin'] }
  ];

  function porId(id) {
    for (var i = 0; i < ABAS.length; i++) if (ABAS[i].id === id) return ABAS[i];
    return null;
  }

  function criarItem(aba, ativo) {
    var a = document.createElement('a');
    a.className = 'item' + (ativo ? ' ativo' : '');
    a.href = aba.href;
    a.title = aba.label;
    if (aba.page) a.setAttribute('data-page', aba.page);
    var ic = document.createElement('span');
    ic.className = 'ic';
    ic.textContent = aba.ic || '·';
    var tx = document.createElement('span');
    tx.className = 'rotulo';
    tx.textContent = aba.label;
    a.appendChild(ic);
    a.appendChild(tx);
    return a;
  }

  function montarMenu(me) {
    var ph = document.querySelector('[data-nav-menu]');
    if (!ph || document.querySelector('.side-nav')) return;
    var ativo = ph.getAttribute('data-nav-active') || '';

    var nav = document.createElement('aside');
    nav.className = 'side-nav';

    var marca = document.createElement('a');
    marca.className = 'marca';
    marca.href = '/inicio';
    marca.innerHTML =
      '<span class="logo">N</span>' +
      '<span class="texto"><span class="nome">Nortevia</span><br>' +
      '<span class="sub">Transportes</span></span>';
    nav.appendChild(marca);

    var inicio = porId('inicio');
    if (inicio) nav.appendChild(criarItem(inicio, ativo === 'inicio'));

    GRUPOS.forEach(function (g) {
      var itens = g.abas.map(porId).filter(function (a) { return a && podeVer(a, me); });
      if (!itens.length) return;              // grupo sem aba liberada não vira título órfão
      var t = document.createElement('div');
      t.className = 'grupo';
      t.textContent = g.nome;
      nav.appendChild(t);
      itens.forEach(function (a) { nav.appendChild(criarItem(a, a.id === ativo)); });
    });

    var esp = document.createElement('div');
    esp.className = 'espaco';
    nav.appendChild(esp);

    var rod = document.createElement('div');
    rod.className = 'rodape';
    var papel = document.createElement('span');
    papel.className = 'papel';
    papel.textContent = me.role === 'admin' ? '● Administrador' : '● Consulta';
    var quem = document.createElement('span');
    quem.className = 'quem';
    quem.textContent = me.nome || me.email || '';
    var sair = document.createElement('a');
    sair.className = 'sair';
    sair.href = '/logout';
    sair.textContent = '↪ Sair';
    rod.appendChild(papel);
    rod.appendChild(quem);
    rod.appendChild(sair);
    nav.appendChild(rod);

    document.body.insertBefore(nav, document.body.firstChild);
    document.body.classList.add('tem-sidebar');
    ph.parentNode.removeChild(ph);

    // O "Sair" que cada tela já tinha no topo vira repetição: a lateral tem o
    // seu. Esconder é melhor que apagar — a página segue funcionando sozinha
    // se um dia a lateral não montar.
    document.querySelectorAll('a[href="/logout"]').forEach(function (el) {
      if (!nav.contains(el)) el.style.display = 'none';
    });
  }

  // Compat: esconde quaisquer links [data-page] hardcoded que sobrem numa página
  // (ex.: atalhos contextuais) quando o usuário não tem a permissão.
  function aplicarPermissoesAbas(me) {
    if (!me) return;
    var isAdmin = me.role === 'admin';
    var perms = me.paginas_permitidas || [];
    document.querySelectorAll('[data-page]').forEach(function (el) {
      var key = el.getAttribute('data-page');
      var ok = isAdmin || perms.indexOf(key) !== -1;
      el.style.display = ok ? '' : 'none';
    });
  }

  // Exposto para páginas que já tenham o objeto /api/me em mãos.
  window.aplicarPermissoesAbas = aplicarPermissoesAbas;
  window.montarMenuNav = montarMenu;
  // A tela de entrada (/inicio) monta os cards a partir DESTA mesma lista e desta
  // mesma regra — para aba nova aparecer nos dois lugares sem duplicar permissão.
  window.NAV_ABAS = ABAS;
  window.navPodeVer = podeVer;

  function iniciar() {
    fetch('/api/me', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.ok) { montarMenu(j); aplicarPermissoesAbas(j); }
      })
      .catch(function () {});
  }

  // Não basta escutar DOMContentLoaded: o PGR injeta este script dinamicamente e
  // script inserido por JS é async — se ele chega depois do evento, o listener
  // nunca dispara e a página fica sem menu. Checar o readyState cobre os dois casos.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
