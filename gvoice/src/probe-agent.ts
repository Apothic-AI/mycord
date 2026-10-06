/**
 * Live conversation test: place a call and run a full voice-agent turn loop.
 *
 *   node src/probe-agent.ts <e164> [--seconds 45] [--greeting "..."] [--brain echo|script|http]
 *
 * Everything is local: Piper for speech, Whisper for transcription, opus on the wire.
 * Only the optional `http` brain reaches the network.
 */

import { MediaPlane } from "./media.ts";
import { SipSession } from "./registrar.ts";
import { VoiceAgent, type Brain, type Turn } from "./agent.ts";
import { PiperTts } from "./tts.ts";
import { createStt } from "./stt.ts";

const argv = process.argv.slice(2);
const e164Arg = argv[0];
const flag = (name: string, fallback?: string): string | undefined => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 ? argv[i + 1] : fallback;
};
const seconds = Number(flag("seconds", "45"));
const greeting = flag("greeting");
const brainName = flag("brain", "script") ?? "script";

if (!e164Arg || e164Arg.startsWith("--")) {
  console.error(
    'usage: node src/probe-agent.ts <e164> [--seconds N] [--greeting "..."] [--brain echo|script|http]',
  );
  process.exit(2);
}
const toE164 = e164Arg.startsWith("+") ? e164Arg : `+${e164Arg.replace(/\D/g, "")}`;

/** Repeat what was heard — the clearest possible proof the round trip works. */
const echoBrain: Brain = (heard) => heard;

/**
 * A small state machine that drives the real 1-800-FLOWERS menu with DTMF.
 *
 * Keypad presses rather than speech wherever the IVR offers a choice: a digit cannot be
 * misheard, so navigation is deterministic. Speech is only used when the tree asks an
 * open question (an order number, say).
 */
const scriptBrain: Brain = (heard, history) => {
  const h = heard.toLowerCase();
  const said = history.filter((m) => m.role === "you").length;

  // Only press a key when the far end actually offers one. Matching a menu phrase that
  // merely reads like an instruction — "track an order" appearing in a spoken menu — is
  // how you end up keying a voice-only agent into silence: this IVR understands speech
  // and ignores DTMF entirely.
  if (/press 1|enter 1|dial 1/.test(h)) return { digits: ["1"] };
  if (/press 2|enter 2|dial 2/.test(h)) return { digits: ["2"] };
  if (/press 3|enter 3|dial 3/.test(h)) return { digits: ["3"] };
  if (/press \*|press pound|press hash|repeat that/.test(h)) return { digits: ["*"] };
  if (/order number|confirmation number|phone number.*(ending|last|four)/.test(h))
    return { say: "The order number is one two three four five." };
  if (/place this order yourself|are you the recipient|person who placed/.test(h))
    return { say: "I am the recipient of the order." };
  if (/are you (a )?(bot|robot|machine)|are you (a )?real (person|human)|who is this|are you human/.test(h))
    return { say: "I am an automated voice agent built for testing." };
  if (/thank you for calling|what can i help|how can i help|may i help|what would you like/.test(h))
    return { say: "I would like to track an order, please." };
  if (/track and order|review delivery|something else|what brings you here/.test(h))
    return { say: "Track and order." };
  if (/haven't heard|whenever you're ready|are you still there/.test(h))
    return { say: "I want to track an order, please." };
  if (/goodbye|good bye|end of call/.test(h)) return { say: "Goodbye." };
  if (said > 14) return { say: "Thank you, goodbye." };
  return { say: "Track an order, please." };
};

