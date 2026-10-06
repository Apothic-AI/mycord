/**
 * A conversational voice agent on a live Google Voice call.
 *
 * Wires the pieces into a turn loop: far-end speech → VAD → Whisper → brain → Piper →
 * opus on the wire, with barge-in so the human can interrupt.
 *
 * The brain is a callback, not a baked-in model. That keeps this usable for IVR
 * navigation, scripted prompts, or a hosted LLM, and keeps the media path free of any
 * network dependency of its own.
 *
 * Design notes worth keeping:
 *
 * - **Turns are serialised.** Overlapping brain calls would interleave replies on the
 *   wire, so a turn already in flight causes a new one to be dropped rather than queued.
 * - **Barge-in stops audio mid-frame.** Waiting for the current 20 ms frame is correct;
 *   waiting for the utterance to finish is not, because the human is already talking.
 * - **STT failures must not kill the call.** A dropped turn is recoverable; a thrown
 *   rejection that tears down the media plane is not.
 */

import { type MediaPlane } from "./media.ts";
import { OpusFrameDecoder } from "./audio.ts";
import { OpusUtteranceEncoder } from "./opusenc.ts";
import { SpeechSender, type TtsBackend } from "./tts.ts";
import type { SttBackend } from "./types.ts";
import { PreRollBuffer, Vad } from "./vad.ts";
import { peak, toFrames } from "./audio.ts";

export interface Turn {
  /** What the far end said. */
  heard: string;
  /** What we replied, if we spoke. */
  said: string;
  /** DTMF digits we sent, if any. */
  digits?: string[];
  /** Wall-clock timings in ms. */
  sttMs: number;
  brainMs: number;
  ttsMs: number;
  speakMs: number;
}

/**
 * What a brain may return: speech, DTMF digits, or both.
 *
 * Most interactive phone trees are faster to drive with keypad presses than with speech —
 * "press 1 for order status" is more reliable than saying "I want to track an order",
 * because it cannot be misheard. So a brain can press keys directly.
 */
export interface Reply {
  /** Text to speak. Omit to only press digits. */
  say?: string;
  /** DTMF digits to send, in order, after any speech. */
  digits?: string[];
}

export type Brain = (
  input: string,
  history: Array<{ role: "you" | "them"; text: string }>,
) => Promise<string | Reply> | string | Reply;

export interface AgentOptions {
  tts: TtsBackend;
  stt: SttBackend;
  /** Produces the reply. */
  brain: Brain;
  /** Seconds of audio kept before the VAD trigger, so first phonemes survive. */
  preRollSeconds?: number;
  vad?: ConstructorParameters<typeof Vad>[0];
  /** Skip replies whose transcript is shorter than this. */
  minReplyChars?: number;
  /**
   * How much louder than our own output the far end must be to count as barge-in.
   *
   * Google Voice returns our own transmitted audio to us, so a naive VAD hears us
   * interrupt ourselves and cancels every utterance mid-word. Measured echo sits close to
   * our own level, while the far end speaking over us is distinctly louder, so gate on
   * that ratio. Set `1` to disable the guard, or `Infinity` to disable barge-in entirely.
   */
  bargeInGuard?: number;
  /** Quiet period after we stop speaking before the VAD is trusted again. */
  echoSettleMs?: number;
  onTurn?: (turn: Turn) => void;
  onLog?: (msg: string) => void;
}

/** Minimal STT contract, satisfied by WhisperStt and WhisperCliStt. */
export type { SttBackend } from "./types.ts";

export class VoiceAgent {
  private readonly vad: Vad;
  private readonly preRoll: PreRollBuffer;
  private readonly decoder: OpusFrameDecoder;
  private readonly encoder: OpusUtteranceEncoder;
  private readonly sender: SpeechSender;
  private readonly opts: AgentOptions;
  private readonly history: Array<{ role: "you" | "them"; text: string }> = [];

  private running = false;
  private busy = false;
  private bargeable = false;
  private quietUntil = 0;
  private turns = 0;
  private dropped = 0;
  private suppressEcho = true;

  private readonly media: MediaPlane;

  constructor(media: MediaPlane, opts: AgentOptions) {
    this.media = media;
    this.opts = opts;
    this.vad = new Vad(opts.vad);
    this.preRoll = new PreRollBuffer(opts.preRollSeconds ?? 0.15);
    this.decoder = new OpusFrameDecoder();
    this.encoder = new OpusUtteranceEncoder({ bitrate: 24000 });
    this.sender = new SpeechSender((pkt) => this.media.sendOpus(pkt));

    this.media.onTrack((payload, meta) => {
      if (meta.payloadType !== 111) return;
      void this.onOpus(payload);
    });
  }

