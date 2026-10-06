/**
 * Speech-to-text for the inbound leg, backed by local Whisper.
 *
 * Uses a persistent Python worker rather than the `whisper` CLI: loading
 * large-v3-turbo costs seconds, and a phone agent transcribes an utterance every
 * few seconds, so a per-turn model load would dominate latency. The worker holds
 * the model in memory and answers one JSON request per line.
 *
 * `WhisperCliStt` is kept as a zero-setup fallback for machines without the
 * Python package available.
 */

import { execFile, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { homedir, tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { SAMPLE_RATE, floatToInt16 } from "./audio.ts";
import type { Transcript } from "./types.ts";

export type { Transcript };

const execFileAsync = promisify(execFile);
const WORKER = fileURLToPath(new URL("../tools/whisper_worker.py", import.meta.url));

/**
 * Model sizes, valid for both backends. `small.en` is the sweet spot for phone audio:
 * intelligible speech at a fraction of `turbo`'s cost.
 */
export type WhisperModel =
  | "tiny" | "tiny.en" | "base" | "base.en" | "small" | "small.en"
  | "medium" | "medium.en" | "turbo" | "large-v3";

export interface SttOptions {
  language?: string;
  model?: WhisperModel;
  /** `cuda` when a GPU is present and the model fits. */
  device?: "cpu" | "cuda";
  /**
   * `faster` is CTranslate2 int8 — ~4x openai-whisper on CPU, with VAD.
   * `openai` is the reference implementation. Default `auto` prefers `faster`.
   */
  backend?: "auto" | "faster" | "openai";
  /** CTranslate2 compute type for the faster backend. */
  computeType?: string;
  /** Interpreter to use. Detected when omitted. */
  python?: string;
}

/**
 * Whisper's native sample rate. Both backends expect 16 kHz mono, and faster-whisper
 * assumes it when handed a raw NumPy array — so resample here rather than letting a
 * 22.05 kHz TTS file be reinterpreted at the wrong speed.
 */
const WHISPER_RATE = 16000;

/** Resample 48 kHz mono Float32 to a 16 kHz mono s16 WAV. */
async function writeWav(dir: string, pcm: Float32Array): Promise<string> {
  const path = join(dir, "in.wav");
  const raw = Buffer.from(floatToInt16(pcm).buffer);
  await new Promise<void>((resolve, reject) => {
    const child = spawn(
      "ffmpeg",
      ["-v", "error", "-f", "s16le", "-ar", String(SAMPLE_RATE), "-ac", "1", "-i", "-",
       "-ar", String(WHISPER_RATE), "-ac", "1", "-c:a", "pcm_s16le", "-y", path],
      { stdio: ["pipe", "ignore", "pipe"] },
    );
    let err = "";
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (c: string) => { err += c; });
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`ffmpeg exited ${code}: ${err.slice(0, 200)}`)),
    );
    child.stdin.end(raw);
  });
  return path;
}

/** Where the faster-whisper virtualenv is installed by default. */
const FW_VENV = process.env.GVOICE_FW_VENV ?? join(homedir(), ".local", "share", "gvoice", "fw-venv");

interface Resolved {
  python: string;
  backend: "faster" | "openai";
}

let resolved: Resolved | undefined;

/**
 * Find an interpreter that can import a Whisper implementation.
 *
 * The `whisper` CLI is often installed somewhere other than the default `python3` — on
 * this machine it lives in a Homebrew Cellar the system interpreter cannot see — and
 * faster-whisper usually lives in its own venv. Probe the candidates once and remember
 * the winner, preferring faster-whisper because openai-whisper's CPU-only torch measured
 * ~15 s for a 5 s utterance, which is far too slow to converse.
 */
async function findBackend(explicit?: string, want: SttOptions["backend"] = "auto"): Promise<Resolved> {
  if (resolved && !explicit) return resolved;

  const candidates: Array<{ python: string; module: string; backend: "faster" | "openai" }> = [];
  if (explicit) candidates.push({ python: explicit, module: "faster_whisper", backend: "faster" });
  candidates.push({ python: join(FW_VENV, "bin", "python"), module: "faster_whisper", backend: "faster" });
  candidates.push({ python: "python3", module: "faster_whisper", backend: "faster" });

  // The interpreter behind the `whisper` console script definitely has openai-whisper.
  try {
    const { stdout } = await execFileAsync("sh", ["-c", "command -v whisper"]);
    const cli = stdout.trim();
    if (cli) {
      const { stdout: shebang } = await execFileAsync("sh", ["-c", `head -1 ${JSON.stringify(cli)}`]);
      const m = /^#!\s*(\S+)/.exec(shebang.trim());
      if (m?.[1]) candidates.push({ python: m[1], module: "whisper", backend: "openai" });
    }
  } catch {
    /* no whisper CLI on PATH */
  }
  candidates.push({ python: "python3", module: "whisper", backend: "openai" });

  for (const c of candidates) {
    if (want !== "auto" && want !== c.backend) continue;
    try {
      await execFileAsync(c.python, ["-c", `import ${c.module}`]);
      resolved = { python: c.python, backend: c.backend };
      return resolved;
    } catch {
      /* try the next candidate */
    }
  }
  throw new Error(
    "no Whisper backend found. Install faster-whisper (recommended: pip install faster-whisper " +
      "into ~/.local/share/gvoice/fw-venv) or openai-whisper, or pass SttOptions.python.",
  );
}

