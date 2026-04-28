// GW Consult — Proxy seguro para Zendesk API da TOTVS
// Credenciais ficam em variáveis de ambiente Vercel (nunca expostas no browser)

import crypto from 'crypto';

const SHEET_ID    = '1-S8RlcMWPk0b_NqNU5__RQlqg7TI63Wnm2HfpmqCM_8';
const SHEET_RANGE = 'Buscas!A:E';

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

  const url = new URL('https://totvscst.zendesk.com/api/v2/help_center/articles/search.json');
  url.searchParams.set('query',    fullQuery.trim());
  url.searchParams.set('locale',   'pt-br');
  url.searchParams.set('per_page', String(per_page));
  url.searchParams.set('page',     String(page));

  try {
    const upstream = await fetch(url.toString(), {
      headers: { 'Authorization': `Basic ${auth}`, 'Content-Type': 'application/json' },
    });

    if (!upstream.ok) {
      return res.status(upstream.status).json({ error: `Zendesk retornou ${upstream.status}` });
    }

    const data = await upstream.json();

    const results = (data.results || []).map(a => ({
      id:         a.id,
      title:      a.title,
      snippet:    a.snippet || '',
      url:        a.html_url,
      updated_at: a.updated_at,
      labels:     a.label_names || [],
    }));

    const out = {
      count:      data.count      || 0,
      page:       data.page       || 1,
      page_count: data.page_count || 1,
      results,
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
