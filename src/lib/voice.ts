import type { Citation, Quote, Refund, RefundReason } from "./contracts";
import type { SpeechInput, SpeechOutput } from "./voice-speech.worker";
import { VoiceTurn } from "./voice-turn";

export type VoiceOrder = {
  order_number: string;
  status: string;
  shipment: { carrier: string; tracking_number: string } | null;
};

export type VoiceStep = "orders" | "order" | "policy" | "refund" | "handoff" | "confirm_refund" | "cancel_refund";
export type VoiceProgress = { type: "progress"; id: string; step: VoiceStep; state: "working" | "done" | "unavailable" };
export type VoiceRefundPreview = {
  type: "refund_preview"; id: string; order_number: string; reason: RefundReason;
  items: { name: string; quantity: number }[]; quote: Quote | null; message: string;
};

export type VoiceEvent =
  | { type: "ready" | "interrupted" | "end_after_reply" }
  | { type: "turn_complete"; saved: boolean }
  | { type: "caption"; role: "customer" | "assistant"; text: string }
  | { type: "order"; order: VoiceOrder }
  | { type: "policy"; citations: Citation[]; message: string }
  | VoiceProgress | VoiceRefundPreview
  | { type: "refund_result"; state: string; refund: Refund | null }
  | { type: "refund_invalidated" }
  | { type: "refund_error"; message: string }
  | { type: "ticket"; id: string; routed?: boolean }
  | { type: "error" | "ended"; message: string };

const CONNECTION_FAILURE = "The voice connection ended. You can call again or type.";
export const DETECTION_FAILURE = "Speech detection could not start. You can call again or type.";
const PRE_ROLL_FRAMES = 25;

export class VoiceConnection {
  private readonly context = new AudioContext();
  private readonly output = this.context.createGain();
  private readonly turn = new VoiceTurn();
  private stream?: MediaStream;
  private socket?: WebSocket;
  private microphone?: AudioWorkletNode;
  private input?: MediaStreamAudioSourceNode;
  private playback = new Set<AudioBufferSourceNode>();
  private playbackEnd = 0;
  private connectTimer?: number;
  private closed = false;
  private ready = false;
  private muted = false;
  private actionPending = false;
  private providerSpeaking = false;
  private discardReply = false;
  private closingAfterReply = false;
  private detector?: Worker;
  private finishDetectorStart?: (error?: Error) => void;
  private detectorEpoch = 0;
  private pendingFrames = 0;
  private preRoll: ArrayBuffer[] = [];
  private callerCaption = "";

  constructor(
    private onEvent: (event: VoiceEvent) => void,
    private onSpeaking: (speaking: boolean) => void,
  ) {
    this.output.connect(this.context.destination);
  }

