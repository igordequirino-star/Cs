import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

// Formato documentado em "Webhooks > Eventos e Payloads" do portal Developers do Artia:
// POST JSON { organization_id, activity, changed_attributes, triggered_by } com os
// cabeçalhos X-Artia-Event, X-Artia-Hook-Id, X-Artia-Triggered-At e, quando o webhook
// tem secret, X-Artia-Hmac-Sha256 (HMAC SHA-256 do corpo, em hexadecimal).

const MAX_BODY_BYTES = 1024 * 1024;

export function signBody(secret, body) {
  return crypto.createHmac('sha256', secret).update(body).digest('hex');
}

function signatureMatches(secret, body, received) {
  if (typeof received !== 'string') return false;
  const expected = Buffer.from(signBody(secret, body), 'hex');
  const given = Buffer.from(received.trim().toLowerCase(), 'hex');
  return expected.length === given.length && crypto.timingSafeEqual(expected, given);
}

// O nome exato do evento não está na documentação, então a criação é reconhecida pelo
// cabeçalho quando ele indicar "create"/"criar", ou pelo formato do payload: criação e
// exclusão vêm sem changed_attributes, e só a exclusão traz deleted_at preenchido.
export function isActivityCreated(eventName, payload) {
  if (!payload?.activity) return false;
  if (eventName) return /creat|criar|cria[cç]/i.test(eventName);
  return payload.changed_attributes == null && !payload.activity.deleted_at;
}

export function summarizeActivity(payload) {
  const activity = payload?.activity;
  if (!activity) return null;
  return {
    id: activity.id ?? null,
    title: activity.title ?? null,
    folderId: activity.folder_id ?? null,
    status: activity.custom_status_name ?? null,
    estimatedStart: activity.estimated_start ?? null,
    estimatedEnd: activity.estimated_end ?? null,
    createdAt: activity.created_at ?? null,
    triggeredBy: payload.triggered_by?.name ?? payload.triggered_by?.email ?? null,
  };
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error('payload muito grande'), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function send(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(body));
}

export function createServer({
  webhookPath = '/webhooks/artia',
  secret = '',
  dataDir = path.join(path.dirname(fileURLToPath(import.meta.url)), 'data'),
  onActivityCreated = async () => {},
  logger = console,
} = {}) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');

    if (req.method === 'GET' && url.pathname === '/health') {
      return send(res, 200, { ok: true });
    }
    if (url.pathname !== webhookPath) {
      return send(res, 404, { error: 'não encontrado' });
    }
    if (req.method !== 'POST') {
      res.setHeader('allow', 'POST');
      return send(res, 405, { error: 'método não permitido' });
    }

    let body;
    try {
      body = await readBody(req);
    } catch (err) {
      return send(res, err.status ?? 400, { error: err.message });
    }

    if (secret && !signatureMatches(secret, body, req.headers['x-artia-hmac-sha256'])) {
      logger.warn('[artia-webhook] assinatura inválida, requisição recusada');
      return send(res, 401, { error: 'assinatura inválida' });
    }

    let payload;
    try {
      payload = JSON.parse(body.toString('utf8'));
    } catch {
      return send(res, 400, { error: 'JSON inválido' });
    }

    const eventName = req.headers['x-artia-event'] ?? null;
    const created = isActivityCreated(eventName, payload);
    const event = {
      receivedAt: new Date().toISOString(),
      event: eventName,
      hookId: req.headers['x-artia-hook-id'] ?? null,
      triggeredAt: req.headers['x-artia-triggered-at'] ?? null,
      activityCreated: created,
      activity: summarizeActivity(payload),
      payload,
    };

    try {
      await fs.mkdir(dataDir, { recursive: true });
      await fs.appendFile(path.join(dataDir, 'events.jsonl'), JSON.stringify(event) + '\n');
    } catch (err) {
      logger.error('[artia-webhook] falha ao salvar evento:', err);
      return send(res, 500, { error: 'falha ao salvar evento' });
    }

    logger.log(
      `[artia-webhook] evento=${eventName ?? '-'} criação=${created ? 'sim' : 'não'} ` +
        `atividade="${event.activity?.title ?? '-'}" por=${event.activity?.triggeredBy ?? '-'}`,
    );

    if (created) {
      try {
        await onActivityCreated(event);
      } catch (err) {
        logger.error('[artia-webhook] falha ao processar atividade criada:', err);
      }
    }

    return send(res, 200, { ok: true, activityCreated: created });
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT ?? 3000);
  const webhookPath = process.env.WEBHOOK_PATH ?? '/webhooks/artia';
  const server = createServer({
    webhookPath,
    secret: process.env.ARTIA_WEBHOOK_SECRET ?? '',
    dataDir: process.env.DATA_DIR,
  });
  server.listen(port, () => {
    console.log(`[artia-webhook] ouvindo em http://localhost:${port}${webhookPath}`);
    if (!process.env.ARTIA_WEBHOOK_SECRET) {
      console.warn('[artia-webhook] ARTIA_WEBHOOK_SECRET não definido: a assinatura não será verificada');
    }
  });
}
