/**
 * WebRTC media plane for Google Voice calls, backed by werift (pure TypeScript).
 *
 * Google Voice's media is ordinary WebRTC — the answer SDP is `setup:passive`, ICE-lite,
 * with a directly routable candidate and opus/telephone-event. So the client only has to
 * act as the DTLS client and let werift do ICE, DTLS-SRTP and RTP.
 *
 * Two things matter for interop with GV specifically:
 *
 * 1. **Payload type must be 111 for opus.** werift's default audio offer only advertises
 *    PT 96/0, and Google's answer picks from what we offer (it answers 111 + 110), so the
 *    codec list has to be overridden or there is nothing to negotiate.
 * 2. **No candidate trickling.** Google is ICE-lite, so it learns our address from the STUN
 *    binding requests we send to its host candidate. The offer therefore goes out on port 9
 *    with no in-band candidates, which is also what the real Voice client does.
 *
 * Usage:
 *
 *   const media = await MediaPlane.create();
 *   await media.applyAnswer(answerSdpFrom183);
 *   media.onTrack((track) => …);   // inbound opus payloads
 *   media.sendOpus(Buffer);         // outbound opus frames
 */

import {
  MediaStreamTrack,
  MediaStreamTrackFactory,
  RTCPeerConnection,
  RTCRtpCodecParameters,
  RtpBuilder,
  type RtpPacket,
} from "werift";

/** Google Voice's opus payload type. Must match what we advertise. */
export const OPUS_PAYLOAD_TYPE = 111;
/** telephone-event, used for DTMF. */
export const TELEPHONE_EVENT_PAYLOAD_TYPE = 110;

export interface InboundStats {
  packets: number;
  bytes: number;
  /** Wall-clock ms since the first inbound packet. */
  elapsedMs: number;
}

export interface MediaPlaneOptions {
  /** Override the advertised codecs (defaults to opus/PT111 + telephone-event). */
  codecs?: RTCRtpCodecParameters[];
}

export class MediaPlane {
  readonly pc: RTCPeerConnection;
  /**
   * Outbound track. werift only populates `transceiver.sender.track` once a real track is
   * attached, so one is created up front — a bare `addTransceiver('audio')` leaves
   * `sender.track` undefined and there is nothing to write RTP to.
   */
  private outbound?: MediaStreamTrack;
  private inboundTrack?: MediaStreamTrack;
  private inboundPackets = 0;
  private inboundBytes = 0;
  private firstPacketAt?: number;
  private outboundPackets = 0;
  private outboundBytes = 0;
  /** SSRC used for outbound RTP. */
  private readonly ssrc = Math.floor(Math.random() * 0xffffffff) >>> 0;
  /** Timestamp step for a 20 ms opus frame at 48 kHz. */
  private readonly opusBuilder = new RtpBuilder({ between: 960, clockRate: 48000 });
  private readonly dtmfBuilder = new RtpBuilder({ between: 960, clockRate: 48000 });

  private constructor(pc: RTCPeerConnection) {
    this.pc = pc;
  }

  /** Build a peer connection whose offer matches what Google Voice expects. */
  static async create(opts: MediaPlaneOptions = {}): Promise<MediaPlane> {
    const codecs =
      opts.codecs ??
      [
        new RTCRtpCodecParameters({
          mimeType: "audio/opus",
          clockRate: 48000,
          channels: 2,
          payloadType: OPUS_PAYLOAD_TYPE,
        }),
        new RTCRtpCodecParameters({
          mimeType: "audio/telephone-event",
          clockRate: 48000,
          payloadType: TELEPHONE_EVENT_PAYLOAD_TYPE,
        }),
      ];

    // No ICE servers: Google's peer is directly routable, so only host candidates are needed.
    const pc = new RTCPeerConnection({ iceServers: [], codecs: { audio: codecs } });
    const plane = new MediaPlane(pc);
    const [track, , dispose] = await MediaStreamTrackFactory.rtpSource({ kind: "audio" });
    plane.outbound = track;
    plane.disposeOutbound = dispose;
    pc.addTransceiver(track, { direction: "sendrecv" });

    return plane;
  }

  /** Create the local offer and return the SDP to put in the SIP INVITE. */
  async createOffer(): Promise<string> {
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    return this.pc.localDescription?.sdp ?? "";
  }

  /** Apply the answer SDP carried on the 183 Session Progress. */
  async applyAnswer(sdp: string): Promise<void> {
    await this.pc.setRemoteDescription({ type: "answer", sdp });
  }

  /** Local DTLS fingerprint, which is what the offer's `a=fingerprint` advertises. */
  get fingerprint(): string | undefined {
    const transport = this.pc as unknown as {
      dtlsTransport?: { localCertificate?: { fingerprints?: Array<{ value?: string }> } };
    };
    return transport.dtlsTransport?.localCertificate?.fingerprints?.[0]?.value;
  }

  get connectionState(): string {
    return this.pc.connectionState;
  }

  get iceConnectionState(): string {
    return this.pc.iceConnectionState;
  }