  private async onOpus(payload: Uint8Array): Promise<void> {
    let samples: Float32Array;
    try {
      ({ samples } = await this.decoder.decode(payload));
    } catch {
      return;
    }
    if (samples.length === 0) return;

    const guard = this.opts.bargeInGuard ?? 1.8;

    for (let at = 0; at < samples.length; at += 960) {
      const frame = samples.subarray(at, Math.min(at + 960, samples.length));

      // Echo suppression. While we are transmitting, Google sends our own audio back;
      // feeding that to the VAD makes us cancel our own utterance. Anything meaningfully
      // louder than what we sent is the far end talking over us.
      if (this.suppressEcho && this.sender.playing) {
        const limit = this.sender.lastTxPeak * guard;
        if (limit === 0 || peak(frame) < limit) {
          this.preRoll.push(frame);
          continue;
        }
        this.log("barge-in: far end is louder than our own echo");
        if (this.sender.stop()) this.log("barge-in: stopped speaking");
        this.quietUntil = Date.now() + (this.opts.echoSettleMs ?? 350);
        this.suppressEcho = false;
        this.bargeable = false;
        this.preRoll.push(frame);
        continue;
      }

      const event = this.vad.push(frame);

      if (event?.type === "speech-start") {
        this.preRoll.push(frame);
        if (this.bargeable && this.sender.stop()) this.log("barge-in: stopped speaking");
        continue;
      }

      if (event?.type === "speech-end") {
        const audio = concatFrames([this.preRoll.drain(), event.samples]);
        void this.respond(audio);
        continue;
      }


      this.preRoll.push(frame);
    }
  }

  private async respond(audio: Float32Array): Promise<void> {
    if (!this.running) return;
    // Serialise turns: an overlapping one would interleave replies on the wire.
    if (this.busy) {
      this.dropped += 1;
      return;
    }
    if (Date.now() < this.quietUntil) return;
    this.busy = true;
    const started = Date.now();

    try {
      const t0 = Date.now();
      const transcript = await this.opts.stt.transcribe(audio);
      const sttMs = Date.now() - t0;
      const heard = transcript.text.trim();

      if (!heard || heard.length < (this.opts.minReplyChars ?? 2)) {
        this.log(`heard nothing intelligible (${sttMs}ms) — staying quiet`);
        return;
      }
      this.log(`heard: "${heard}" (${sttMs}ms)`);
      this.history.push({ role: "them", text: heard });

      const t1 = Date.now();
      const raw = await this.opts.brain(heard, this.history);
      const reply: Reply = typeof raw === "string" ? { say: raw.trim() } : raw;
      const brainMs = Date.now() - t1;
      if (!reply.say?.trim() && !reply.digits?.length) return;

      if (reply.say?.trim()) {
        this.history.push({ role: "you", text: reply.say.trim() });
        this.log(`reply: "${reply.say.trim()}" (${brainMs}ms)`);
      }

      const t2 = Date.now();
      let spoken: { framesSent: number; interrupted?: boolean } | undefined;
      if (reply.say?.trim()) {
        const pcm = await this.opts.tts.speak(reply.say.trim());
        const packets = await this.encoder.encode(pcm);
        const levels = [...toFrames(pcm)].map((f) => peak(f));
        this.bargeable = true;
        this.suppressEcho = true;
        spoken = await this.sender.play(packets, levels);
        this.bargeable = false;
        // Let the echo tail decay before trusting the VAD again.
        this.quietUntil = Date.now() + (this.opts.echoSettleMs ?? 350);
      }
      // Digits go after speech: IVRs commonly finish their prompt with "then press 1".
      for (const digit of reply.digits ?? []) {
        if (!this.running) break;
        this.log(`sending DTMF ${digit}`);
        this.media.sendDtmf(digit);
        await new Promise((r) => setTimeout(r, 250));
      }
      const speakMs = Date.now() - t2;
      const ttsMs = spoken ? speakMs : 0;

      this.turns += 1;
      this.opts.onTurn?.({
        heard,
        said: reply.say?.trim() ?? "",
        ...(reply.digits?.length ? { digits: reply.digits } : {}),
        sttMs,
        brainMs,
        ttsMs,
        speakMs,
      });
      this.log(
        `turn ${this.turns}: ${spoken ? `spoke ${spoken.framesSent} frames` : "digits only"}` +
          ` in ${speakMs}ms${spoken?.interrupted ? " (interrupted)" : ""}`,
      );
    } catch (err) {
      // Never let a turn failure tear down the call.
      this.log(`turn failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      this.busy = false;
    }
  }

  /** Begin the turn loop. */
  start(): void {
    this.running = true;
    this.log("listening");
  }

  /** Stop responding and release the speaker. */
  stop(): void {
    this.running = false;
    this.sender.stop();
    this.vad.reset();
    this.log("stopped");
  }

  /** Speak immediately, without waiting to be prompted. Useful for greetings. */
  async say(text: string): Promise<void> {
    const pcm = await this.opts.tts.speak(text);
    const packets = await this.encoder.encode(pcm);
    const levels = [...toFrames(pcm)].map((f) => peak(f));
    this.bargeable = true;
    this.suppressEcho = true;
    await this.sender.play(packets, levels);
    this.bargeable = false;
    this.quietUntil = Date.now() + (this.opts.echoSettleMs ?? 350);
  }

  /** Cut off playback immediately. */
  shutUp(): void {
    this.sender.stop();
  }

  get turnCount(): number {
    return this.turns;
  }

  /** Turns abandoned because one was already in flight. */
  get droppedTurns(): number {
    return this.dropped;
  }

  get transcript(): ReadonlyArray<{ role: "you" | "them"; text: string }> {
    return this.history;
  }

  private log(msg: string): void {
    this.opts.onLog?.(msg);
  }
}

function concatFrames(parts: Float32Array[]): Float32Array {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Float32Array(total);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}