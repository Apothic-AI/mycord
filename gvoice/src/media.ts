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

/**
 * Ensure the remote SDP carries at least one `a=ssrc:` line per media section.
 *
 * werift creates a receiver track only when it can key it by an SSRC; Google Voice's
 * answer omits SSRC entirely, so without this werift accepts the answer, reports
 * `receiver tracks: 0`, and silently discards all inbound RTP. The injected value is a
 * placeholder — the real SSRC is discovered at runtime by `wireWildcardReceive`, which
 * registers a track with no SSRC so werift stops filtering.
 */
export function ensureSsrcLines(sdp: string, placeholderSsrc = 1): string {
  const lines = sdp.replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  let inMedia = false;

  for (const line of lines) {
    if (line.startsWith("m=")) inMedia = true;
    if (inMedia && line.trim() === "") {
      // End of the media section: add the placeholder if the section had no SSRC.
      out.push(`a=ssrc:${placeholderSsrc} cname:gv-placeholder`);
      out.push("");
      inMedia = false;
      continue;
    }
    out.push(line);
  }
  // SDP without a trailing blank line still needs the attribute.
  if (inMedia) out.push(`a=ssrc:${placeholderSsrc} cname:gv-placeholder`);
  return out.join("\r\n");
}

/**
 * Remove IPv6 candidates from an answer so both agents settle on one address family.
 *
 * See MediaPlaneOptions.useIpv6 for why this is needed: the two sides otherwise disagree
 * about which candidate pair carries media.
 */
export function dropIPv6Candidates(sdp: string): string {
  const isV6 = (line: string): boolean => {
    if (!line.startsWith("a=candidate:")) return false;
    const parts = line.slice("a=candidate:".length).trim().split(/\s+/);
    // a=candidate:<foundation> <component> <transport> <priority> <address> <port> ...
    const addr = parts[4] ?? "";
    return addr.includes(":");
  };
  return sdp
    .split(/\r?\n/)
    .filter((line) => !isV6(line))
    .join("\r\n");
}

export interface InboundStats {
  packets: number;
  bytes: number;
  /** Wall-clock ms since the first inbound packet. */
  elapsedMs: number;
}

export interface MediaPlaneOptions {
  /** Override the advertised codecs (defaults to opus/PT111 + telephone-event). */
  codecs?: RTCRtpCodecParameters[];
  /**
   * Address family to use. Defaults to IPv4 only.
   *
   * Google Voice's answer offers both an IPv4 and an IPv6 candidate. werift was gathering
   * both, accepting an IPv4 pair, and reporting DTLS connected — while a packet capture
   * showed 1615 of 1623 inbound media packets arriving over IPv6 and only 8 (the DTLS
   * handshake) over IPv4. The two sides disagreed about which pair carried media, so werift
   * decrypted nothing. Pinning one family on both sides removes the ambiguity.
   */
  useIpv6?: boolean;
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
    const useIpv6 = opts.useIpv6 ?? false;
    const pc = new RTCPeerConnection({
      iceServers: [],
      codecs: { audio: codecs },
      ...({ ice: { useIpv4: !useIpv6, useIpv6 } } as Record<string, unknown>),
    });
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

  /**
   * Apply the answer SDP carried on the 183 Session Progress.
   *
   * Throws with context on failure — a malformed or unusable answer is the single most
   * likely reason a call connects but never carries audio, so do not swallow it.
   */
  async applyAnswer(sdp: string): Promise<void> {
    try {
      // werift only creates a receiver track when the remote answer carries at least one
      // `a=ssrc:` line. Google Voice's answer is minimal and omits them, so werift
      // silently drops every inbound RTP packet. Inject a placeholder SSRC so the track
      // exists, then swap in a wildcard track that accepts any SSRC (werift filters
      // inbound RTP by the SSRC named in the answer, and we cannot know Google's).
      await this.pc.setRemoteDescription({
        type: "answer",
        sdp: ensureSsrcLines(dropIPv6Candidates(sdp)),
      });
    } catch (err) {
      throw new Error(
        `setRemoteDescription(answer) failed: ${err instanceof Error ? err.message : String(err)}\n` +
          `--- answer ---\n${sdp.slice(0, 900)}`,
      );
    }
    if (this.wireWildcardReceive()) return;

    throw new Error(
      "answer applied but the transceiver has no receiver track; inbound RTP will be dropped.\n" +
        `signallingState=${this.pc.signalingState}\n--- answer ---\n${sdp.slice(0, 900)}`,
    );
  }

