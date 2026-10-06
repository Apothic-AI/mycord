/**
 * Opus decoding and PCM helpers for the Google Voice media path.
 *
 * Google negotiates `opus/48000/2` (payload type 111), so the codec runs at 48 kHz with
 * 20 ms frames of 960 samples. RTP carries one complete opus packet per frame, which maps
 * cleanly onto the decoder's per-frame API.
 *
 * Decoding uses `opus-decoder` (wasm). Note its `decodeFrame` resolves to an object with
 * `channelData`, not a bare channel array — easy to get wrong.
 *
 * Encoding lives in `opusenc.ts`, not here. It was previously done by `opusscript`, whose
 * output no conformant decoder could read; see that file for the details.
 *
 * Everything is mono internally and duplicated to stereo on the wire, since a phone call is
 * mono and opus in stereo mostly wastes bitrate.
 */

import { OpusDecoder } from "opus-decoder";

export const SAMPLE_RATE = 48000;
export const FRAME_SAMPLES = 960; // 20 ms
export const CHANNELS = 1;

/** One decoded 20 ms frame. */
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
 * Scale `samples` so its peak hits `target`, and return the gain applied.
 *
 * Neural TTS often renders straight to full scale, which clips once opus encodes it and
 * sounds harsh on the phone. Google Voice's own signalling audio peaks around 0.16, so
 * 0.7 is a safe ceiling for speech: loud, no inter-sample clipping.
 */
export function normalize(samples: Float32Array, target = 0.7): number {
  const p = peak(samples);
  if (p === 0) return 1;
  const gain = target / p;
  if (gain !== 1) {
    for (let i = 0; i < samples.length; i++) samples[i] = (samples[i] ?? 0) * gain;
  }
  return gain;
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