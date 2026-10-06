/**
 * Speech-to-text backends, each a long-lived worker process.
 *
 * Loading a speech model costs seconds, and a phone agent transcribes an utterance every
 * few seconds, so every backend here keeps its model resident and answers one request per
 * line over stdin/stdout.
 *
 * Preference order is deliberate and measured on this project:
 *
 *  1. **parakeet-redux** (`moondream/parakeet-redux`) — NVIDIA parakeet-tdt-0.6b-v3 quantised
 *     to 1.58-bit ternary weights. No multiplies in the hot loop, so it runs at 12x realtime
 *     on AVX2-only hardware here and 113x on AVX-512 VNNI. A 3 s phone utterance transcribes
 *     in ~0.25 s versus ~1.8 s for faster-whisper. It also accepts growing audio, which is
 *     what makes streaming partials possible at all.
 *  2. **faster-whisper** (CTranslate2 int8) — better on noisy audio, so it stays as a
 *     fallback. parakeet-redux trades accuracy at low SNR (leaderboard: 9.04 vs 6.72 WER on
 *     MUSAN), which is not nothing for telephone audio.
 *  3. **openai-whisper** — last resort. ~15 s per 5 s utterance on CPU-only torch.
 *
 * All backends receive 16 kHz mono: Whisper's native rate, and parakeet is happy at it.
 */

