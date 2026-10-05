/**
 * Opus codec glue for the Google Voice media path.
 *
 * Google negotiates `opus/48000/2` (payload type 111), so the codec runs at 48 kHz with
 * 20 ms frames of 960 samples. RTP carries one complete opus packet per frame, which maps
 * cleanly onto the decoder's per-frame API.
 *
 * Two implementations are in play because neither package does both jobs well:
 *
 *  - **encode** via `opusscript` (emscripten libopus). It exposes an encoder *and*
 *    decoder, but is synchronous and older.
 *  - **decode** via `opus-decoder` (wasm). Faster and better maintained. Its
 *    `decodeFrame` resolves to an object with `channelData`, not a bare channel array —
 *    easy to get wrong.
 *
 * Everything is mono internally and duplicated to stereo on the wire, since a phone call is
 * mono and opus in stereo mostly wastes bitrate.
 */

import { createRequire } from "node:module";
import { OpusDecoder } from "opus-decoder";

const require = createRequire(import.meta.url);

export const SAMPLE_RATE = 48000;
export const FRAME_SAMPLES = 960; // 20 ms
export const CHANNELS = 1;

interface OpusCodec {
  /** Accepts Int16 PCM; Float32 is accepted by the typings but encodes as near-silence. */
  encode(pcm: Float32Array | Int16Array, frameSize: number): Buffer;
  decode(packet: Uint8Array): Float32Array;
  /** libopus control; takes a bitrate in bits/second. */
  encoderCTL?: (bitrate: number) => void;
  delete?(): void;
}

type OpusScriptCtor = new (sampleRate: number, channels: number, application?: number) => OpusCodec;

interface OpusScriptModule {
  Application: { VOIP: number; AUDIO: number; RESTRICTED_LOWDELAY: number };
  default?: OpusScriptCtor;
}

// opusscript is CommonJS and exposes the constructor either as the module itself or as
// `.default` depending on interop, so normalise both shapes. It ships no type declarations,
// hence the cast through unknown.
const required = require("opusscript") as unknown;
const mod = required as OpusScriptModule;
const OpusScript = ((mod as { default?: unknown }).default ?? required) as OpusScriptCtor;

export interface EncoderOptions {
  /** Target bitrate in bits/second. 24 kbps is a reasonable voice default. */
  bitrate?: number;
  /** libopus application. VOIP is right for calls. */
  application?: number;
}

/** Stateful opus encoder producing one packet per 20 ms frame. */
export class OpusEncoder {
  private readonly codec: OpusCodec;
  private readonly bitrate: number;
  /** False when the encoder ignored our bitrate request (opusscript does). */
  private bitrateApplied = false;

  constructor(opts: EncoderOptions = {}) {
    this.bitrate = opts.bitrate ?? 24000;
    this.codec = new OpusScript(SAMPLE_RATE, CHANNELS, opts.application ?? mod.Application.VOIP);
    // opusscript's encoderCTL throws "Unimplemented", so the bitrate is only a request.
    // Frame size still has to be passed per encode() call, which is what actually matters
    // for a fixed 20 ms pipeline.
    try {
      this.codec.encoderCTL?.(this.bitrate);
      this.bitrateApplied = true;
    } catch {
      this.bitrateApplied = false;
    }
  }

  /**
   * Encode 960 mono samples at 48 kHz into one opus packet.
   * Pass `null` to emit a DTX/keepalive frame when there is nothing to send.
   *
   * Note the Float32 -> Int16 conversion is *required*: opusscript's encoder takes Int16
   * PCM. Handing it Float32 in the nominal ±1.0 range silently produces a much smaller
   * packet (57 B vs 120 B) that decodes to near-silence, because ±1.0 is effectively
   * inaudible at int16 scale. Scale by 32767 and clamp.
   */
  encode(pcm: Float32Array | null): Buffer {
    if (pcm === null) {
      // 0xF8FFFE is a standard opus DTX frame (valid for any frame size).
      return Buffer.from([0xf8, 0xff, 0xfe]);
    }
    if (pcm.length !== FRAME_SAMPLES) {
      throw new Error(`expected ${FRAME_SAMPLES} samples (20 ms), got ${pcm.length}`);
    }
    return this.codec.encode(floatToInt16(pcm), FRAME_SAMPLES);
  }