/** Any OpenAI-compatible chat endpoint, for a real language model. */
const httpBrain: Brain = async (heard, history) => {
  const url = process.env.GV_LLM_URL ?? "http://127.0.0.1:1234/v1/chat/completions";
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(process.env.GV_LLM_KEY ? { authorization: `Bearer ${process.env.GV_LLM_KEY}` } : {}),
    },
    body: JSON.stringify({
      model: process.env.GV_LLM_MODEL ?? "local-model",
      max_tokens: 64,
      messages: [
        {
          role: "system",
          content:
            "You are a concise voice assistant on a phone call. Reply in one short sentence. " +
            "No markdown, no lists, no emoji.",
        },
        ...history.map((m) => ({ role: m.role === "you" ? "assistant" : "user", content: m.text })),
      ],
    }),
  });
  if (!res.ok) throw new Error(`LLM returned ${res.status}`);
  const body = (await res.json()) as { choices?: Array<{ message?: { content?: string } }> };
  return body.choices?.[0]?.message?.content?.trim() ?? "";
};

const brains: Record<string, Brain> = { echo: echoBrain, script: scriptBrain, http: httpBrain };
const brain = brains[brainName];
if (!brain) {
  console.error(`unknown brain "${brainName}"; choose from ${Object.keys(brains).join(", ")}`);
  process.exit(2);
}

const media = await MediaPlane.create();
// Auto-picks parakeet-redux when its venv is installed, else faster-whisper.
const stt = await createStt();
const tts = new PiperTts();
const started = Date.now();
const stamp = (): string => `+${((Date.now() - started) / 1000).toFixed(1)}s`;

const turns: Turn[] = [];
const agent = new VoiceAgent(media, {
  tts,
  stt,
  brain,
  onLog: (m) => console.log(`  [${stamp()}] ${m}`),
  onTurn: (t) => turns.push(t),
});

// Preload Whisper so the first turn is not charged for a model load.
process.stdout.write("loading whisper… ");
const t0 = Date.now();
await stt.start();
console.log(`${((Date.now() - t0) / 1000).toFixed(1)}s`);

const session = await SipSession.open({ onMessage: () => {} });
console.log(`\ncalling ${toE164} from ${session.ownNumber}`);

const result = await session.placeCall({ toE164, media });
console.log(`SIP: ${result.responses.join(" ")}\n`);
if (!result.answer) {
  console.log("no SDP answer — aborting");
  process.exitCode = 1;
} else {
  for (let i = 0; i < 40 && !media.dtlsConnected; i++) await new Promise((r) => setTimeout(r, 250));
  console.log(`media up (opus/${result.answer.rtpmap[111] ?? "?"}), agent listening for ${seconds}s\n`);

  agent.start();
  if (greeting) {
    console.log(`  [${stamp()}] greeting: "${greeting}"`);
    void agent.say(greeting);
  }

  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) await new Promise((r) => setTimeout(r, 200));

  agent.stop();
}

console.log(`\n=== ${turns.length} completed turn(s) ===`);
for (const [i, t] of turns.entries()) {
  console.log(`\n${i + 1}. heard : "${t.heard}"`);
  console.log(`   said  : ${t.said ? `"${t.said}"` : "(no speech)"}${t.digits ? ` + DTMF ${t.digits.join(",")}` : ""}`);
  console.log(
    `   stt ${(t.sttMs / 1000).toFixed(2)}s | brain ${(t.brainMs / 1000).toFixed(2)}s |` +
      ` tts ${(t.ttsMs / 1000).toFixed(2)}s | total ${(t.speakMs / 1000).toFixed(2)}s`,
  );
}
const stats = media.stats();
console.log(
  `\nmedia: in ${stats.inbound.packets} pkts | out ${stats.outboundPackets} |` +
    ` decrypt ok=${stats.raw.decryptOk} fail=${stats.raw.decryptFail}`,
);
console.log(
  `agent: ${turns.length} turns | mean latency ` +
    `${turns.length ? ((turns.reduce((n, t) => n + t.sttMs + t.brainMs + t.ttsMs, 0) / turns.length) / 1000).toFixed(2) : "n/a"}s`,
);

media.close();
session.bye();
await stt.stop();
process.exitCode = turns.length > 0 ? 0 : 1;