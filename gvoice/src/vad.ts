/**
 * Energy-based voice activity detection with hysteresis and hangover.
 *
 * A phone agent needs two things a naive threshold gets wrong:
 *
 * - **Hysteresis.** One threshold invites chatter: a single noisy frame starts a turn and
 *   every breath fragments it. Use a higher bar to enter speech than to stay in it.
 * - **Hangover.** Speech does not stop the instant the speaker does. Cutting the buffer
 *   at the first quiet frame truncates the last word, so hold through short gaps.
 *
 * The noise floor is tracked adaptively because phone audio crosses from silence to a loud
 * IVR with no warning, and a fixed threshold either clips the start or never triggers.
 *
 * Frames are 20 ms, matching the wire format, so detection costs nothing extra.
 */

import { FRAME_SAMPLES, SAMPLE_RATE, rms } from "./audio.ts";

export interface VadOptions {
  /** Absolute RMS floor; frames below this are never speech. */
  minRms?: number;
  /** Multiple of the adaptive noise floor required to start speech. */
  enterFactor?: number;
  /** Multiple of the adaptive noise floor to stay in speech. */
  exitFactor?: number;
  /** Silence needed to end a turn. 600 ms covers normal inter-word pauses. */
  hangoverMs?: number;
  /** Ignore turns shorter than this, which are almost always coughs or clicks. */
  minSpeechMs?: number;
  /** Cap on a single utterance, so a stuck VAD cannot buffer forever. */
  maxUtteranceMs?: number;
  /** Decay applied to the noise-floor estimate per frame. */
  floorDecay?: number;
}

export type VadEvent =
  | { type: "speech-start" }
  | { type: "speech-end"; samples: Float32Array; durationMs: number };

const DEFAULTS: Required<VadOptions> = {
  minRms: 0.012,
  enterFactor: 3.0,
  exitFactor: 1.6,
  hangoverMs: 700,
  minSpeechMs: 250,
  maxUtteranceMs: 30000,
  floorDecay: 0.995,
};

export class Vad {
  private readonly o: Required<VadOptions>;
  private floor = 0.004;
  private inSpeech = false;
  private quietFrames = 0;
  private speechFrames = 0;
  private buffer: Float32Array[] = [];
  private readonly frameMs = (FRAME_SAMPLES / SAMPLE_RATE) * 1000;

  constructor(opts: VadOptions = {}) {
    this.o = { ...DEFAULTS, ...opts };
  }

  /** Feed one 20 ms frame (960 samples at 48 kHz). Returns any event it triggers. */
  push(frame: Float32Array): VadEvent | undefined {
    const level = rms(frame);
    const enterAt = Math.max(this.o.minRms, this.floor * this.o.enterFactor);
    const exitAt = Math.max(this.o.minRms * 0.7, this.floor * this.o.exitFactor);

    // Track the noise floor only while not in speech, so speech cannot raise the bar and
    // cause the detector to lose track of a long utterance.
    if (!this.inSpeech) this.floor = level * (1 - this.o.floorDecay) + this.floor * this.o.floorDecay;

    if (!this.inSpeech) {
      if (level >= enterAt) {
        this.inSpeech = true;
        this.quietFrames = 0;
        this.speechFrames = 1;
        this.buffer = [frame.slice()];
        return { type: "speech-start" };
      }
      return undefined;
    }

    // In speech: accumulate, and count down the hangover on quiet frames.
    this.buffer.push(frame.slice());
    if (level > exitAt) {
      this.speechFrames += 1;
      this.quietFrames = 0;
    } else {
      this.quietFrames += 1;
    }

    const quietMs = this.quietFrames * this.frameMs;
    const speechMs = this.speechFrames * this.frameMs;
    const tooLong = speechMs >= this.o.maxUtteranceMs;

    if (quietMs >= this.o.hangoverMs || tooLong) {
      this.inSpeech = false;
      this.quietFrames = 0;
      // Drop the hangover tail; it is silence by definition.
      const keep = this.buffer.length - this.quietFrames;
      const kept = this.buffer.slice(0, Math.max(0, keep));
      this.buffer = [];
      this.speechFrames = 0;
      const durationMs = kept.length * this.frameMs;
      if (durationMs < this.o.minSpeechMs) return undefined;
      return { type: "speech-end", samples: concat(kept), durationMs };
    }
    return undefined;
  }

  /** True while a turn is in progress. */
  get speaking(): boolean {
    return this.inSpeech;
  }

  /** Current adaptive noise floor, for tuning. */
  get noiseFloor(): number {
    return this.floor;
  }

  reset(): void {
    this.inSpeech = false;
    this.quietFrames = 0;
    this.speechFrames = 0;
    this.buffer = [];
  }
}

function concat(frames: Float32Array[]): Float32Array {
  const total = frames.reduce((n, f) => n + f.length, 0);
  const out = new Float32Array(total);
  let at = 0;
  for (const f of frames) {
    out.set(f, at);
    at += f.length;
  }
  return out;
}

/**
 * Ring buffer that keeps the most recent `seconds` of audio.
 *
 * Needed so that when the VAD declares speech, the utterance includes the ~100 ms before
 * the trigger frame — otherwise every utterance loses its first phoneme.
 */
export class PreRollBuffer {
  private chunks: Float32Array[] = [];

  private readonly seconds: number;
  private readonly frameMs: number;
  private readonly capacity: number;

  constructor(seconds: number, frameMs = 20) {
    this.seconds = seconds;
    this.frameMs = frameMs;
    this.capacity = Math.max(1, Math.ceil((seconds * 1000) / frameMs));
  }

  push(frame: Float32Array): void {
    this.chunks.push(frame.slice());
    if (this.chunks.length > this.capacity) this.chunks.shift();
  }

  drain(): Float32Array {
    const out = concat(this.chunks);
    this.chunks = [];
    return out;
  }
}