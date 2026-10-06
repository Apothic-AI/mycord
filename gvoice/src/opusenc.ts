/**
 * Opus encoding via `opusenc` (opus-tools), replacing `opusscript`.
 *
 * Why the switch: `opusscript` emits packets that no conformant decoder will accept. Its
 * output looked plausible — 120 B packets, a legal-looking TOC byte — but decoding it
 * (with either `opus-decoder` or Google's own decoder) produced noise rather than the
 * input signal, so outbound speech was unintelligible on the wire. Verified by decoding a
 * known sine: `opusscript` in at amplitude 0.1 came back as noise at peak 1.53, i.e. no
 * correlation with the input at all. `opusenc` from libopus round-trips correctly.
 *
 * Encoding whole utterances rather than frame-by-frame is also the right shape for opus:
 * variable bitrate and lookahead only work across a sequence. That matches how
 * `SpeechSender` already plays an utterance at a time.
 */

import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import { CHANNELS, SAMPLE_RATE, floatToInt16 } from "./audio.ts";

const execFileAsync = promisify(execFile);

/** Minimal RIFF/WAVE header plus PCM s16 payload for mono 48 kHz samples. */
export function wavBytes(pcm: Float32Array, sampleRate = SAMPLE_RATE): Buffer {
  const data = Buffer.from(floatToInt16(pcm).buffer);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16); // PCM fmt chunk size
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(CHANNELS, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * CHANNELS * 2, 28);
  header.writeUInt16LE(CHANNELS * 2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

/**
 * Extract raw opus packets from an Ogg Opus file.
 *
 * Ogg pages carry a segment table in which any segment shorter than 255 bytes terminates
 * a packet, and packets may straddle pages, so segments accumulate until one terminates.
 * The first two packets are OpusHead and OpusTags, not audio.
 */
export function oggPackets(buf: Buffer): Buffer[] {
  const packets: Buffer[] = [];
  let pending: Buffer[] = [];

  for (let i = 0; i + 27 < buf.length; ) {
    if (buf.toString("latin1", i, i + 4) !== "OggS") break;
    const nSeg = buf[i + 26]!;
    const segTable = i + 27;
    let off = segTable + nSeg;

    for (let s = 0; s < nSeg; s++) {
      const len = buf[segTable + s]!;
      pending.push(buf.subarray(off, off + len));
      off += len;
      if (len < 255) {
        packets.push(Buffer.concat(pending));
        pending = [];
      }
    }
    i = off;
  }
  return packets;
}

export interface UtteranceEncoderOptions {
  /** Target bitrate in bits per second. Telephony speech is happy at 24 kbps. */
  bitrate?: number;
  /** Frame size in ms. 20 ms matches the 960-sample frames on the wire. */
  frameSizeMs?: number;
  /** Ask libopus for its speech tuning. */
  speech?: boolean;
}

export class OpusUtteranceEncoder {
  private readonly kbps: number;
  private readonly speech: boolean;
  private readonly frameSizeMs: number;

  constructor(opts: UtteranceEncoderOptions = {}) {
    // opusenc takes kbit/s per channel, in the range 6-256.
    this.kbps = (opts.bitrate ?? 24000) / 1000;
    this.speech = opts.speech ?? true;
    this.frameSizeMs = opts.frameSizeMs ?? 20;
  }

  /**
   * Encode 48 kHz mono PCM into RTP-ready opus packets.
   * Throws if `opusenc` is unavailable, so a missing binary fails loudly rather than
   * silently putting noise on the call.
   */
  async encode(pcm: Float32Array): Promise<Buffer[]> {
    const dir = await mkdtemp(join(tmpdir(), "gv-opus-"));
    const wav = join(dir, "in.wav");
    const opus = join(dir, "out.opus");
    try {
      await writeFile(wav, wavBytes(pcm));
      const args = [
        "--quiet",
        "--bitrate", String(this.kbps),
        "--framesize", String(this.frameSizeMs),
      ];
      if (this.speech) args.push("--speech");
      args.push(wav, opus);
      await execFileAsync("opusenc", args);
      const packets = oggPackets(await readFile(opus));
      // Drop OpusHead and OpusTags; only audio packets belong in RTP.
      return packets.slice(2);
    } catch (err) {
      throw new Error(
        `opus encoding failed (is opus-tools installed?): ` +
          `${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  }
}