import { Activity, AudioLines, Check, Clock3, Mic, MicOff, Phone, PhoneOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Citation, Message, Refund } from "@/lib/contracts";
import { dateTime, label, money, trackingNumber } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { DETECTION_FAILURE, VoiceConnection, type VoiceEvent, type VoiceOrder, type VoiceProgress, type VoiceRefundPreview, type VoiceStep } from "@/lib/voice";
import { QuoteReview } from "./refund";
import { ErrorNotice, Loading } from "./ui";

const steps: Record<VoiceStep, Record<VoiceProgress["state"], string>> = {
  orders: { working: "Finding your orders", done: "Orders found", unavailable: "Order list unavailable" },
  order: { working: "Looking up your order", done: "Order details found", unavailable: "Order lookup unavailable" },
  policy: { working: "Checking the support policy", done: "Policy checked", unavailable: "Policy lookup unavailable" },
  refund: { working: "Checking eligibility and filling your return", done: "Return reviewed", unavailable: "Return details need review" },
  handoff: { working: "Routing to a support specialist", done: "Support ticket created", unavailable: "Couldn’t route to support" },
  confirm_refund: { working: "Submitting your refund request", done: "Refund request submitted", unavailable: "Refund submission unavailable" },
  cancel_refund: { working: "Cancelling the preview", done: "Preview cancelled", unavailable: "Couldn’t cancel the preview" },
};