import { execFile, spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

import { SAMPLE_RATE, floatToInt16 } from "./audio.ts";
import type { SttBackend, Transcript } from "./types.ts";

const execFileAsync = promisify(execFile);
const TOOLS = fileURLToPath(new URL("../tools/", import.meta.url));
const PARAKEET_WORKER = join(TOOLS, "parakeet_worker.py");
const WHISPER_WORKER = join(TOOLS, "whisper_worker.py");

/** Where the model venvs live. Override with GVOICE_PARAKEET_VENV / GVOICE_FW_VENV. */
const PARAKEET_VENV = process.env.GVOICE_PARAKEET_VENV ?? join(homedir(), ".local", "share", "gvoice", "asr-venv");
const FW_VENV = process.env.GVOICE_FW_VENV ?? join(homedir(), ".local", "share", "gvoice", "fw-venv");

/**
 * Whisper's native sample rate, and parakeet's expected input rate.
 *
 * Handing either backend a 22.05 kHz array reinterpreted at the wrong speed returns
 * fluent, completely wrong text rather than an error, so resampling happens here rather
 * than being left to the caller's assumptions.
 */
const ASR_RATE = 16000;

export type WhisperModel =
  | "tiny" | "tiny.en" | "base" | "base.en" | "small" | "small.en"
  | "medium" | "medium.en" | "turbo" | "large-v3";

/** Resample 48 kHz mono Float32 to a 16 kHz mono s16 WAV. */
async function writeWav(dir: string, pcm: Float32Array): Promise<string> {
  const path = join(dir, "in.wav");
  const raw = Buffer.from(floatToInt16(pcm).buffer);
  await new Promise<void>((resolve, reject) => {
    const child = spawn(
      "ffmpeg",
      ["-v", "error", "-f", "s16le", "-ar", String(SAMPLE_RATE), "-ac", "1", "-i", "-",
       "-ar", String(ASR_RATE), "-ac", "1", "-c:a", "pcm_s16le", "-y", path],
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

export interface WorkerOptions {
  /** Interpreter override. Normally auto-detected. */
  python?: string;
  model?: string;
  device?: "cpu" | "cuda" | "mps";
}

/**
 * Shared plumbing for the worker-process backends: one child process, newline-delimited
 * JSON, serialised requests, and a ready handshake.
 */
export abstract class WorkerStt implements SttBackend {
  protected proc: ChildProcessWithoutNullStreams | undefined;
  protected ready: Promise<void> | undefined;
  protected spawning: Promise<ChildProcessWithoutNullStreams> | undefined;
  protected stopping = false;
  protected queue: Promise<unknown> = Promise.resolve();
  private lineBuffer = "";
  protected pending: Array<(v: never) => void> = [];
  protected loadSeconds = 0;

  abstract readonly name: string;
  abstract readonly args: string[];
  /** Interpreter that can import this backend's library. */
  abstract readonly python: string;

  /** Full transcription of settled audio. */
  abstract transcribe(pcm: Float32Array): Promise<Transcript>;


  protected spawnWorker(): Promise<ChildProcessWithoutNullStreams> {
    this.spawning ??= (async () => {
      this.stopping = false;
      const env: Record<string, string> = {};
      for (const [k, v] of Object.entries(process.env)) if (v !== undefined) env[k] = v;
      // Parakeet pulls in torch and its own thread pool; an empty OMP_NUM_THREADS makes
      // libgomp abort, so only forward it when actually set.
      const proc = spawn(this.python, this.args, { stdio: ["pipe", "pipe", "pipe"], env });

      let resolveReady: () => void = () => {};
      let rejectReady: (e: Error) => void = () => {};
      this.ready = new Promise<void>((res, rej) => {
        resolveReady = res;
        rejectReady = rej;
      });
      const timer = setTimeout(
        () => rejectReady(new Error(`${this.name} worker did not become ready`)),
        300_000,
      );
      this.ready.then(() => clearTimeout(timer), () => clearTimeout(timer));

      proc.stdout.setEncoding("utf8");
      proc.stdout.on("data", (chunk: string) => {
        this.lineBuffer += chunk;
        let idx: number;
        while ((idx = this.lineBuffer.indexOf("\n")) >= 0) {
          const line = this.lineBuffer.slice(0, idx).trim();
          this.lineBuffer = this.lineBuffer.slice(idx + 1);
          if (!line) continue;
          let msg: Record<string, unknown>;
          try {
            msg = JSON.parse(line) as Record<string, unknown>;
          } catch {
            continue;
          }
          if (msg.ready === true) {
            this.loadSeconds = Number(msg.loadSeconds ?? 0);
            resolveReady();
            continue;
          }
          (this.pending.shift() as ((v: unknown) => void) | undefined)?.(msg);
        }
      });
      proc.stderr.setEncoding("utf8");
      // Surface worker diagnostics only if we are not yet ready; later noise is harmless.
      proc.stderr.on("data", (c: string) => {
        if (this.ready && !this.settled) process.stderr.write(`[${this.name}] ${c}`);
      });
      proc.on("exit", (code) => {
        if (!this.stopping) {
          const err = new Error(`${this.name} worker exited with code ${code}`);
          this.pending.splice(0).forEach((fn) =>
            (fn as unknown as (v: unknown) => void)({ error: err.message }),
          );
          rejectReady(err);
        }
        this.proc = undefined;
      });
      return proc;
    })();
    return this.spawning;
  }

  private settled = false;

  /** Start the worker and wait for its ready handshake. */
  async start(): Promise<void> {
    this.settled = false;
    this.proc ??= await this.spawnWorker();
    await this.ready;
    this.settled = true;
  }

  /** Seconds spent loading the model, reported in readiness output. */
  get modelLoadSeconds(): number {
    return this.loadSeconds;
  }

  protected request(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const proc = this.proc;
    if (!proc) return Promise.reject(new Error(`${this.name} worker is not running`));
    return new Promise((resolve) => {
      this.pending.push(resolve as (v: never) => void);
      proc.stdin.write(`${JSON.stringify(payload)}\n`);
    });
  }

  /** Write PCM to a temp WAV, send one request, and clean up. */
  protected async withWav(
    pcm: Float32Array,
    send: (wav: string) => Promise<Record<string, unknown>>,
  ): Promise<Record<string, unknown>> {
    const dir = await mkdtemp(join(tmpdir(), "gv-asr-"));
    try {
      const wav = await writeWav(dir, pcm);
      return await send(wav);
    } finally {
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  }

  /** Serialise work and guarantee cleanup even when a turn fails. */
  protected serialise<T>(fn: () => Promise<T>): Promise<T> {
    const task = this.queue.then(async () => {
      if (this.stopping) throw new Error(`${this.name} worker is stopping`);
      await this.start();
      return fn();
    });
    this.queue = task.catch(() => undefined);
    return task;
  }

  async stop(): Promise<void> {
    // Resolve anything in flight as empty text: callers routinely stop the worker while a
    // final turn is still transcribing, and that is not a failure.
    this.stopping = true;
    this.pending.splice(0).forEach((fn) =>
      (fn as unknown as (v: unknown) => void)({ text: "", segments: [] }),
    );
    try {
      this.proc?.stdin.write("quit\n");
    } catch {
      /* pipe already closed */
    }
    this.proc?.kill();
    this.proc = undefined;
    this.spawning = undefined;
    this.ready = undefined;
  }
}

/** parakeet-redux: ternary Parakeet v3. Fastest, and the only one that can stream. */
export class ParakeetStt extends WorkerStt {
  readonly name = "parakeet-redux";
  readonly python: string;
  private readonly model: string;
  private readonly device: string;

  constructor(opts: WorkerOptions = {}) {
    super();
    this.python = opts.python ?? join(PARAKEET_VENV, "bin", "python");
    this.model = opts.model ?? process.env.GV_PARAKEET_MODEL ?? "moondream/parakeet-redux";
    this.device = opts.device ?? process.env.GV_PARAKEET_DEVICE ?? "cpu";
  }

  readonly args: string[] = [PARAKEET_WORKER];

  async transcribe(pcm: Float32Array): Promise<Transcript> {
    return this.serialise(async () => {
      const res = await this.withWav(pcm, (wav) => this.request({ wav, stream: false }));
      if (res.error) throw new Error(String(res.error));
      return {
        text: String(res.text ?? "").trim(),
        segments: (res.segments as Transcript["segments"] | undefined) ?? [],
      };
    });
  }

  /** Streaming partial: transcribes whatever has accumulated so far. */
  async partial(pcm: Float32Array): Promise<string> {
    return this.serialise(async () => {
      const res = await this.withWav(pcm, (wav) => this.request({ wav, stream: true }));
      if (res.error) throw new Error(String(res.error));
      // Too short to transcribe yet: report nothing rather than a guess.
      if (res.skipped) return "";
      return String(res.partial ?? "").trim();
    });
  }
}

export interface SttOptions {
  language?: string;
  model?: WhisperModel;
  device?: "cpu" | "cuda";
  backend?: "auto" | "parakeet" | "faster" | "openai";
  computeType?: string;
  python?: string;
}

/** faster-whisper: CTranslate2 int8, more accurate on noisy audio than parakeet-redux. */
export class WhisperStt extends WorkerStt {
  readonly name = "whisper-worker";
  readonly python: string;
  readonly args: string[];

  constructor(opts: SttOptions = {}) {
    super();
    this.python = opts.python ?? join(FW_VENV, "bin", "python");
    this.args = [
      WHISPER_WORKER,
      "--backend=faster",
      `--model=${opts.model ?? "small.en"}`,
      `--device=${opts.device ?? "cpu"}`,
      `--compute_type=${opts.computeType ?? "int8"}`,
    ];
  }

  async transcribe(pcm: Float32Array): Promise<Transcript> {
    return this.serialise(async () => {
      const res = await this.withWav(pcm, (wav) => this.request({ wav }));
      if (res.error) throw new Error(String(res.error));
      return {
        text: String(res.text ?? "").trim(),
        segments: (res.segments as Transcript["segments"] | undefined) ?? [],
      };
    });
  }
}

/**
 * Pick the best available backend.
 *
 * Probes for an interpreter that can import each library rather than trusting the default
 * `python3`, because these are usually installed into their own venvs and the system
 * interpreter cannot see them.
 */
export async function createStt(opts: SttOptions = {}): Promise<WorkerStt> {
  const want = opts.backend ?? "auto";
  const candidates: Array<{ ctor: () => WorkerStt; module: string; kind: string }> = [
    { ctor: () => new ParakeetStt(opts.python ? { python: opts.python } : {}), module: "moondream", kind: "parakeet" },
    { ctor: () => new WhisperStt(opts), module: "faster_whisper", kind: "faster" },
  ];

  if (want !== "auto") {
    const only = candidates.filter((c) => c.kind === want);
    if (only.length) return only[0]!.ctor();
  }

  for (const c of candidates) {
    if (want !== "auto" && want !== c.kind) continue;
    const py = c.kind === "parakeet"
      ? opts.python ?? join(PARAKEET_VENV, "bin", "python")
      : join(FW_VENV, "bin", "python");
    try {
      await execFileAsync(py, ["-c", `import ${c.module}`]);
      return c.ctor();
    } catch {
      /* try the next candidate */
    }
  }

  // No venv found; fall back to whatever the `whisper` CLI's interpreter can import.
  try {
    const { stdout } = await execFileAsync("sh", ["-c", "command -v whisper"]);
    const cli = stdout.trim();
    if (cli) {
      const { stdout: shebang } = await execFileAsync("sh", ["-c", `head -1 ${JSON.stringify(cli)}`]);
      const m = /^#!\s*(\S+)/.exec(shebang.trim());
      if (m?.[1]) {
        await execFileAsync(m[1], ["-c", "import whisper"]);
        return new WhisperStt({ ...opts, python: m[1] });
      }
    }
  } catch {
    /* no whisper CLI */
  }

  throw new Error(
    "no speech-to-text backend available. Install parakeet-redux (recommended): " +
      "`uv pip install moondream` into ~/.local/share/gvoice/asr-venv, " +
      "or faster-whisper into ~/.local/share/gvoice/fw-venv.",
  );
}