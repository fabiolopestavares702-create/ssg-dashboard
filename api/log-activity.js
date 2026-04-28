// GW Command Center — Log de atividade no Google Sheets
// Cria a aba "Dashboard" automaticamente se não existir

import crypto from 'crypto';

const SHEET_ID    = '1-S8RlcMWPk0b_NqNU5__RQlqg7TI63Wnm2HfpmqCM_8';
const TAB_NAME    = 'Dashboard';
const SHEET_RANGE = `${TAB_NAME}!A:D`;
const HEADERS     = [['Timestamp', 'Atendente', 'Evento', 'Detalhe']];

async function getGoogleToken(sa) {
  const now = Math.floor(Date.now() / 1000);
  const header  = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
  const body    = Buffer.from(JSON.stringify({
    iss:   sa.client_email,
    scope: 'https://www.googleapis.com/auth/spreadsheets',
    aud:   'https://oauth2.googleapis.com/token',
    exp:   now + 3600,
    iat:   now,
  })).toString('base64url');
  const unsigned = `${header}.${body}`;
  const sign = crypto.createSign('SHA256');
  sign.update(unsigned);
  const jwt = `${unsigned}.${sign.sign(sa.private_key, 'base64url')}`;

  const r = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer', assertion: jwt }),
  });
  const d = await r.json();
  if (!d.access_token) throw new Error('Token: ' + JSON.stringify(d));
  return d.access_token;
}

async function sheetsRequest(token, method, path, body) {
  const r = await fetch(`https://sheets.googleapis.com/v4/spreadsheets/${SHEET_ID}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, data: await r.json() };
}

async function ensureTabExists(token) {
  // Verifica se a aba já existe
  const { data } = await sheetsRequest(token, 'GET', '?fields=sheets.properties.title');
  const tabs = (data.sheets || []).map(s => s.properties.title);
  if (tabs.includes(TAB_NAME)) return; // já existe

  // Cria a aba
  await sheetsRequest(token, 'POST', ':batchUpdate', {
    requests: [{ addSheet: { properties: { title: TAB_NAME } } }],
  });

  // Adiciona cabeçalhos
  await sheetsRequest(token, 'PUT',
    `/values/${encodeURIComponent(TAB_NAME + '!A1')}?valueInputOption=USER_ENTERED`,
    { values: HEADERS }
  );
}

async function appendRow(token, row) {
  const { status, data } = await sheetsRequest(token, 'POST',
    `/values/${encodeURIComponent(SHEET_RANGE)}:append?valueInputOption=USER_ENTERED`,
    { values: [row] }
  );
  // Se a aba não existe, o Sheets retorna 400 — criar e tentar de novo
  if (status === 400) {
    await ensureTabExists(token);
    await sheetsRequest(token, 'POST',
      `/values/${encodeURIComponent(SHEET_RANGE)}:append?valueInputOption=USER_ENTERED`,
      { values: [row] }
    );
  }
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.status(200).end();

  let atendente, evento, detalhe;
  if (req.method === 'POST') {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    try {
      ({ atendente, evento, detalhe } = JSON.parse(Buffer.concat(chunks).toString()));
    } catch (_) {
      ({ atendente, evento, detalhe } = req.query);
    }
  } else {
    ({ atendente, evento, detalhe } = req.query);
  }

  if (!process.env.GOOGLE_SERVICE_ACCOUNT) return res.status(200).json({ ok: true });

  try {
    const sa    = JSON.parse(process.env.GOOGLE_SERVICE_ACCOUNT);
    const token = await getGoogleToken(sa);
    const row   = [new Date().toISOString(), atendente||'anônimo', evento||'', detalhe||''];
    await appendRow(token, row);
    return res.status(200).json({ ok: true });
  } catch (e) {
    console.error('[log-activity]', e.message);
    return res.status(200).json({ ok: true }); // silencioso
  }
}
