/** Shared contracts, kept in their own module so `agent.ts` does not depend on `stt.ts`. */

export interface Transcript {
  text: string;
  segments: Array<{ start: number; end: number; text: string }>;
}

/** Anything that can turn 48 kHz mono PCM into text. */
export interface SttBackend {
  readonly name: string;
  transcribe(pcm: Float32Array): Promise<Transcript>;
}
