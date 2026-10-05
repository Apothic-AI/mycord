/**
 * Speech synthesis for the outbound leg, producing PCM ready for opus.
 *
 * Deliberately backend-agnostic: a phone agent usually wants a good neural voice, but the
 * part that matters to Google Voice is only "48 kHz mono Float32 in 20 ms frames". So the
 * contract is `speak(text) -> Float32Array @ 48 kHz` and the backend is swappable.
 *
 * Built-in backend is **espeak-ng**, because it is local, offline, needs no API key, and
 * is good enough to prove a two-way speech loop. It is robotic — swap in something better
 * (Qwen TTS, Kokoro, edge-tts) via `CommandTtsBackend` without touching the call path.
 *
 * espeak-ng emits 22.05 kHz mono s16 WAV, so ffmpeg resamples to the 48 kHz that
 * `opus/48000/2` requires.
 */

import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import { SAMPLE_RATE, floatToInt16, toFrames } from "./audio.ts";

const execFileAsync = promisify(execFile);

export interface TtsOptions {
  /** espeak-ng voice, e.g. "en-us". */
  voice?: string;
  /** Words per minute. */
  rate?: number;
  /** 0..100. */
  pitch?: number;
  /** Peak amplitude of the returned PCM. */
  gain?: number;
}

export interface TtsBackend {
  readonly name: string;
  /** Render `text` to mono Float32 samples at 48 kHz. */
  speak(text: string, opts?: TtsOptions): Promise<Float32Array>;
}

/** Resample a WAV file to 48 kHz mono s16 via ffmpeg and return Float32 samples. */
async function wavToPcm48k(path: string): Promise<Float32Array> {
  const { stdout } = await execFileAsync(
    "ffmpeg",
    ["-v", "error", "-i", path, "-f", "s16le", "-acodec", "pcm_s16le",
     "-ar", String(SAMPLE_RATE), "-ac", "1", "-"],
    { encoding: "buffer", maxBuffer: 64 * 1024 * 1024 },
  );
  const buf = stdout as unknown as Buffer;
  const ints = new Int16Array(buf.buffer, buf.byteOffset, Math.floor(buf.length / 2));
  const out = new Float32Array(ints.length);
  for (let i = 0; i < ints.length; i++) out[i] = (ints[i] ?? 0) / 32768;
  return out;
}

/** espeak-ng: local, offline, no credentials. Robotic but serviceable. */
export class EspeakTts implements TtsBackend {
  readonly name = "espeak-ng";

  async speak(text: string, opts: TtsOptions = {}): Promise<Float32Array> {
    const dir = await mkdtemp(join(tmpdir(), "gv-tts-"));
    const wav = join(dir, "out.wav");
    try {
      const args = ["-v", opts.voice ?? "en-us"];
      if (opts.rate !== undefined) args.push("-s", String(opts.rate));
      if (opts.pitch !== undefined) args.push("-p", String(opts.pitch));
      args.push("-w", wav, text);
      await execFileAsync("espeak-ng", args);
      const pcm = await wavToPcm48k(wav);
      const gain = opts.gain ?? 1.0;
      return gain === 1.0 ? pcm : pcm.map((v) => v * gain);
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  }
}

/**
 * Backend that shells out to an arbitrary command.
 *
 * The command must write a WAV (or any ffmpeg-readable audio) to stdout, or accept an
 * output path as the final argument. Used to plug in neural TTS without new dependencies:
 *
 *   new CommandTtsBackend("edge-tts", ["--voice", "en-US-Aria", "--text"], { argIndex: -1 })
 */
export class CommandTtsBackend implements TtsBackend {
  readonly name: string;
  private readonly baseArgs: string[];
  private readonly opts: { outputPathArg?: boolean };

  constructor(name: string, baseArgs: string[], opts: { outputPathArg?: boolean } = {}) {
    this.name = name;
    this.baseArgs = baseArgs;
    this.opts = opts;
  }

  async speak(text: string, opts: TtsOptions = {}): Promise<Float32Array> {
    const dir = await mkdtemp(join(tmpdir(), "gv-tts-"));
    const wav = join(dir, "out.wav");
    try {
      const args = [...this.baseArgs];
      if (this.opts.outputPathArg) args.push(text, wav);
      else args.push(text);

      if (this.opts.outputPathArg) {
        await execFileAsync(this.name, args);
      } else {
        const { stdout } = await execFileAsync(this.name, args, {
          encoding: "buffer",
          maxBuffer: 64 * 1024 * 1024,
        });
        const { writeFile } = await import("node:fs/promises");
        await writeFile(wav, stdout as unknown as Buffer);
      }
      return await wavToPcm48k(wav);
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  }
}

export interface UtteranceResult {
  /** Frames actually sent. */
  framesSent: number;
  /** Wall-clock duration of the utterance. */
  durationMs: number;
  /** Set if playback was cut short (barge-in or teardown). */
  interrupted?: boolean;
}

/**
 * Streams PCM into a live call as opus frames, paced at real time.
 *
 * Real-time pacing matters: dumping a whole utterance as fast as possible overruns the
 * far end's jitter buffer and the opening words are lost.
 */
export class SpeechSender {
  private cancelled = false;
  private timer: NodeJS.Timeout | undefined;
  /** Resolves when the utterance finishes or is interrupted. */
  private done?: Promise<UtteranceResult>;

  /** Milliseconds per 20 ms frame. Defaults to real time. */
  private readonly frameIntervalMs: number;
  private readonly send: (opusPacket: Buffer) => void;

  constructor(send: (opusPacket: Buffer) => void, frameIntervalMs = 20) {
    this.send = send;
    this.frameIntervalMs = frameIntervalMs;
  }

  /** Speak `pcm`, resolving when playback completes. */
  play(encodeFrame: (pcm: Float32Array) => Buffer, pcm: Float32Array): Promise<UtteranceResult> {
    this.stop();
    this.cancelled = false;
    const frames = [...toFrames(pcm)];

    this.done = new Promise<UtteranceResult>((resolve) => {
      let index = 0;
      const started = Date.now();
      const finish = (interrupted: boolean): void => {
        if (this.timer) clearInterval(this.timer);
        this.timer = undefined;
        resolve({
          framesSent: index,
          durationMs: Date.now() - started,
          ...(interrupted ? { interrupted } : {}),
        });
      };

      this.timer = setInterval(() => {
        if (this.cancelled) return finish(true);
        const frame = frames[index];
        if (!frame) return finish(false);
        this.send(encodeFrame(frame));
        index += 1;
      }, this.frameIntervalMs);
    });

    return this.done;
  }

  /** Cut off any in-flight playback (barge-in). */
  stop(): void {
    if (this.timer) {
      this.cancelled = true;
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }

  /** Await the current utterance, if any. */
  async settled(): Promise<UtteranceResult | undefined> {
    return this.done;
  }
}

/** Convenience: 48 kHz mono Int16 of `pcm`, useful for writing WAVs in tests. */
export const toInt16 = (pcm: Float32Array): Int16Array => floatToInt16(pcm);