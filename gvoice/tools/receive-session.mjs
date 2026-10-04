// Dev helper: receives the Google cookie jar from a signed-in Chrome and writes
// gv-session.json (gitignored). Cookie *values* are never logged.
import { createServer } from 'node:http';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'gv-session.json');
const server = createServer((req, res) => {
  if (req.method !== 'POST') { res.writeHead(405).end(); return; }
  let body = '';
  req.on('data', (c) => { body += c; });
  req.on('end', () => {
    try {
      const { cookies } = JSON.parse(body);
      const kept = cookies.filter((c) => c.name && c.value);
      const sapisidRec = kept.find((c) => c.name === 'SAPISID')
        ?? kept.find((c) => c.name === '__Secure-3PAPISID');
      if (!sapisidRec) throw new Error('no SAPISID cookie in payload');
      writeFileSync(
        OUT,
        JSON.stringify({ cookies: kept, sapisid: sapisidRec.value }, null, 1) + '\n',
        { mode: 0o600 },
      );
      const dupes = kept.length - new Set(kept.map((c) => c.name)).size;
      console.log(`wrote gv-session.json: ${kept.length} cookies (${dupes} duplicate names across domains)`);
      res.writeHead(204).end();
      setTimeout(() => server.close(), 200);
    } catch (e) {
      console.error('bad payload:', e.message);
      res.writeHead(400).end();
    }
  });
});
server.listen(8791, '127.0.0.1', () => console.log('listening on http://127.0.0.1:8791'));
