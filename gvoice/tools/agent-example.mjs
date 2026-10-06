#!/usr/bin/env node
/**
 * An external agent driving a live Google Voice call over the control API.
 *
 * This is the shape the whole thing is for: a process that knows nothing about SIP, RTP,
 * opus or opusscript listens to a transcript stream and sends text to be spoken. Any
 * language, any host, any model — it only needs HTTP.
 *
 *   # terminal 1 — place the call
 *   node src/probe-control.ts +18003569377 --brain none --token s3cret
 *
 *   # terminal 2 — be the agent
 *   node tools/agent-example.mjs http://127.0.0.1:8787 s3cret
 *
 * Set GV_LLM_URL to an OpenAI-compatible endpoint to get real replies instead of the
 * canned script below. Without it the agent just confirms what it heard, which is enough
 * to prove the loop.
 */

import { ControlClient } from "../src/control.ts";

const base = process.argv[2] ?? "http://127.0.0.1:8787";
const token = process.argv[3];

const client = new ControlClient(base, token);

/** A trivial responder, so the example works with no model and no network. */
function replyFor(heard) {
  const h = heard.toLowerCase();
  if (/goodbye|end of call/.test(h)) return { say: "Goodbye.", hangup: true };
  if (/place this order yourself|are you the recipient/.test(h))
    return { say: "I am the recipient." };
  if (/order number|confirmation number/.test(h))
    return { say: "The order number is four two seven one." };
  if (/track and order|review delivery|something else/.test(h))
    return { say: "Track and order." };
  return { say: `I heard: ${heard.slice(0, 80)}` };
}

/** Optional LLM backend. GV_LLM_URL is any OpenAI-compatible /chat/completions endpoint. */
async function llmReply(heard, history) {
  const res = await fetch(process.env.GV_LLM_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(process.env.GV_LLM_KEY
        ? { authorization: `Bearer ${process.env.GV_LLM_KEY}` }
        : {}),
    },
    body: JSON.stringify({
      model: process.env.GV_LLM_MODEL ?? "local-model",
      max_tokens: 64,
      messages: [
        {
          role: "system",
          content:
            "You are a concise assistant on a phone call. Reply in one short sentence. " +
            "No markdown, no lists.",
        },
        ...history.map((m) => ({
          role: m.role === "you" ? "assistant" : "user",
          content: m.text,
        })),
      ],
    }),
  });
  if (!res.ok) throw new Error(`LLM ${res.status}`);
  const body = await res.json();
  return body.choices?.[0]?.message?.content?.trim() ?? "";
}

console.log(`connecting to ${base}`);
const controller = new AbortController();
const history = [];
const finals = new Map(); // utteranceId -> text, to avoid replying twice to one utterance

for await (const event of client.stream(controller.signal)) {
  if (event.type === "ready") {
    console.log("  stream connected");
    continue;
  }

  const { kind, text, utteranceId, at } = event;
  const t = at.toFixed(1);

  if (kind === "speech-start") {
    console.log(`  [${t}s] …`);
  } else if (kind === "partial") {
    // Partials let a model start thinking before the sentence ends.
    process.stdout.write(`\r  [${t}s] ${text.padEnd(80).slice(0, 80)}`);
  } else if (kind === "final") {
    if (finals.has(utteranceId)) continue;
    finals.set(utteranceId, text);
    console.log(`\n  [${t}s] heard: "${text}"`);
    history.push({ role: "them", text });

    let say;
    let hangup = false;
    if (process.env.GV_LLM_URL) {
      try {
        say = await llmReply(text, history);
      } catch (err) {
        console.log(`  [${t}s] llm failed (${err.message}); using scripted reply`);
        ({ say, hangup } = replyFor(text));
      }
    } else {
      ({ say, hangup } = replyFor(text));
    }

    if (say) {
      console.log(`  [${t}s] saying: "${say}"`);
      history.push({ role: "you", text: say });
      await client.say(say);
    }
    if (hangup) {
      console.log(`  [${t}s] hanging up`);
      await client.hangup();
      controller.abort();
      break;
    }
  }
}

console.log("done");