/** Local Whisper via a long-lived worker process. */
export class WhisperStt {
  readonly name = "whisper-worker";
  private proc: ChildProcessWithoutNullStreams | undefined;
  private ready: Promise<void> | undefined;
  private queue: Promise<unknown> = Promise.resolve();
  private stopping = false;
  private lineBuffer = "";
  private pending: Array<(v: Transcript | { error: string }) => void> = [];

  private readonly opts: SttOptions;
  constructor(opts: SttOptions = {}) {
    this.opts = opts;
  }

  private spawning: Promise<ChildProcessWithoutNullStreams> | undefined;

  private spawnWorker(): Promise<ChildProcessWithoutNullStreams> {
    this.spawning ??= (async () => {
    const found = await findBackend(this.opts.python, this.opts.backend);
    const proc = spawn(
      found.python,
      [
        WORKER,
        `--backend=${found.backend}`,
        `--model=${this.opts.model ?? "small.en"}`,
        `--device=${this.opts.device ?? "cpu"}`,
        `--compute_type=${this.opts.computeType ?? "int8"}`,
      ],
      { stdio: ["pipe", "pipe", "pipe"] },
    );

    let resolveReady: () => void = () => {};
    let rejectReady: (e: Error) => void = () => {};
    this.ready = new Promise<void>((res, rej) => {
      resolveReady = res;
      rejectReady = rej;
    });
    // A worker that dies mid-call should not hang the agent forever.
    const failTimer = setTimeout(() => rejectReady(new Error("whisper worker did not become ready")), 180_000);
    this.ready.then(() => clearTimeout(failTimer), () => clearTimeout(failTimer));

    proc.stdout.setEncoding("utf8");
    proc.stdout.on("data", (chunk: string) => {
      this.lineBuffer += chunk;
      let idx: number;
      while ((idx = this.lineBuffer.indexOf("\n")) >= 0) {
        const line = this.lineBuffer.slice(0, idx).trim();
        this.lineBuffer = this.lineBuffer.slice(idx + 1);
        if (!line) continue;
        const msg = JSON.parse(line) as Transcript & { ready?: boolean };
        if (msg.ready) {
          resolveReady();
          continue;
        }
        this.pending.shift()?.(msg);
      }
    });
    proc.stderr.setEncoding("utf8");
    proc.stderr.on("data", () => {});
    proc.on("exit", (code) => {
      if (!this.stopping) {
        const err = new Error(`whisper worker exited with code ${code}`);
        this.pending.splice(0).forEach((fn) => fn({ error: err.message }));
        rejectReady(err);
      }
      this.proc = undefined;
    });
    return proc;
    })();
    return this.spawning;
  }

  /** Ensure the worker is up, loading the model once. */
  async start(): Promise<void> {
    this.proc ??= await this.spawnWorker();
    await this.ready;
  }

  /** Transcribe 48 kHz mono Float32 samples. Requests are serialised. */
  async transcribe(pcm: Float32Array): Promise<Transcript> {
    const task = this.queue.then(async () => {
      if (this.stopping) return { text: "", segments: [] };
      await this.start();
      const dir = await mkdtemp(join(tmpdir(), "gv-stt-"));
      try {
        const wav = await writeWav(dir, pcm);
        const proc = this.proc;
        if (!proc) throw new Error("whisper worker is not running");
        const result = new Promise<Transcript | { error: string }>((resolve) => {
          this.pending.push(resolve);
          proc.stdin.write(
            `${JSON.stringify({ wav, language: this.opts.language ?? "en" })}\n`,
          );
        });
        const res = await result;
        if ("error" in res) throw new Error(res.error);
        return res;
      } finally {
        await rm(dir, { recursive: true, force: true }).catch(() => {});
      }
    });
    // Keep the queue alive even when a turn fails.
    this.queue = task.catch(() => undefined);
    return task;
  }

  async stop(): Promise<void> {
    // Resolve anything in flight as empty text rather than an error: callers routinely stop
    // the worker while a final turn is still transcribing, and that is not a failure.
    this.stopping = true;
    this.pending.splice(0).forEach((fn) => fn({ text: "", segments: [] }));
    this.proc?.stdin.write("quit\n");
    this.proc?.kill();
    this.proc = undefined;
    this.spawning = undefined;
    this.ready = undefined;
  }
}

/** Zero-setup fallback: shells out to the `whisper` CLI, loading the model per call. */
export class WhisperCliStt {
  readonly name = "whisper-cli";
  private readonly opts: SttOptions;
  constructor(opts: SttOptions = {}) {
    this.opts = opts;
  }

  async transcribe(pcm: Float32Array): Promise<Transcript> {
    const dir = await mkdtemp(join(tmpdir(), "gv-stt-"));
    try {
      const wav = await writeWav(dir, pcm);
      const args = [
        "--model", this.opts.model ?? "turbo",
        "--language", this.opts.language ?? "en",
        "--output_format", "json",
        "--output_dir", dir,
        "--fp16", "False",
        "--verbose", "False",
        wav,
      ];
      if (this.opts.device) args.push("--device", this.opts.device);
      await execFileAsync("whisper", args);
      const raw = await import("node:fs/promises").then((fs) =>
        fs.readFile(join(dir, "in.json"), "utf8"),
      );
      const parsed = JSON.parse(raw) as { text?: string; segments?: Transcript["segments"] };
      return { text: (parsed.text ?? "").trim(), segments: parsed.segments ?? [] };
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  }
}