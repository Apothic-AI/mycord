/**
 * Probe: prove an external (non-browser) client can register with Google Voice's
 * SIP-over-WebSocket registrar.
 *
 *   pnpm probe:sip
 *   GV_VERBOSE=1 pnpm probe:sip     # dump raw SIP messages
 *
 * This mirrors the REGISTER the Voice web client actually sends:
 *
 *   - SIP identity is the *opaque credential* from GetSipRegisterInfo, NOT the phone
 *     number. Using +1…4153356728 as the From user gets 400 Bad Request.
 *   - The first REGISTER carries an Authorization header with empty nonce/response
 *     (a cheap "is auth needed" probe); the server answers 401 with a real challenge.
 *   - Allow / Supported / User-Agent / X-Google-Client-Info are all present; the
 *     registrar appears to validate them.
 *   - Via uses ";rport;keep" and CSeq increments between the probe and the retry.
 */

import { randomBytes, randomUUID } from "node:crypto";
import { METHODS, VoiceClient } from "./gv-api.ts";
import {
  REGISTER_STATIC_HEADERS,
  SIP_DOMAIN,
  buildDigest,
  buildMessage,
  makeCallId,
  openSipTransport,
  parseChallenge,
  parseSipRegisterInfo,
  registerContactParams,
  type SipChallenge,
} from "./sip.ts";

type Outcome = "registered" | "rejected" | "failed";

const VERBOSE = process.env.GV_VERBOSE === "1";

const dump = (dir: "->" | "<-", msg: string): void => {
  if (VERBOSE) console.log(`\n${dir} ${msg.replace(/\r\n/g, "\\n")}\n`);
};

const summary = (msg: string): string => {
  const start = msg.split("\r\n")[0] ?? "";
  const via = /(^|\r\n)Via:[^\r\n]*/i.exec(msg)?.[0]?.trim() ?? "";
  const auth = /(^|\r\n)Authorization:[^\r\n]*/i.exec(msg)?.[0]?.trim() ?? "";
  const challenge = /(^|\r\n)(WWW|Proxy)-Authenticate:[^\r\n]*/i.exec(msg)?.[0]?.trim() ?? "";
  const parts = [start, via, challenge || auth].filter(Boolean);
  return parts.join("\n      ");
};

async function main(): Promise<void> {
  const client = new VoiceClient();

  console.log("1. GetSipRegisterInfo …");
  const raw = await client.call(METHODS.sipRegisterInfoGet, [3, "F6IjFoL0Avfz"]);
  const creds = parseSipRegisterInfo(raw);
  console.log(`   sip user : ${creds.username.slice(0, 16)}… (${creds.username.length} chars)`);
  console.log(`   password : ${creds.password.slice(0, 6)}… (${creds.password.length} chars)`);
  if (creds.expiry) console.log(`   expires  : ${new Date(creds.expiry * 1000).toISOString()}`);

  // The SIP identity is the credential, not the phone number.
  const identity = creds.username;
  const uri = `sip:${SIP_DOMAIN}`;
  const from = `<sip:${identity}@${SIP_DOMAIN}>`;
  const to = `<sip:${identity}@${SIP_DOMAIN}>`;
  const viaHost = randomBytes(5).toString("hex").toUpperCase().slice(0, 10);
  const contactParams = registerContactParams(randomUUID());
  const contact =
    `<sip:${identity}@[::]:5061;transport=wss>;` +
    Object.entries(contactParams).map(([k, v]) => `${k}=${v}`).join(";");
  const callId = `${makeCallId()}_${Date.now()}`;

  console.log(`\n2. opening wss SIP transport (${SIP_DOMAIN}) …`);

  const outcome = await new Promise<Outcome>((resolve) => {
    let settled = false;
    const finish = (v: Outcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(guard);
      try { ws.close(); } catch { /* ignore */ }
      resolve(v);
    };
    const guard = setTimeout(() => {
      console.error("   ! timed out waiting for a final response");
      finish("failed");
    }, 30_000);

    const send = (cseq: number, auth: string | undefined) => {
      const msg = buildMessage({
        method: "REGISTER",
        uri,
        from,
        to,
        callId,
        branch: `z9hG4bK-524287-1---${randomBytes(8).toString("hex")}`,
        cseq,
        contact,
        viaHost,
        auth,
        extra: { ...REGISTER_STATIC_HEADERS, Expires: "3600" },
      });
      console.log(`   -> REGISTER (CSeq ${cseq}${auth ? ", authenticated" : ", probe"})`);
      console.log(`      ${summary(msg)}`);
      dump("->", msg);
      ws.send(msg);
    };

    const ws = openSipTransport({
      onOpen: () => {
        console.log("   transport open (subprotocol=sip)");
        // Probe with an empty nonce/response, exactly like the real client.
        send(1, `Digest username="${identity}",realm="${SIP_DOMAIN}",uri="${uri}",nonce="",response=""`);
      },
      onMessage: (msg) => {
        console.log(`   <- ${summary(msg)}`);
        dump("<-", msg);

        if (msg.startsWith("SIP/2.0 401")) {
          const raw2 =
            /(^|\r\n)WWW-Authenticate:([^\r\n]*)(\r\n|$)/i.exec(msg)?.[2] ?? "";
          const challenge: SipChallenge = parseChallenge(raw2);
          console.log(`\n3. digest challenge`);
          console.log(`   realm     : ${challenge.realm}`);
          console.log(`   nonce     : ${challenge.nonce.slice(0, 20)}…`);
          console.log(`   algorithm : ${challenge.algorithm ?? "(none)"}`);
          console.log(`   qop       : ${challenge.qop ?? "(none)"}  (RFC 2069 style — no qop, no cnonce)`);
          console.log(`   opaque    : ${challenge.opaque ?? "(none)"}`);
          send(2, buildDigest({ challenge, username: identity, password: creds.password, method: "REGISTER", uri }).header);
          return;
        }

        if (msg.startsWith("SIP/2.0 200")) {
          console.log("\n   ✅ 200 OK — registered from a plain Node client, no browser involved");
          finish("registered");
          return;
        }

        const code = /^SIP\/2\.0 (\d{3})/.exec(msg)?.[1];
        if (code) {
          console.error(`\n   ❌ SIP ${code} ${msg.split("\r\n")[0]?.split(" ").slice(2).join(" ")}`);
          finish("rejected");
        }
      },
      onError: (err) => {
        console.error(`   transport error: ${err.message}`);
        finish("failed");
      },
      onClose: (code) => {
        if (!settled) {
          console.error(`   transport closed early (${code})`);
          finish("failed");
        }
      },
    });
  });

  console.log(`\nresult: ${outcome}`);
  if (outcome !== "registered") process.exitCode = 1;
}

main().catch((err: unknown) => {
  console.error(`\n${err instanceof Error ? err.message : String(err)}`);
  process.exitCode = 1;
});