  /** Resolves once ICE completes. Google is ICE-lite so this is quick. */
  waitForIce(timeoutMs = 20_000): Promise<void> {
    if (this.pc.iceConnectionState === "completed" || this.pc.iceConnectionState === "connected") {
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const sub = this.pc.iceConnectionStateChange.subscribe((state) => {
        if (state === "completed" || state === "connected") {
          clearTimeout(timer);
          sub.unSubscribe();
          resolve();
        } else if (state === "failed") {
          clearTimeout(timer);
          sub.unSubscribe();
          reject(new Error("ICE failed"));
        }
      });
      const timer = setTimeout(() => {
        sub.unSubscribe();
        reject(new Error(`ICE did not complete (state=${this.pc.iceConnectionState})`));
      }, timeoutMs);
    });
  }

  /**
   * Subscribe to inbound RTP.
   *
   * The callback receives raw opus payloads (20 ms frames), not PCM — decode with an opus
   * decoder of your choice if you need samples.
   */
  onTrack(cb: (payload: Buffer, meta: { payloadType: number; ssrc: number }) => void): void {
    this.pc.onTrack.subscribe((track: MediaStreamTrack) => {
      this.inboundTrack = track;
      track.onReceiveRtp.subscribe((rtp: RtpPacket) => {
        if (this.firstPacketAt === undefined) this.firstPacketAt = Date.now();
        this.inboundPackets += 1;
        this.inboundBytes += rtp.payload.length;
        cb(rtp.payload, { payloadType: rtp.header.payloadType, ssrc: rtp.header.ssrc });
      });
    });
  }

  /** Send one opus frame (20 ms). `frame` must be a complete opus packet. */
  sendOpus(frame: Buffer): void {
    const rtp = this.opusBuilder.create(frame);
    rtp.header.payloadType = OPUS_PAYLOAD_TYPE;
    rtp.header.ssrc = this.ssrc;
    this.outboundPackets += 1;
    this.outboundBytes += frame.length;
    this.outboundTrack().writeRtp(rtp);
  }

  /** Send a DTMF digit as RFC 4733 telephone-event. */
  sendDtmf(digit: string, durationMs = 100): void {
    const track = this.outboundTrack();
    const code = "0123456789*#ABCD".indexOf(digit.toUpperCase());
    if (code < 0) throw new Error(`unsupported DTMF digit: ${digit}`);
    // RFC 4733: volume 10 (0x0a), 160 ms nominal duration; final packet sets bit 7.
    const start = this.dtmfBuilder.create(Buffer.from([0x00, 0x8a, code, 0xa0]));
    start.header.payloadType = TELEPHONE_EVENT_PAYLOAD_TYPE;
    start.header.ssrc = this.ssrc;
    this.outboundPackets += 1;
    this.outboundBytes += 4;
    track.writeRtp(start);

    setTimeout(() => {
      const end = this.dtmfBuilder.create(Buffer.from([0x80, 0x8a, code, 0x00]));
      end.header.payloadType = TELEPHONE_EVENT_PAYLOAD_TYPE;
      end.header.ssrc = this.ssrc;
      track.writeRtp(end);
    }, durationMs);
  }

  /**
   * Low-level transport diagnostics. `connectionState` alone is ambiguous — this exposes
   * whether ICE actually carried bytes and whether DTLS/SRTP completed.
   */
  async diagnostics(): Promise<Record<string, unknown>> {
    // Note the accessors are plural: `dtlsTransports` / `iceTransports` are arrays.
    const dtls = this.pc.dtlsTransports?.[0] as unknown as {
      state?: string;
      srtp?: { keyLength?: number };
    } | undefined;
    const ice = this.pc.iceTransports?.[0] as unknown as {
      role?: string;
      gatheringState?: string;
      // Returns only the candidate pair, no byte counters.
      // Note: werift's Candidate exposes `host`, not `address`.
      getSelectedCandidatePair?: () => {
        local?: { host?: string; port?: number; type?: string };
        remote?: { host?: string; port?: number; type?: string };
      } | null;
    } | undefined;

    let selected: Record<string, unknown> | undefined;
    try {
      const pair = ice?.getSelectedCandidatePair?.();
      if (pair) {
        const addr = (c?: { host?: string; port?: number; type?: string }): string =>
          c?.host ? `${c.host}:${c.port} (${c.type ?? "?"})` : "(none)";
        selected = { local: addr(pair.local), remote: addr(pair.remote) };
      }
    } catch (err) {
      selected = { error: err instanceof Error ? err.message : String(err) };
    }

    return {
      connectionState: this.pc.connectionState,
      iceConnectionState: this.pc.iceConnectionState,
      iceGatheringState: ice?.gatheringState,
      iceRole: ice?.role,
      dtlsState: dtls?.state,
      srtpKeyLength: dtls?.srtp?.keyLength,
      selectedCandidatePair: selected,
      transceivers: this.pc.getTransceivers().map((t) => ({
        direction: t.direction,
        currentDirection: t.currentDirection,
        senderTrack: !!t.sender.track,
        receiverTracks: t.receiver.tracks.length,
      })),
    };
  }

  /** Timed stats, for verifying the media path actually carries audio. */
  stats(): { inbound: InboundStats; outboundPackets: number; outboundBytes: number; connection: string; ice: string } {
    return {
      inbound: {
        packets: this.inboundPackets,
        bytes: this.inboundBytes,
        elapsedMs: this.firstPacketAt ? Date.now() - this.firstPacketAt : 0,
      },
      outboundPackets: this.outboundPackets,
      outboundBytes: this.outboundBytes,
      connection: this.connectionState,
      ice: this.iceConnectionState,
    };
  }

  private outboundTrack(): MediaStreamTrack {
    const t = this.outbound ?? this.pc.getTransceivers()[0]?.sender?.track ?? this.inboundTrack;
    if (!t) throw new Error("no outbound track — the peer connection was not created via MediaPlane.create()");
    return t;
  }

  private disposeOutbound?: () => void;

  close(): void {
    this.disposeOutbound?.();
    this.pc.close();
  }
}