  /**
   * Replace the SSRC-filtered receiver track with a wildcard one.
   *
   * werift routes inbound RTP to a receiver track keyed by the SSRC from the answer. Since
   * Google's answer names none, the injected placeholder would filter out every real
   * packet. A track with `ssrc` left undefined accepts any, so register one of those.
   *
   * @returns true when a wildcard track is now receiving.
   */
  private wireWildcardReceive(): boolean {
    const receiver = this.pc.getTransceivers()[0]?.receiver;
    if (!receiver) return false;

    const existing = receiver.tracks[0];
    if (existing && existing.ssrc === undefined) {
      this.inboundTrack = existing;
      return true;
    }

    const wildcard = new MediaStreamTrack({ kind: "audio" });
    const added = receiver.addTrack(wildcard);
    if (!added) return false;

    this.inboundTrack = wildcard;
    this.learnInboundSsrc(receiver, wildcard);
    this.attachInbound();
    return true;
  }

  /**
   * Teach werift the peer's SSRC from the first packet that arrives.
   *
   * `RtpReceiver.handleRtpBySsrc` does a bare `trackBySSRC[packet.header.ssrc]` lookup and
   * passes `undefined` on when there is no entry, which silently discards the packet. Our
   * SSRC is only ever published in the answer's `a=ssrc:` lines, and Google Voice's answer
   * carries none, so the key can never be pre-seeded. Wrapping the handler lets the first
   * inbound packet register its own SSRC against our wildcard track.
   */
  private learnInboundSsrc(receiver: unknown, wildcard: MediaStreamTrack): void {
    const r = receiver as {
      trackBySSRC?: Record<string, MediaStreamTrack>;
      handleRtpBySsrc?: (packet: { header: { ssrc: number } }, ext?: unknown) => void;
    };
    const map = r.trackBySSRC;
    if (!map || typeof r.handleRtpBySsrc !== "function") return;

    const original = r.handleRtpBySsrc.bind(receiver) as (
      packet: { header: { ssrc: number } },
      ext?: unknown,
    ) => void;

    r.handleRtpBySsrc = (packet, ext) => {
      const ssrc = packet.header.ssrc;
      if (!map[ssrc]) {
        map[ssrc] = wildcard;
        wildcard.ssrc = ssrc;
        this.learnedSsrc = ssrc;
      }
      original(packet, ext);
    };
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

  /** True once the DTLS handshake has produced SRTP keys. */
  get dtlsConnected(): boolean {
    return this.pc.dtlsTransports?.[0]?.state === "connected";
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
    this.rxSubscribers.push(cb);
    this.attachInbound();
  }

  private readonly rxSubscribers: Array<(payload: Buffer, meta: { payloadType: number; ssrc: number }) => void> = [];
  private inboundAttached = false;
  /** SSRC learned from the first inbound packet, if any. */
  learnedSsrc?: number;

  /**
   * Subscribe to whichever track actually receives.
   *
   * Prefer the wildcard track installed by `wireWildcardReceive` when present — werift's
   * own auto-created track is keyed by the placeholder SSRC and would filter out every
   * real packet. Falling back to `pc.onTrack` keeps this working for peers that do send
   * proper SSRC lines.
   */
  private attachInbound(): void {
    if (this.inboundAttached || this.rxSubscribers.length === 0) return;

    const wildcard = this.inboundTrack;
    if (wildcard) {
      wildcard.onReceiveRtp.subscribe((rtp: RtpPacket) => this.handleInboundRtp(rtp));
      this.inboundAttached = true;
      return;
    }

    this.pc.onTrack.subscribe((track: MediaStreamTrack) => {
      this.inboundTrack ??= track;
      track.onReceiveRtp.subscribe((rtp: RtpPacket) => this.handleInboundRtp(rtp));
      this.inboundAttached = true;
    });
  }

  private handleInboundRtp(rtp: RtpPacket): void {
    if (this.firstPacketAt === undefined) this.firstPacketAt = Date.now();
    this.inboundPackets += 1;
    this.inboundBytes += rtp.payload.length;
    const meta = { payloadType: rtp.header.payloadType, ssrc: rtp.header.ssrc };
    for (const cb of this.rxSubscribers) cb(rtp.payload, meta);
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
      cipherSuite?: { name?: string };
      srtpProfiles?: number[];
      srtp?: { keyLength?: number; profile?: { name?: string } };
      localKeyPair?: unknown;
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
      dtlsCipherSuite: dtls?.cipherSuite?.name ?? null,
      negotiatedSrtpProfiles: dtls?.srtpProfiles ?? null,
      srtpKeyLength: dtls?.srtp?.keyLength ?? null,
      srtpProfileName: dtls?.srtp?.profile?.name ?? null,
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