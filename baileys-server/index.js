import express from 'express';
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore
} from '@whiskeysockets/baileys';
import { createClient } from '@supabase/supabase-js';
import pino from 'pino';
import QRCode from 'qrcode';
import fs from 'fs';
import path from 'path';

const app = express();
app.use(express.json());
const logger = pino({ level: 'silent' });

const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_SERVICE_KEY
);

const accounts = new Map();
const MAX_RETRIES = 5;
const SESSION_BASE = './sessions';

function sessionDir(id) { return path.join(SESSION_BASE, id); }

async function saveSession(id) {
  try {
    const dir = sessionDir(id);
    if (!fs.existsSync(dir)) return;
    const data = {};
    for (const f of fs.readdirSync(dir)) data[f] = fs.readFileSync(path.join(dir, f), 'utf-8');
    await supabase.from('whatsapp_sessions').upsert(
      { session_id: id, session_data: data, is_connected: true, updated_at: new Date().toISOString() },
      { onConflict: 'session_id' }
    );
  } catch (e) { console.error(`[${id}] saveSession:`, e.message); }
}

async function loadSession(id) {
  try {
    const { data } = await supabase.from('whatsapp_sessions').select('session_data').eq('session_id', id).single();
    if (data?.session_data && Object.keys(data.session_data).length > 0) {
      const dir = sessionDir(id);
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      for (const [f, c] of Object.entries(data.session_data)) fs.writeFileSync(path.join(dir, f), c);
      return true;
    }
  } catch (e) {}
  return false;
}

async function clearSession(id) {
  try {
    const dir = sessionDir(id);
    if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
    await supabase.from('whatsapp_sessions')
      .update({ session_data: {}, is_connected: false, updated_at: new Date().toISOString() })
      .eq('session_id', id);
  } catch (e) {}
}

// ── Core connect function (used for both QR and after pairing) ────────────────
async function connectAccount(id, accountType = 'checker') {
  let acct = accounts.get(id);
  if (acct?.status === 'connected' || acct?.status === 'connecting') return;
  if (!acct) {
    acct = { sock: null, status: 'disconnected', qrCode: null, phoneNumber: null, retryCount: 0, wasConnected: false, accountType };
    accounts.set(id, acct);
  }
  acct.status = 'connecting'; acct.qrCode = null;

  try {
    await loadSession(id);
    const dir = sessionDir(id);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

    const { state, saveCreds } = await useMultiFileAuthState(dir);
    const { version } = await fetchLatestBaileysVersion();

    const sock = makeWASocket({
      version, logger,
      auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
      printQRInTerminal: false,
      browser: ['Ubuntu', 'Chrome', '20.0.04'],
      generateHighQualityLinkPreview: false,
      syncFullHistory: false,
      markOnlineOnConnect: false
    });
    acct.sock = sock;

    sock.ev.on('creds.update', async () => { await saveCreds(); await saveSession(id); });

    sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
      if (qr) {
        acct.qrCode = await QRCode.toDataURL(qr);
        acct.status = 'waiting_for_scan';
        console.log(`[${id}] QR ready`);
      }
      if (connection === 'open') {
        acct.status = 'connected';
        acct.qrCode = null;
        acct.retryCount = 0;
        acct.wasConnected = true;
        acct.phoneNumber = sock.user?.id?.split(':')[0] || null;
        console.log(`[${id}] Connected! +${acct.phoneNumber}`);
        await saveSession(id);
      }
      if (connection === 'close') {
        const code = lastDisconnect?.error?.output?.statusCode;
        const banned = code === DisconnectReason.loggedOut;
        acct.sock = null; acct.qrCode = null;
        acct.status = banned ? 'banned' : 'disconnected';
        if (banned) {
          await clearSession(id);
          acct.retryCount = 0;
          console.log(`[${id}] Banned/logged out`);
        } else if (acct.retryCount < MAX_RETRIES) {
          acct.retryCount++;
          const backoff = Math.min(300000, 30000 * acct.retryCount);
          console.log(`[${id}] Retry ${acct.retryCount} in ${backoff/1000}s`);
          setTimeout(() => connectAccount(id, acct.accountType), backoff);
        }
        acct.wasConnected = false;
      }
    });
  } catch (err) {
    console.error(`[${id}] connectAccount error:`, err.message);
    if (acct) acct.status = 'disconnected';
  }
}

async function checkNumber(id, phone) {
  const acct = accounts.get(id);
  if (!acct?.sock || acct.status !== 'connected') return { phone_number: phone, isRegistered: null, error: 'not_connected' };
  try {
    const formatted = phone.replace(/[^\d]/g, '');
    const [result] = await acct.sock.onWhatsApp(`${formatted}@s.whatsapp.net`);
    return { phone_number: phone, isRegistered: result?.exists === true };
  } catch (e) { return { phone_number: phone, isRegistered: false, error: e.message }; }
}

// ── Routes ────────────────────────────────────────────────────────────────────
app.get('/health', (req, res) => res.json({ status: 'ok', accounts: accounts.size }));

app.get('/accounts', (req, res) => {
  const list = [];
  for (const [id, a] of accounts) list.push({ id, status: a.status, phoneNumber: a.phoneNumber, accountType: a.accountType, hasQR: !!a.qrCode });
  res.json({ accounts: list });
});

app.post('/accounts/:id/connect', async (req, res) => {
  const { id } = req.params;
  const { accountType = 'checker' } = req.body;
  if (accounts.get(id)?.status === 'connected') return res.json({ status: 'already_connected' });
  connectAccount(id, accountType);
  res.json({ status: 'connecting' });
});

