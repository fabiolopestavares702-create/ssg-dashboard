// SSG Operations Center — Testes unitários
// Rodar: node tests/unit.test.js

let passed = 0, failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log('  ✅', name);
    passed++;
  } catch(e) {
    console.log('  ❌', name, '→', e.message);
    failed++;
  }
}

function assert(val, msg) {
  if (!val) throw new Error(msg || 'assertion failed');
}

function assertEqual(a, b) {
  if (a !== b) throw new Error(`esperado "${b}", recebido "${a}"`);
}

// ─── Funções utilitárias (copiadas do index.html) ────────────────────────────

function pct(a, b) { return b > 0 ? Math.round(a / b * 100) : 0; }
function avg(arr)  { return arr.length ? arr.reduce((a,b) => a+b, 0) / arr.length : 0; }
function fmtDate(s){ var p = s.split('-'); return p[2] + '/' + p[1]; }

const EM2 = {
  'aberto':'Aberto','em atendimento':'Em Atendimento','fechado':'Fechado',
  'resolvido':'Resolvido','aguardando cliente':'Aguardando Cliente',
  'aguardando interno':'Aguardando Interno','retorno cliente':'Retorno Cliente',
  'fechado s/ êxito':'Fechado s/ Êxito','aguardando externo':'Aguardando Externo'
};
const PM2 = {
  'muito alta':'Muito Alta','alta':'Alta','normal':'Normal',
  'baixa':'Baixa','muito baixa':'Muito Baixa'
};

function ePill(e) {
  const m = {'Fechado':'pg','Resolvido':'pg','Fechado s/ Êxito':'px','Aberto':'pb','Em Atendimento':'pb','Aguardando Cliente':'py','Aguardando Interno':'py','Retorno Cliente':'py','Aguardando Externo':'pp'};
  return '<span class="pill ' + (m[e] || 'px') + '">' + e + '</span>';
}
function pPill(p) {
  const m = {'Muito Alta':'pr','Alta':'py','Normal':'pb','Baixa':'pt','Muito Baixa':'px'};
  return '<span class="pill ' + (m[p] || 'px') + '">' + p + '</span>';
}

// ─── Suite de testes ─────────────────────────────────────────────────────────

console.log('\n📋 pct() — percentual');
test('100% de 200', () => assertEqual(pct(200, 200), 100));
test('50% de 200', ()  => assertEqual(pct(100, 200), 50));
test('0 com divisor 0', () => assertEqual(pct(10, 0), 0));
test('arredonda corretamente', () => assertEqual(pct(1, 3), 33));

console.log('\n📋 avg() — média');
test('média simples', () => assertEqual(avg([10, 20, 30]), 20));
test('array vazio retorna 0', () => assertEqual(avg([]), 0));
test('único elemento', () => assertEqual(avg([42]), 42));

console.log('\n📋 fmtDate() — formatar data');
test('formata YYYY-MM-DD para DD/MM', () => assertEqual(fmtDate('2026-03-30'), '30/03'));
test('mantém zero à esquerda', ()       => assertEqual(fmtDate('2026-01-05'), '05/01'));

console.log('\n📋 ePill() — badge de estado');
test('Fechado → classe pg (verde)', ()          => assert(ePill('Fechado').includes('class="pill pg"')));
test('Aberto → classe pb (azul)', ()            => assert(ePill('Aberto').includes('class="pill pb"')));
test('Aguardando Cliente → classe py (amarelo)',() => assert(ePill('Aguardando Cliente').includes('class="pill py"')));
test('estado desconhecido → classe px (cinza)', () => assert(ePill('Outro').includes('class="pill px"')));
test('contém o texto do estado', ()             => assert(ePill('Fechado').includes('Fechado')));

console.log('\n📋 pPill() — badge de prioridade');
test('Muito Alta → classe pr (vermelho)', () => assert(pPill('Muito Alta').includes('class="pill pr"')));
test('Normal → classe pb (azul)', ()        => assert(pPill('Normal').includes('class="pill pb"')));
test('Muito Baixa → classe px (cinza)', ()  => assert(pPill('Muito Baixa').includes('class="pill px"')));
test('contém o texto da prioridade', ()     => assert(pPill('Alta').includes('Alta')));

console.log('\n📋 normalizaFila() — typos vindos do Znuny');
const FILA_FIX = {'INFRAESTUTURA':'INFRAESTRUTURA','INFRA ESTRUTURA':'INFRAESTRUTURA','INFRA-ESTRUTURA':'INFRAESTRUTURA'};
function normalizaFila(raw) { var f = raw.trim().toUpperCase(); return FILA_FIX[f] || f; }
test('INFRAESTUTURA → INFRAESTRUTURA',  () => assertEqual(normalizaFila('INFRAESTUTURA'), 'INFRAESTRUTURA'));
test('INFRA ESTRUTURA → INFRAESTRUTURA',() => assertEqual(normalizaFila('INFRA ESTRUTURA'), 'INFRAESTRUTURA'));
test('INFRA-ESTRUTURA → INFRAESTRUTURA',() => assertEqual(normalizaFila('INFRA-ESTRUTURA'), 'INFRAESTRUTURA'));
test('SSG sem alteração',                () => assertEqual(normalizaFila('SSG'), 'SSG'));
test('DBA sem alteração',                () => assertEqual(normalizaFila('DBA'), 'DBA'));

// ─── Resultado ───────────────────────────────────────────────────────────────
console.log('\n' + '─'.repeat(40));
console.log(`  ${passed} passaram · ${failed} falharam`);
if (failed > 0) process.exit(1);
