// GW Consult — Proxy seguro para Zendesk API da TOTVS
// Credenciais ficam em variáveis de ambiente Vercel (nunca expostas no browser)

import crypto from 'crypto';

const SHEET_ID    = '1-S8RlcMWPk0b_NqNU5__RQlqg7TI63Wnm2HfpmqCM_8';
const SHEET_RANGE = 'Página1!A:E';

// ── Google Sheets logging ──────────────────────────────────────────────────

async function getGoogleToken(sa) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    iss:   sa.client_email,
    scope: 'https://www.googleapis.com/auth/spreadsheets',
    aud:   'https://oauth2.googleapis.com/token',
    exp:   now + 3600,
    iat:   now,
  };

  const header  = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
  const body    = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const unsigned = `${header}.${body}`;

  const sign = crypto.createSign('SHA256');
  sign.update(unsigned);
  const sig = sign.sign(sa.private_key, 'base64url');
  const jwt = `${unsigned}.${sig}`;

  const res = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
      assertion:  jwt,
    }),
  });
  const data = await res.json();
  if (!data.access_token) throw new Error('Token: ' + JSON.stringify(data));
  return data.access_token;
}

async function logToSheet(sa, { query, product, count, atendente }) {
  const token = await getGoogleToken(sa);
  const row   = [
    new Date().toISOString(),           // A: timestamp
    atendente || 'anônimo',             // B: quem buscou
    product   || 'Todos',               // C: produto filtrado
    query,                              // D: termo buscado
    count,                              // E: nº de resultados
  ];
  await fetch(
    `https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}/values/${encodeURIComponent(SHEET_RANGE)}:append?valueInputOption=USER_ENTERED`,
    {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: [row] }),
    }
  );
}

// ── Handler principal ──────────────────────────────────────────────────────