app.get('/accounts/:id/status', (req, res) => {
  const acct = accounts.get(req.params.id);
  if (!acct) return res.json({ status: 'not_found' });
  res.json({ status: acct.status, phoneNumber: acct.phoneNumber, hasQR: !!acct.qrCode });
});

app.get('/accounts/:id/qr', (req, res) => {
  const acct = accounts.get(req.params.id);
  if (!acct) return res.json({ status: 'not_found' });
  if (acct.status === 'connected') return res.json({ status: 'already_connected' });
  if (acct.qrCode) return res.json({ qr: acct.qrCode, status: 'waiting_for_scan' });
  res.json({ status: acct.status, message: 'QR not ready yet' });
});

app.post('/accounts/:id/disconnect', async (req, res) => {
  const { id } = req.params;
  const acct = accounts.get(id);
  if (acct?.sock) { try { await acct.sock.logout(); } catch (e) {} acct.sock = null; }
  await clearSession(id);
  if (acct) { acct.status = 'disconnected'; acct.qrCode = null; }
  res.json({ status: 'disconnected' });
});

app.delete('/accounts/:id', async (req, res) => {
  const { id } = req.params;
  const acct = accounts.get(id);
  if (acct?.sock) { try { acct.sock.end(); } catch (e) {} }
  await clearSession(id);
  accounts.delete(id);
  res.json({ status: 'deleted' });
});

// ── Pairing code — creates socket, gets code, keeps socket alive for auth ─────
app.post('/accounts/:id/pair', async (req, res) => {
  const { id } = req.params;
  const { phone, accountType = 'checker' } = req.body;
  if (!phone) return res.status(400).json({ error: 'phone required' });

  // If already connected, return early
  const existing = accounts.get(id);
  if (existing?.status === 'connected') return res.json({ status: 'already_connected' });

  // Clear old session and start fresh
  if (existing?.sock) { try { existing.sock.end(); } catch (e) {} }
  await clearSession(id);

  const dir = sessionDir(id);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  try {
    const { state, saveCreds } = await useMultiFileAuthState(dir);
    const { version } = await fetchLatestBaileysVersion();

    const sock = makeWASocket({
      version, logger,
      auth: { creds: state.creds, keys: makeCacheableSignalKeyStore(state.keys, logger) },
      printQRInTerminal: false,
      browser: ['Ubuntu', 'Chrome', '20.0.04']
    });

    const acct = { sock, status: 'connecting', qrCode: null, phoneNumber: null, retryCount: 0, wasConnected: false, accountType };
    accounts.set(id, acct);

    // Full connection handler — same as connectAccount so it properly transitions to 'connected'
    sock.ev.on('creds.update', async () => { await saveCreds(); await saveSession(id); });
    sock.ev.on('connection.update', async ({ connection, lastDisconnect }) => {
      if (connection === 'open') {
        acct.status = 'connected';
        acct.qrCode = null;
        acct.retryCount = 0;
        acct.wasConnected = true;
        acct.phoneNumber = sock.user?.id?.split(':')[0] || null;
        console.log(`[${id}] Paired & Connected! +${acct.phoneNumber}`);
        await saveSession(id);
      }
      if (connection === 'close') {
        const code = lastDisconnect?.error?.output?.statusCode;
        const banned = code === DisconnectReason.loggedOut;
        acct.sock = null; acct.status = banned ? 'banned' : 'disconnected';
        if (!banned && acct.retryCount < MAX_RETRIES) {
          acct.retryCount++;
          const backoff = Math.min(300000, 30000 * acct.retryCount);
          setTimeout(() => connectAccount(id, accountType), backoff);
        }
      }
    });

    // Wait for socket to register before requesting pairing code
    await new Promise(r => setTimeout(r, 3000));

    const formatted = phone.replace(/[^\d]/g, '');
    const code = await sock.requestPairingCode(formatted);
    console.log(`[${id}] Pairing code sent for +${formatted}`);
    res.json({ code, status: 'pairing_code_sent' });

  } catch (e) {
    console.error(`[${id}] Pair error:`, e.message);
    res.status(500).json({ error: e.message });
  }
});

app.post('/accounts/:id/check', async (req, res) => {
  const { phone } = req.body;
  if (!phone) return res.status(400).json({ error: 'phone required' });
  res.json(await checkNumber(req.params.id, phone));
});

app.post('/accounts/:id/check-batch', async (req, res) => {
  const { phones } = req.body;
  if (!phones || !Array.isArray(phones)) return res.status(400).json({ error: 'phones array required' });
  const results = [];
  for (const p of phones) { results.push(await checkNumber(req.params.id, p)); await new Promise(r => setTimeout(r, 50)); }
  res.json({ results });
});

// ── Boot: restore connected sessions from Supabase ────────────────────────────
const PORT = process.env.PORT || 3001;
app.listen(PORT, async () => {
  console.log(`[Baileys] Multi-account server on port ${PORT}`);
  try {
    const { data } = await supabase.from('whatsapp_sessions').select('session_id').eq('is_connected', true);
    if (data?.length > 0) {
      console.log(`[Baileys] Restoring ${data.length} session(s)...`);
      for (const row of data) {
        await connectAccount(row.session_id);
        await new Promise(r => setTimeout(r, 1000));
      }
    }
  } catch (e) { console.error('[Baileys] Restore error:', e.message); }
});