  async start(prepare: () => Promise<string | undefined>) {
    await this.context.resume();
    if (this.closed) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
    });
    if (this.closed) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    this.stream = stream;
    await Promise.all([
      this.startSpeechDetector(),
      this.context.audioWorklet.addModule("/voice-microphone.js"),
    ]);
    if (this.closed) return;
    const conversationId = await prepare();
    if (this.closed || !conversationId) return;

    const url = new URL(`/api/conversations/${encodeURIComponent(conversationId)}/voice`, location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    socket.onmessage = ({ data }: MessageEvent<string | ArrayBuffer>) => {
      if (this.closed) return;
      if (typeof data === "string") {
        this.handleEvent(JSON.parse(data) as VoiceEvent);
      } else if (!this.discardReply && !this.turn.speaking) {
        this.providerSpeaking = true;
        this.play(data);
      } else if (this.turn.speaking) {
        // Speech began before the assistant's first audio arrived.
        this.discardReply = true;
      }
    };
    socket.onclose = () => this.fail(CONNECTION_FAILURE);

    const input = this.context.createMediaStreamSource(stream);
    const microphone = new AudioWorkletNode(this.context, "canopy-microphone", { numberOfOutputs: 0 });
    this.input = input;
    this.microphone = microphone;
    microphone.port.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
      if (!this.ready || this.closed || this.muted || this.actionPending || this.closingAfterReply || socket.readyState !== WebSocket.OPEN) return;
      if (socket.bufferedAmount > 32_000) {
        this.fail("The network is too slow for this call. You can call again or type.");
        return;
      }
      this.handleMicrophone(socket, data);
    };
    input.connect(microphone);
    stream.getAudioTracks().forEach((track) => {
      track.addEventListener("ended", () => this.fail("The microphone disconnected. You can call again or type."));
    });
    this.connectTimer = window.setTimeout(() => this.fail(CONNECTION_FAILURE), 12_000);
  }

  private startSpeechDetector(): Promise<void> {
    const detector = new Worker(new URL("./voice-speech.worker.ts", import.meta.url), { type: "module" });
    this.detector = detector;
    return new Promise((resolve, reject) => {
      const startup = window.setTimeout(() => this.finishDetectorStart?.(new Error(DETECTION_FAILURE)), 20_000);
      this.finishDetectorStart = (error) => {
        window.clearTimeout(startup);
        this.finishDetectorStart = undefined;
        if (error) reject(error);
        else resolve();
      };
      const fail = () => {
        if (this.finishDetectorStart) this.finishDetectorStart(new Error(DETECTION_FAILURE));
        else this.fail("Speech detection stopped. You can call again or type.");
      };
      detector.onerror = fail;
      detector.onmessage = ({ data }: MessageEvent<SpeechOutput>) => {
        if (data.type === "ready") this.finishDetectorStart?.();
        if (data.type === "error") fail();
        if (data.type === "score") {
          this.pendingFrames -= 1;
          if (data.epoch === this.detectorEpoch && data.probability !== undefined) {
            this.handleSpeechScore(data.probability);
          }
        }
      };
    });
  }

  private handleEvent(event: VoiceEvent) {
    if (event.type === "caption" && event.role === "assistant" &&
        (this.discardReply || this.turn.speaking)) return;
    if (event.type === "caption" && event.role === "customer" && this.turn.speaking) {
      this.callerCaption = event.text;
    }
    if (event.type === "ready") {
      this.ready = true;
      window.clearTimeout(this.connectTimer);
    } else if (event.type === "interrupted") {
      this.discardReply = true;
      this.clearPlayback();
    } else if (event.type === "turn_complete") {
      this.providerSpeaking = false;
      this.discardReply = false;
    } else if (event.type === "refund_result" || event.type === "refund_error") {
      this.actionPending = false;
    } else if (event.type === "end_after_reply") {
      this.closingAfterReply = true;
      this.resetMicrophone();
    } else if (event.type === "error" || event.type === "ended") {
      this.close();
    }
    this.onEvent(event);
    if (event.type === "end_after_reply" && !this.playback.size) this.hangUp();
  }

  actOnRefund(action: "confirm_refund" | "cancel_refund", quoteId: string) {
    if (!this.ready || this.closed || this.actionPending || this.socket?.readyState !== WebSocket.OPEN) return false;
    if (this.turn.speaking) {
      this.onEvent({ type: "refund_error", message: "Finish speaking before using the refund controls." });
      return false;
    }
    this.actionPending = true;
    this.resetMicrophone();
    this.discardReply = this.providerSpeaking;
    this.clearPlayback();
    this.socket.send(JSON.stringify({
      type: "refund_action", action, quote_id: quoteId,
      confirmed: action === "confirm_refund" ? true : undefined,
    }));
    return true;
  }

  private handleMicrophone(socket: WebSocket, audio: ArrayBuffer) {
    if (this.pendingFrames >= 15) {
      this.fail("Speech detection fell behind. You can call again or type.");
      return;
    }
    if (this.turn.speaking) {
      socket.send(audio);
    } else {
      this.preRoll.push(audio);
      if (this.preRoll.length > PRE_ROLL_FRAMES) this.preRoll.shift();
    }
    const input: SpeechInput = { audio, epoch: this.detectorEpoch };
    this.detector?.postMessage(input);
    this.pendingFrames += 1;
  }

  private handleSpeechScore(probability: number) {
    const socket = this.socket;
    if (!this.ready || this.closed || this.muted || this.actionPending || this.closingAfterReply || socket?.readyState !== WebSocket.OPEN) return;
    const assistantSpeaking = this.providerSpeaking || this.playback.size > 0;
    switch (this.turn.observe(probability, assistantSpeaking, this.callerCaption)) {
      case "duck": this.setDucked(true); break;
      case "resume": this.setDucked(false); break;
      case "start": this.beginSpeech(socket); break;
      case "end":
        socket.send("speech_end");
        this.resetMicrophone();
        break;
    }
  }

  private beginSpeech(socket: WebSocket) {
    if (this.providerSpeaking || this.playback.size > 0) {
      this.discardReply = this.providerSpeaking;
      this.clearPlayback();
      this.onEvent({ type: "interrupted" });
    }
    socket.send("speech_start");
    for (const frame of this.preRoll) socket.send(frame);
    this.preRoll = [];
  }

  private setDucked(duck: boolean) {
    this.output.gain.setTargetAtTime(duck ? 0.4 : 1, this.context.currentTime, 0.02);
  }

  private resetMicrophone() {
    this.detectorEpoch += 1;
    this.turn.reset();
    this.callerCaption = "";
    this.preRoll = [];
    this.setDucked(false);
  }

  private play(data: ArrayBuffer) {
    if (!data.byteLength || data.byteLength % 2) return;
    const currentTime = this.context.currentTime;
    if (this.playbackEnd - currentTime > 20) {
      this.fail("The audio fell behind. You can call again or type.");
      return;
    }
    const pcm = new DataView(data);
    const buffer = this.context.createBuffer(1, data.byteLength / 2, 24_000);
    const samples = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = pcm.getInt16(index * 2, true) / 32_768;
    }
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.output);
    if (this.playback.size === 0) this.onSpeaking(true);
    this.playback.add(source);
    source.onended = () => {
      this.playback.delete(source);
      source.disconnect();
      if (!this.playback.size && !this.closed) {
        this.onSpeaking(false);
        if (this.closingAfterReply) this.hangUp();
      }
    };
    const start = Math.max(currentTime + 0.025, this.playbackEnd);
    source.start(start);
    this.playbackEnd = start + buffer.duration;
  }

  private clearPlayback() {
    for (const source of this.playback) {
      source.onended = null;
      source.stop();
      source.disconnect();
    }
    this.playback.clear();
    this.playbackEnd = 0;
    this.setDucked(false);
    if (!this.closed) this.onSpeaking(false);
  }

  setMuted(muted: boolean) {
    if (this.closed || !this.ready || this.socket?.readyState !== WebSocket.OPEN) return;
    this.muted = muted;
    this.resetMicrophone();
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = !muted; });
    this.socket.send(muted ? "mute" : "unmute");
  }

  private fail(message: string) {
    if (this.closed) return;
    this.close();
    this.onEvent({ type: "error", message });
  }

  hangUp() {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      this.close();
      this.onEvent({ type: "ended", message: "Call ended." });
      return;
    }
    this.ready = false;
    this.discardReply = true;
    this.stream?.getAudioTracks().forEach((track) => track.stop());
    this.clearPlayback();
    this.socket.send("end");
    window.clearTimeout(this.connectTimer);
    this.connectTimer = window.setTimeout(() => {
      this.close();
      this.onEvent({ type: "ended", message: "Call ended." });
    }, 1500);
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    window.clearTimeout(this.connectTimer);
    this.clearPlayback();
    this.input?.disconnect();
    this.microphone?.disconnect();
    this.microphone?.port.close();
    this.finishDetectorStart?.(new Error(DETECTION_FAILURE));
    this.detector?.terminate();
    this.stream?.getTracks().forEach((track) => track.stop());
    this.socket?.close();
    void this.context.close().catch(() => undefined);
  }
}
