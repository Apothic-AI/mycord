/** Shared contracts, kept in their own module so `agent.ts` does not depend on `stt.ts`. */

export interface Transcript {
  text: string;
  segments: Array<{ start: number; end: number; text: string }>;
}

/** Anything that can turn 48 kHz mono PCM into text. */
export interface SttBackend {
  readonly name: string;
  transcribe(pcm: Float32Array): Promise<Transcript>;
  /**
   * Best-effort transcription of speech that may still be in progress.
   *
   * Optional: backends that only handle settled utterances simply omit it, and the agent
   * emits no partials. This is what lets an external agent react to the first clause of a
   * sentence instead of waiting out the whole turn.
   */
  partial?(pcm: Float32Array): Promise<string>;
  /** Release any subprocess. */
  stop?(): Promise<void>;
}
