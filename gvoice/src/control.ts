/**
 * Agent-facing control surface for a live Google Voice call.
 *
 * The point of this module is that an external agent — a model, a script, anything over
 * HTTP — drives the call without linking against this codebase. Two directions:
 *
 *   agent -> call : send text to be spoken, press DTMF, hang up, listen for speech
 *   call -> agent : a *stream* of transcript events as speech is recognised
 *
 * Transcript streaming matters more than it sounds. Waiting for a final transcript after a
 * whole turn means the agent cannot react to the first clause, and a three-second dead air
 * makes the far end think the call dropped. So partials are emitted while the far end is
 * still speaking, with a `final` flag on the settled result.
 *
 * Transport is HTTP + Server-Sent Events. SSE rather than WebSocket because the traffic is
 * one-directional, it survives proxies, and `curl` can read it, which makes the whole thing
 * debuggable from a terminal. Long-lived SSE connections are exactly what browsers and
 * proxies expect for this.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";

export type TranscriptEventKind = "speech-start" | "partial" | "final";

export interface TranscriptEvent {
  /** Stable id shared by every partial and the final of one utterance. */
  utteranceId: string;
  kind: TranscriptEventKind;
  /** Text recognised so far. Empty for `speech-start`. */
  text: string;
  /** Seconds since the call started. */
  at: number;
}

export interface CallHandle {
  /** Speak text on the call. Resolves when the utterance has been queued. */
  say(text: string): Promise<void>;
  /** Stop the current utterance immediately. */
  shutUp(): void;
  /** Send a DTMF digit. */
  press(digit: string): void;
  /** Terminate the call. */
  hangup(): void;
}

/** What a transport adapter must supply for the control surface to work. */
export interface ControlHost extends CallHandle {
  /** Subscribe to transcript events. Returns an unsubscribe function. */
  onTranscript(fn: (e: TranscriptEvent) => void): () => void;
  /** True while the far end is speaking. */
  isFarEndSpeaking(): boolean;
  /** Seconds since the call connected. */
  elapsedSeconds(): number;
}

export interface ControlServerOptions {
  host?: string;
  port?: number;
  /** Shared secret. Requests must send `x-gvoice-token`. */
  token?: string;
  /** Called when the call ends by any route. */
  onHangup?: () => void;
  log?: (msg: string) => void;
}

interface Client {
  id: string;
  res: ServerResponse;
}

/**
 * HTTP + SSE control server.
 *
 * Every endpoint except `/events` is a plain request/response; `/events` holds the stream
 * open. Tokens are checked on every route including the SSE upgrade, because an unauthenticated
 * stream is a live transcript of someone's phone call.
 */
export class ControlServer {
  private readonly clients = new Set<Client>();
  private server: Server | undefined;
  private address: { host: string; port: number } | undefined;
  private closed = false;

  private readonly host: ControlHost;
  private readonly opts: ControlServerOptions;

  constructor(host: ControlHost, opts: ControlServerOptions = {}) {
    this.host = host;
    this.opts = opts;
  }

  private log(msg: string): void {
    this.opts.log?.(msg);
  }

  private authorized(req: IncomingMessage): boolean {
    const token = this.opts.token;
    if (!token) return true;
    return req.headers["x-gvoice-token"] === token;
  }