export default async function handler(req, res) {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { query, per_page = 10, page = 1, product = '', atendente = '' } = req.query;

  if (!query || query.trim().length < 2) {
    return res.status(400).json({ error: 'query obrigatório (mínimo 2 caracteres)' });
  }

  const email = process.env.TOTVS_EMAIL;
  const pass  = process.env.TOTVS_PASS;

  if (!email || !pass) {
    return res.status(500).json({ error: 'Credenciais TOTVS não configuradas' });
  }

  const fullQuery = product ? `${product} ${query}` : query;
  const auth      = Buffer.from(`${email}:${pass}`).toString('base64');

  // User-Agent obrigatório para o TDN (bloqueia user-agents que parecem bots)
  const BROWSER_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36';

  // ── Três fontes em paralelo: ────────────────────────────────────────────
  // 1) totvscst.zendesk.com               → CST (autenticado, inclui restritos)
  // 2) centraldeatendimento.totvs.com     → Central de Atendimento (público)
  // 3) tdn.totvs.com (Confluence público) → Documentação técnica TDN
  // ────────────────────────────────────────────────────────────────────────

  const cstUrl = new URL('https://totvscst.zendesk.com/api/v2/search.json');
  cstUrl.searchParams.set('query',    `type:article ${fullQuery.trim()}`);
  cstUrl.searchParams.set('per_page', String(per_page));
  cstUrl.searchParams.set('page',     String(page));

  const caUrl = new URL('https://centraldeatendimento.totvs.com/api/v2/help_center/articles/search.json');
  caUrl.searchParams.set('query',    fullQuery.trim());
  caUrl.searchParams.set('locale',   'pt-br');
  caUrl.searchParams.set('per_page', String(per_page));
  caUrl.searchParams.set('page',     String(page));

  // TDN usa Confluence CQL — escapar aspas duplas no termo de busca
  const tdnQuery = fullQuery.trim().replace(/"/g, '\\"');
  const tdnUrl = new URL('https://tdn.totvs.com/rest/api/content/search');
  tdnUrl.searchParams.set('cql',    `type=page AND text~"${tdnQuery}"`);
  tdnUrl.searchParams.set('limit',  String(per_page));
  tdnUrl.searchParams.set('start',  String((page - 1) * per_page));
  tdnUrl.searchParams.set('expand', 'version,space');

  function mapZendesk(a, source) {
    return {
      id:         a.id,
      title:      a.title || '(sem título)',
      snippet:    a.snippet || (a.body ? a.body.replace(/<[^>]+>/g, ' ').substring(0, 300) : ''),
      url:        a.html_url,
      updated_at: a.updated_at,
      labels:     a.label_names || [],
      source:     source,  // 'cst' ou 'central'
    };
  }

  function mapTdn(p) {
    const space = p.space?.name || p.space?.key || 'TDN';
    return {
      id:         p.id,
      title:      p.title || '(sem título)',
      snippet:    space + ' — ' + (p.title || ''),  // body é grande, buscar no expand
      url:        'https://tdn.totvs.com' + (p._links?.webui || '/pages/viewpage.action?pageId=' + p.id),
      updated_at: p.version?.when || null,
      labels:     space ? [space] : [],
      source:     'tdn',
    };
  }

  try {
    // Buscar nas três bases em paralelo. Se uma falhar, mantém as outras.
    const [cstRes, caRes, tdnRes] = await Promise.allSettled([
      fetch(cstUrl.toString(), {
        headers: { 'Authorization': `Basic ${auth}`, 'Content-Type': 'application/json' },
      }).then(r => r.ok ? r.json() : { results: [], count: 0 }),
      fetch(caUrl.toString(), {
        headers: { 'Content-Type': 'application/json' },
      }).then(r => r.ok ? r.json() : { results: [], count: 0 }),
      fetch(tdnUrl.toString(), {
        headers: { 'User-Agent': BROWSER_UA, 'Accept': 'application/json' },
      }).then(r => r.ok ? r.json() : { results: [], size: 0, totalSize: 0 }),
    ]);

    const cstData = cstRes.status === 'fulfilled' ? cstRes.value : { results: [], count: 0 };
    const caData  = caRes.status  === 'fulfilled' ? caRes.value  : { results: [], count: 0 };
    const tdnData = tdnRes.status === 'fulfilled' ? tdnRes.value : { results: [], totalSize: 0 };

    const cstResults = (cstData.results || []).map(a => mapZendesk(a, 'cst'));
    const caResults  = (caData.results  || []).map(a => mapZendesk(a, 'central'));
    const tdnResults = (tdnData.results || []).map(mapTdn);

    // Intercalar resultados das três fontes (round-robin)
    const results = [];
    const max = Math.max(cstResults.length, caResults.length, tdnResults.length);
    for (let i = 0; i < max; i++) {
      if (cstResults[i]) results.push(cstResults[i]);
      if (caResults[i])  results.push(caResults[i]);
      if (tdnResults[i]) results.push(tdnResults[i]);
    }

    const out = {
      count:      (cstData.count || 0) + (caData.count || 0) + (tdnData.totalSize || tdnData.size || 0),
      page:       page,
      page_count: Math.max(cstData.page_count || 1, caData.page_count || 1, Math.ceil((tdnData.totalSize || 0) / per_page) || 1),
      results,
      sources: {
        cst:     cstData.count   || 0,
        central: caData.count    || 0,
        tdn:     tdnData.totalSize || tdnData.size || 0,
      },
    };

    // Log no Google Sheets — fire-and-forget, não adiciona latência
    if (process.env.GOOGLE_SERVICE_ACCOUNT) {
      try {
        const sa = JSON.parse(process.env.GOOGLE_SERVICE_ACCOUNT);
        logToSheet(sa, { query: query.trim(), product, count: out.count, atendente })
          .catch(e => console.error('[sheet]', e.message));
      } catch (e) {
        console.error('[sheet-parse]', e.message);
      }
    }

    return res.status(200).json(out);

  } catch (err) {
    return res.status(500).json({ error: 'Erro ao consultar TOTVS: ' + err.message });
  }
}
