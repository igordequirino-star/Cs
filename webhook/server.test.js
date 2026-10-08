import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createServer, isActivityCreated, signBody } from './server.js';

const SECRET = 'segredo';
const silent = { log() {}, warn() {}, error() {} };
const created = [];
let server;
let baseUrl;
let dataDir;

const activityPayload = (overrides = {}) => ({
  organization_id: 1,
  activity: {
    id: 42,
    title: 'Revisar contrato',
    folder_id: 7,
    custom_status_name: 'A fazer',
    created_at: '2026-10-08T12:00:00-03:00',
    deleted_at: null,
  },
  changed_attributes: null,
  triggered_by: { id: 3, name: 'Ana', email: 'ana@example.com' },
  ...overrides,
});

function post(payload, { event = 'activity_created', secret = SECRET } = {}) {
  const body = JSON.stringify(payload);
  const headers = { 'content-type': 'application/json', 'x-artia-hook-id': 'hook-1' };
  if (event) headers['x-artia-event'] = event;
  if (secret) headers['x-artia-hmac-sha256'] = signBody(secret, body);
  return fetch(`${baseUrl}/webhooks/artia`, { method: 'POST', headers, body });
}

before(async () => {
  dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'artia-webhook-'));
  server = createServer({
    secret: SECRET,
    dataDir,
    logger: silent,
    onActivityCreated: (event) => created.push(event),
  });
  await new Promise((resolve) => server.listen(0, resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await new Promise((resolve) => server.close(resolve));
  await fs.rm(dataDir, { recursive: true, force: true });
});

test('reconhece criação pelo cabeçalho do evento', () => {
  assert.equal(isActivityCreated('activity.create', activityPayload()), true);
  assert.equal(isActivityCreated('Criar atividades', activityPayload()), true);
  assert.equal(isActivityCreated('activity.update', activityPayload()), false);
  assert.equal(isActivityCreated('activity.create', { time_entry: {} }), false);
});

test('sem cabeçalho, reconhece criação pelo formato do payload', () => {
  assert.equal(isActivityCreated(null, activityPayload()), true);
  assert.equal(isActivityCreated(null, activityPayload({ changed_attributes: { title: ['a', 'b'] } })), false);
  const deleted = activityPayload();
  deleted.activity.deleted_at = '2026-10-08';
  assert.equal(isActivityCreated(null, deleted), false);
});

test('recusa assinatura ausente ou errada', async () => {
  assert.equal((await post(activityPayload(), { secret: '' })).status, 401);
  assert.equal((await post(activityPayload(), { secret: 'outro' })).status, 401);
});

test('aceita criação de atividade assinada, salva e dispara o processamento', async () => {
  const res = await post(activityPayload());
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { ok: true, activityCreated: true });

  const event = created.at(-1);
  assert.equal(event.activity.title, 'Revisar contrato');
  assert.equal(event.activity.triggeredBy, 'Ana');
  assert.equal(event.hookId, 'hook-1');

  const lines = (await fs.readFile(path.join(dataDir, 'events.jsonl'), 'utf8')).trim().split('\n');
  assert.equal(JSON.parse(lines.at(-1)).payload.activity.id, 42);
});

test('aceita outros eventos sem disparar o processamento de criação', async () => {
  const before = created.length;
  const res = await post(activityPayload({ changed_attributes: { title: ['a', 'b'] } }), {
    event: 'activity_updated',
  });
  assert.equal(res.status, 200);
  assert.equal((await res.json()).activityCreated, false);
  assert.equal(created.length, before);
});

test('rejeita JSON inválido, outros métodos e outras rotas', async () => {
  const body = 'não é json';
  const res = await fetch(`${baseUrl}/webhooks/artia`, {
    method: 'POST',
    headers: { 'x-artia-hmac-sha256': signBody(SECRET, body) },
    body,
  });
  assert.equal(res.status, 400);
  assert.equal((await fetch(`${baseUrl}/webhooks/artia`)).status, 405);
  assert.equal((await fetch(`${baseUrl}/outra-rota`)).status, 404);
  assert.equal((await fetch(`${baseUrl}/health`)).status, 200);
});