  get bitrateBps(): number {
    return this.bitrate;
  }

  /** Whether the requested bitrate was actually honoured. */
  get bitrateIsApplied(): boolean {
    return this.bitrateApplied;
  }

  close(): void {
    this.codec.delete?.();
  }
}

export interface DecodedFrame {
  /** Mono samples for this frame. */
  samples: Float32Array;
  sampleRate: number;
  /** Errors reported by the wasm decoder, if any (rendered as strings). */
  errors: string[];
}

/** Stateful opus decoder; one call per inbound RTP payload. */
export class OpusFrameDecoder {
  private readonly decoder: InstanceType<typeof OpusDecoder>;
  private ready?: Promise<void>;

  constructor() {
    this.decoder = new OpusDecoder({
      channels: CHANNELS,
      streamCount: 1,
      coupledStreamCount: 0,
      // libopus pre-skip for a 48 kHz stream; the first few samples are encoder ramp-up.
      preSkip: 312,
    });
    this.ready = this.decoder.ready;
  }

  /** Decode one opus packet into 960 mono samples. */
  async decode(packet: Uint8Array): Promise<DecodedFrame> {
    if (this.ready) await this.ready;
    const out = await this.decoder.decodeFrame(packet);
    const channel = out.channelData?.[0];
    return {
      samples: channel instanceof Float32Array ? channel : new Float32Array(0),
      sampleRate: out.sampleRate ?? SAMPLE_RATE,
      errors: Array.isArray(out.errors) ? out.errors.map((e) => String(e)) : [],
    };
  }

  close(): void {
    void this.decoder.free?.();
  }
}

/**
 * Convert normalised Float32 samples (±1.0) to Int16.
 *
 * `opusscript` requires Int16 input; see OpusEncoder.encode.
 */
export function floatToInt16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i] ?? 0)) * 32767;
    out[i] = Math.round(v);
  }
  return out;
}

/** Convert Int16 samples to normalised Float32, clamping codec overshoot. */
export function int16ToFloat(samples: Int16Array | Buffer): Float32Array {
  const out = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    out[i] = Math.max(-1, Math.min(1, (samples[i] ?? 0) / 32767));
  }
  return out;
}

/** Root-mean-square level, for deciding whether a frame carries actual audio. */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += (samples[i] ?? 0) ** 2;
  return Math.sqrt(sum / samples.length);
}

/** Peak absolute amplitude. */
export function peak(samples: Float32Array): number {
  let p = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = Math.abs(samples[i] ?? 0);
    if (v > p) p = v;
  }
  return p;
}

/**
 * Splits an arbitrary PCM stream into 960-sample mono frames, zero-padding the last one.
 * Useful for feeding a mic or TTS output of arbitrary length.
 */
export function* toFrames(pcm: Float32Array, frameSamples = FRAME_SAMPLES): Generator<Float32Array> {
  for (let offset = 0; offset < pcm.length; offset += frameSamples) {
    const slice = pcm.subarray(offset, offset + frameSamples);
    if (slice.length === frameSamples) {
      yield slice;
    } else {
      const padded = new Float32Array(frameSamples);
      padded.set(slice);
      yield padded;
    }
  }
}

/** Convenience: generate a sine tone, handy for verifying the TX path audibly. */
export function sineFrame(freq: number, amplitude = 0.3, frameSamples = FRAME_SAMPLES): Float32Array {
  const out = new Float32Array(frameSamples);
  for (let i = 0; i < frameSamples; i++) {
    out[i] = Math.sin((2 * Math.PI * freq * i) / SAMPLE_RATE) * amplitude;
  }
  return out;
}