  private async readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const raw = Buffer.concat(chunks).toString("utf8");
    if (!raw) return {};
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      throw new Error("body is not valid JSON");
    }
  }

  private json(res: ServerResponse, status: number, body: unknown): void {
    const payload = JSON.stringify(body);
    res.writeHead(status, {
      "content-type": "application/json",
      "content-length": Buffer.byteLength(payload),
    });
    res.end(payload);
  }

  private sse(res: ServerResponse): void {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      // Defeats proxy buffering, which otherwise holds transcripts until the buffer fills.
      "x-accel-buffering": "no",
    });
    res.write(": connected\n\n");
    // Comment frames keep intermediaries from reaping an idle connection.
    const keepAlive = setInterval(() => res.write(": ping\n\n"), 15000);
    keepAlive.unref?.();
    res.on("close", () => clearInterval(keepAlive));
  }

  private broadcast(event: TranscriptEvent): void {
    const frame = `event: transcript\ndata: ${JSON.stringify(event)}\n\n`;
    for (const client of this.clients) {
      // A dead client must not break delivery for the others.
      try {
        client.res.write(frame);
      } catch {
        this.clients.delete(client);
      }
    }
  }

  /** Start listening. Resolves once bound, so callers know the port is usable. */
  async listen(): Promise<{ host: string; port: number; url: string }> {
    if (this.closed) throw new Error("control server is closed");
    const host = this.opts.host ?? "127.0.0.1";
    const port = this.opts.port ?? 8787;

    const server = createServer((req, res) => {
      void this.route(req, res).catch((err: unknown) => {
        if (!res.headersSent) {
          this.json(res, 500, { error: err instanceof Error ? err.message : String(err) });
        } else {
          res.end();
        }
      });
    });
    this.server = server;

    await new Promise<void>((resolve, reject) => {
      server.once("error", reject);
      server.listen(port, host, () => {
        server.removeListener("error", reject);
        resolve();
      });
    });

    const addr = server.address();
    const actual = typeof addr === "object" && addr ? addr.port : port;
    this.address = { host, port: actual };
    const url = `http://${host}:${actual}`;
    this.log(`control server on ${url}`);
    return { host, port: actual, url };
  }

  private async route(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const path = (req.url ?? "/").split("?")[0];
    const method = req.method ?? "GET";

    if (path === "/health") {
      this.json(res, 200, {
        ok: true,
        elapsedSeconds: this.host.elapsedSeconds(),
        farEndSpeaking: this.host.isFarEndSpeaking(),
        streamClients: this.clients.size,
      });
      return;
    }

    if (!this.authorized(req)) {
      this.json(res, 401, { error: "missing or invalid x-gvoice-token" });
      return;
    }

    if (path === "/events" && method === "GET") {
      this.sse(res);
      const client: Client = { id: randomUUID(), res };
      this.clients.add(client);
      this.log(`stream client connected (${this.clients.size} total)`);
      // Tell the agent the stream is live, so it can distinguish "connected" from
      // "connected but never going to send anything".
      res.write(
        `event: ready\ndata: ${JSON.stringify({ at: this.host.elapsedSeconds() })}\n\n`,
      );
      req.on("close", () => {
        this.clients.delete(client);
        this.log("stream client disconnected");
      });
      return;
    }

    if (method !== "POST") {
      this.json(res, 405, { error: `method ${method} not allowed on ${path}` });
      return;
    }

    switch (path) {
      case "/say": {
        const body = await this.readJson(req);
        const text = typeof body.text === "string" ? body.text.trim() : "";
        if (!text) {
          this.json(res, 400, { error: "expected {\"text\": \"...\"}" });
          return;
        }
        await this.host.say(text);
        this.json(res, 202, { accepted: true, chars: text.length });
        return;
      }
      case "/press": {
        const body = await this.readJson(req);
        const digit = typeof body.digit === "string" ? body.digit : "";
        if (!/^[0-9*#]$/.test(digit)) {
          this.json(res, 400, { error: 'expected {"digit": "0-9", "*" or "#"}' });
          return;
        }
        this.host.press(digit);
        this.json(res, 202, { accepted: true, digit });
        return;
      }
      case "/shutup": {
        this.host.shutUp();
        this.json(res, 202, { accepted: true });
        return;
      }
      case "/hangup": {
        this.json(res, 202, { accepted: true });
        this.opts.onHangup?.();
        return;
      }
      default:
        this.json(res, 404, { error: `no route ${path}` });
    }
  }

  /**
   * Wire transcript events from the call into the stream.
   * Returns an unsubscribe function.
   */
  subscribe(): () => void {
    return this.host.onTranscript((e) => this.broadcast(e));
  }

  get url(): string | undefined {
    return this.address ? `http://${this.address.host}:${this.address.port}` : undefined;
  }

  async close(): Promise<void> {
    this.closed = true;
    for (const client of this.clients) {
      try {
        client.res.end();
      } catch {
        /* already gone */
      }
    }
    this.clients.clear();
    const server = this.server;
    if (!server) return;
    await new Promise<void>((resolve) => server.close(() => resolve()));
    this.server = undefined;
  }
}

/** Minimal typed client for the control server, so agents are not hand-rolling fetch. */
export class ControlClient {
  private readonly base: string;
  private readonly token: string | undefined;

  constructor(base: string, token?: string) {
    this.base = base;
    this.token = token;
  }

  private headers(): Record<string, string> {
    return {
      "content-type": "application/json",
      ...(this.token ? { "x-gvoice-token": this.token } : {}),
    };
  }

  private async post(path: string, body: unknown): Promise<void> {
    const res = await fetch(`${this.base}${path}`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`${path} -> ${res.status} ${await res.text()}`);
  }

  say(text: string): Promise<void> {
    return this.post("/say", { text });
  }

  press(digit: string): Promise<void> {
    return this.post("/press", { digit });
  }

  shutUp(): Promise<void> {
    return this.post("/shutup", {});
  }

  hangup(): Promise<void> {
    return this.post("/hangup", {});
  }

  /** Consume the transcript stream. Yields events until `signal` aborts. */
  async *stream(signal?: AbortSignal): AsyncGenerator<TranscriptEvent | { type: "ready" }> {
    const res = await fetch(`${this.base}/events`, {
      headers: this.token ? { "x-gvoice-token": this.token } : {},
      ...(signal ? { signal } : {}),
    });
    if (!res.body) throw new Error("event stream had no body");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          let eventName = "message";
          const dataLines: string[] = [];
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          }
          if (!dataLines.length) continue;
          const payload = JSON.parse(dataLines.join("\n")) as unknown;
          if (eventName === "ready") yield { type: "ready" };
          else if (eventName === "transcript") yield payload as TranscriptEvent;
        }
      }
    } finally {
      reader.cancel().catch(() => {});
    }
  }
}