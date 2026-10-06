/**
 * Live call exposed over HTTP, for driving it from an external agent.
 *
 *   node src/probe-control.ts <e164> [--port 8787] [--token secret] [--seconds 90]
 *
 * Two roles in one process:
 *
 *   - the call itself: place it, run the media plane, and expose an agent control surface
 *   - optionally an inline brain, via `--brain script|echo`, so the call still does
 *     something useful with nobody connected
 *
 * With `--brain none` nothing replies automatically, which is the mode where an external
 * agent listens on `/events` and decides. That is the interesting one:
 *
 *   # terminal 1 — place the call
 *   node src/probe-control.ts +18003569377 --brain none --token s3cret
 *
 *   # terminal 2 — act as the agent
 *   curl -N -H 'x-gvoice-token: s3cret' http://127.0.0.1:8787/events
 *   curl -H 'x-gvoice-token: s3cret' -d '{"text":"Thanks for calling."}' \
 *        -H 'content-type: application/json' http://127.0.0.1:8787/say
 *
 * `tools/agent-example.mjs` is a complete external agent built on `ControlClient`.
 */

import { MediaPlane } from "./media.ts";
import { SipSession } from "./registrar.ts";
import { VoiceAgent, agentControlHost, type Brain } from "./agent.ts";
import { PiperTts } from "./tts.ts";
import { createStt } from "./stt.ts";
import { ControlServer } from "./control.ts";

const argv = process.argv.slice(2);
const e164Arg = argv[0];
const flag = (name: string, fallback?: string): string | undefined => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? argv[i + 1] : fallback;
};

const seconds = Number(flag("seconds", "90") ?? "90");
const port = Number(flag("port", "8787") ?? "8787");
const token = flag("token");
const brainName = flag("brain", "none") ?? "none";
const greeting = flag("greeting");

if (!e164Arg || e164Arg.startsWith("--")) {
  console.error(
    'usage: node src/probe-control.ts <e164> [--port N] [--token T] [--seconds N] ' +
      '[--brain none|script|echo] [--greeting "..."]',
  );
  process.exit(2);
}
const toE164 = e164Arg.startsWith("+") ? e164Arg : `+${e164Arg.replace(/\D/g, "")}`;

const echoBrain: Brain = (heard) => heard;
const scriptBrain: Brain = (heard, history) => {
  const h = heard.toLowerCase();
  if (/press 1|enter 1|dial 1/.test(h)) return { digits: ["1"] };
  if (/place this order yourself|are you the recipient|person who placed/.test(h))
    return { say: "I am the recipient of the order." };
  if (/order number|confirmation number/.test(h))
    return { say: "The order number is one two three four five." };
  if (/are you (a )?(bot|robot|machine)|are you human|who is this/.test(h))
    return { say: "I am an automated agent. I would like to track an order." };
  if (/track and order|review delivery|something else/.test(h))
    return { say: "Track and order." };
  if (/haven't heard|whenever you're ready/.test(h))
    return { say: "I want to track an order, please." };
  if (history.length > 16) return { say: "Thank you, goodbye." };
  return { say: "Track an order, please." };
};
const brains: Record<string, Brain | undefined> = {
  none: undefined,
  echo: echoBrain,
  script: scriptBrain,
};
const brain: Brain | undefined = Object.hasOwn(brains, brainName)
  ? brains[brainName]
  : ((): never => {
      console.error(`unknown brain "${brainName}"; choose from ${Object.keys(brains).join(", ")}`);
      process.exit(2);
    })();

const media = await MediaPlane.create();
const stt = await createStt();
const tts = new PiperTts();

process.stdout.write(`loading ${stt.name}… `);
let t0 = Date.now();
await stt.start();
console.log(`${((Date.now() - t0) / 1000).toFixed(1)}s`);

const agent = new VoiceAgent(media, {
  tts,
  stt,
  brain: brain ?? (() => ""),
  onLog: (m) => console.log(`  [${agent.elapsedSeconds.toFixed(1)}s] ${m}`),
  onTranscript: (e) => {
    if (e.kind === "partial") console.log(`    partial: "${e.text}"`);
  },
});

let hangingUp = false;
let bye = () => {};

const server = new ControlServer(
  agentControlHost({
    agent,
    press: (d) => agent.press(d),
    hangup: () => {
      hangingUp = true;
      bye();
    },
  }),
  {
    port,
    ...(token ? { token } : {}),
    log: (m) => console.log(`  control: ${m}`),
  },
);
const bound = await server.listen();
server.subscribe();

const session = await SipSession.open({ onMessage: () => {} });
bye = () => {
  try {
    session.bye();
  } catch {
    /* already torn down */
  }
};

console.log(`\ncalling ${toE164} from ${session.ownNumber}`);
const result = await session.placeCall({ toE164, media });
console.log(`SIP: ${result.responses.join(" ")}\n`);

if (!result.answer) {
  console.log("no SDP answer — aborting");
  process.exitCode = 1;
} else {
  for (let i = 0; i < 40 && !media.dtlsConnected; i++) await new Promise((r) => setTimeout(r, 250));
  agent.start();

  console.log(`control API: ${bound.url}`);
  console.log(`  GET  /health`);
  console.log(`  GET  /events            (SSE transcript stream)`);
  console.log(`  POST /say {"text":..}`);
  console.log(`  POST /press {"digit":..}`);
  console.log(`  POST /shutup`);
  console.log(`  POST /hangup`);
  if (token) console.log(`  header: x-gvoice-token: ${token}`);
  console.log(`\nbrain: ${brainName}   listening ${seconds}s\n`);

  if (greeting) await agent.say(greeting);

  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline && !hangingUp) await new Promise((r) => setTimeout(r, 200));
}

console.log(`\n=== ${agent.turnCount} turn(s), ${agent.droppedTurns} dropped ===`);
for (const m of agent.transcript) console.log(`  ${m.role === "you" ? "->" : "<-"} ${m.text}`);

bye();
agent.stop();
media.close();
await server.close();
await stt.stop?.();