export function VoiceCall({ disabled, conversationId, prepare, onActive, onEvent }: {
  disabled: boolean;
  conversationId?: string;
  prepare: () => Promise<string | undefined>;
  onActive: (active: boolean) => void;
  onEvent: (event: VoiceEvent) => void;
}) {
  const connection = useRef<VoiceConnection | null>(null);
  const [callConversation, setCallConversation] = useState<string>();
  const [state, setState] = useState<"idle" | "connecting" | "connected" | "ending">("idle");
  const [speaking, setSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const [startedAt, setStartedAt] = useState<number>();
  const [seconds, setSeconds] = useState(0);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [actionPending, setActionPending] = useState(false);
  const [caption, setCaption] = useState({ customer: "", assistant: "" });
  const [order, setOrder] = useState<VoiceOrder>();
  const [evidence, setEvidence] = useState<{ citations: Citation[]; message: string }>();
  const [progress, setProgress] = useState<VoiceProgress[]>([]);
  const [preview, setPreview] = useState<VoiceRefundPreview>();
  const [receipt, setReceipt] = useState<Refund>();
  const [revision, setRevision] = useState(0);

  function end() {
    connection.current?.close();
    connection.current = null;
    setState("idle");
    setStartedAt(undefined);
    setMuted(false);
    setSpeaking(false);
    setActionPending(false);
    setCaption({ customer: "", assistant: "" });
    onActive(false);
  }

  useEffect(() => {
    function release() {
      connection.current?.close();
      connection.current = null;
      onActive(false);
    }
    window.addEventListener("pagehide", release);
    return () => {
      window.removeEventListener("pagehide", release);
      release();
    };
  }, [onActive]);

  useEffect(() => {
    if (startedAt === undefined) return;
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  function receive(event: VoiceEvent) {
    switch (event.type) {
      case "ready": setState("connected"); setStartedAt(Date.now()); break;
      case "end_after_reply": setState("ending"); break;
      case "caption": setCaption((current) => ({ ...current, [event.role]: event.text })); break;
      case "interrupted":
        setCaption((current) => ({ ...current, assistant: "" }));
        setProgress((current) => current.filter((step) => step.state !== "working"));
        break;
      case "turn_complete":
        setCaption({ customer: "", assistant: "" });
        if (event.saved) setRevision((value) => value + 1);
        break;
      case "progress":
        setProgress((current) => [...current.filter((step) => step.id !== event.id || step.step !== event.step), event].slice(-5));
        break;
      case "order": setOrder(event.order); break;
      case "policy": setEvidence(event); break;
      case "refund_preview": setPreview(event); setReceipt(undefined); setActionError(""); break;
      case "refund_invalidated": setPreview(undefined); setActionError(""); break;
      case "refund_error": setActionPending(false); setActionError(event.message); break;
      case "refund_result":
        setPreview(undefined);
        setActionPending(false);
        setReceipt(event.refund ?? undefined);
        setNotice(event.refund ? "Your refund request was submitted." : "Refund preview cancelled.");
        setRevision((value) => value + 1);
        break;
      case "ticket":
        setNotice(event.routed ? "Your return was sent to the support team." : "Support ticket created.");
        break;
      case "ended": end(); setNotice(event.message); setRevision((value) => value + 1); break;
      case "error": end(); setError(event.message); setRevision((value) => value + 1); break;
    }
    onEvent(event);
  }

  async function start() {
    if (connection.current || disabled) return;
    setError("");
    setNotice("");
    setCallConversation(undefined);
    setOrder(undefined);
    setEvidence(undefined);
    setProgress([]);
    setPreview(undefined);
    setReceipt(undefined);
    setActionError("");
    setActionPending(false);
    setSeconds(0);
    setRevision(0);
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext || !window.AudioWorkletNode) {
      setError("This browser cannot start a voice call. You can continue by typing.");
      return;
    }
    let active: VoiceConnection | undefined;
    try {
      active = new VoiceConnection(receive, setSpeaking);
      connection.current = active;
      onActive(true);
      setState("connecting");
      await active.start(async () => {
        const id = await prepare();
        if (!id) throw new Error("Conversation unavailable");
        if (connection.current === active) setCallConversation(id);
        return id;
      });
    } catch (failure) {
      if (active && connection.current !== active) return;
      end();
      if (failure instanceof DOMException && failure.name === "NotAllowedError") {
        setError("Allow microphone access in your browser to call Canopy.");
      } else {
        setError(failure instanceof Error && failure.message === DETECTION_FAILURE
          ? DETECTION_FAILURE : "The call could not start. You can try again or continue by typing.");
      }
    }
  }

  function act(action: "confirm_refund" | "cancel_refund") {
    if (!preview?.quote || actionPending) return;
    setActionError("");
    if (connection.current?.actOnRefund(action, preview.quote.id)) setActionPending(true);
  }

  const working = progress.findLast((item) => item.state === "working");
  const status = state === "connecting" ? "Connecting…" : state === "ending" ? "Ending call…" :
    state === "idle" ? "Voice support" : muted ? "Microphone muted" :
      speaking ? "Canopy is speaking" : working ? steps[working.step].working : "Listening";
  const active = state !== "idle";
  const transcriptId = active ? callConversation : conversationId;
  const showDetails = !!callConversation && callConversation === conversationId;
  const elapsed = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;

  return <section className="call-workspace" aria-label="Canopy voice support">
    <div className="voice-call">
      <div className="voice-call-head">
        <div className="voice-call-intro">
          <p className="call-channel"><span className={active ? "call-indicator live" : "call-indicator"} /> {active ? "Live call" : "Amazora support"}</p>
          <h2>Talk to Canopy</h2>
          {!active && <p>Ask about an order or return. Gemini processes audio and Canopy saves a call transcript.</p>}
        </div>
        {active && <span className="call-time" aria-label="Elapsed call time"><Clock3 size={15} aria-hidden /> {elapsed}</span>}
      </div>
      <div className={speaking ? "call-signal speaking" : "call-signal"} aria-hidden>
        <AudioLines size={38} />
      </div>
      <p className="voice-status" role="status">{status}</p>
      {active ? <div className="call-controls">
        <button type="button" className="button voice-mute" disabled={state !== "connected"}
          onClick={() => { connection.current?.setMuted(!muted); setMuted(!muted); }}>
          {muted ? <MicOff size={17} aria-hidden /> : <Mic size={17} aria-hidden />} {muted ? "Unmute" : "Mute"}
        </button>
        <button type="button" className="button voice-end" disabled={state === "ending"} onClick={() => {
          setState("ending");
          connection.current?.hangUp();
        }}><PhoneOff size={17} aria-hidden /> End call</button>
      </div> : <button type="button" className="button voice-start" disabled={disabled} onClick={() => void start()}>
        <Phone size={17} aria-hidden /> Start call
      </button>}
      {error && <ErrorNotice message={error} />}
    </div>

    {(active || showDetails) && (progress.length > 0 || order || evidence || preview || receipt || notice) &&
      <section className="panel call-actions-panel" aria-labelledby="call-actions-title">
        <div className="panel-header"><h2 id="call-actions-title"><Activity size={18} aria-hidden /> Call activity</h2></div>
        {progress.length > 0 && <ol className="call-progress" aria-label="Support actions" aria-live="polite">
          {progress.filter((item) => active || item.state !== "working").map((item) => <li key={`${item.id}-${item.step}`} data-state={item.state}>
            <span className="call-progress-mark" aria-hidden>{item.state === "done" ? <Check size={13} /> : "·"}</span>
            <span>{steps[item.step][item.state]}</span>
          </li>)}
        </ol>}
        {order && <section className="voice-detail" aria-label="Verified order">
          <strong>{order.order_number} · {label(order.status)}</strong>
          {order.shipment && <p>{order.shipment.carrier} · {trackingNumber(order.shipment.tracking_number)}</p>}
        </section>}
        {evidence && <section className="voice-detail" aria-label="Verified policy sources">
          <strong>Policy sources</strong>
          {!evidence.citations.length && <p>{evidence.message}</p>}
          {evidence.citations.map((item) => <details key={item.locator}>
            <summary>{item.title} · {item.section} · {item.source_version}</summary>
            <p className="prewrap">{item.excerpt}</p>
          </details>)}
        </section>}
        {preview?.quote && active && <QuoteReview key={preview.quote.id} quote={preview.quote}
          disabled={state !== "connected" || actionPending}
          onConfirm={() => act("confirm_refund")} onCancel={() => act("cancel_refund")} />}
        {preview && !preview.quote && <p className="voice-detail">{preview.message.replace(/^Here is what I found\.\s*/, "")}</p>}
        {actionError && <ErrorNotice message={actionError} />}
        {receipt && <section className="voice-detail call-receipt" aria-label="Refund receipt">
          <strong>{money(receipt.amount, receipt.currency)} · Refund request</strong>

        </section>}
        {notice && <p className="voice-notice" role="status">{notice}</p>}
      </section>}
    {transcriptId && <CallTranscript
      key={transcriptId} id={transcriptId}
      revision={revision} caption={caption} />}
  </section>;
}

function CallTranscript({ id, revision, caption }: {
  id: string;
  revision: number;
  caption: { customer: string; assistant: string };
}) {
  const history = useResource<Message[]>(`/conversations/${id}/history?limit=40`);
  const reload = history.reload;
  useEffect(() => { if (revision) reload(); }, [revision, reload]);

  return <details className="panel call-transcript">
    <summary>Call transcript</summary>
    {history.resource.state === "loading" && <Loading>Loading transcript…</Loading>}
    {history.resource.state === "error" && <ErrorNotice message={history.resource.error} retry={reload} />}
    {history.resource.state === "ready" && <ol aria-label="Call transcript entries">
      {history.resource.data.filter((message) => !message.workflow && (message.role === "assistant" ||
        (message.role === "customer" && !message.content.startsWith("Customer action: ")))).map((message) => <li key={message.id}>
        <span><strong>{message.role === "customer" ? "You" : message.role === "assistant" ? "Canopy" : label(message.role)}</strong>
          <time dateTime={message.created_at}>{dateTime(message.created_at)}</time></span>
        <p>{message.content}</p>
      </li>)}
    </ol>}
    {caption.customer && <p className="call-caption"><strong>You</strong> {caption.customer}</p>}
    {caption.assistant && <p className="call-caption"><strong>Canopy</strong> {caption.assistant}</p>}
  </details>;
}
