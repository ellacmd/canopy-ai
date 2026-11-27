import { ArrowDown, ArrowUpRight, History, MessageCircle, Phone, Plus, Send } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, describeError, jsonBody, streamTurn } from "@/lib/api";
import type { Conversation, Message, OrderDetail, Quote, TurnBody, TurnResult } from "@/lib/contracts";
import { dateTime, label, PAGE_SIZE, stageLabels } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import type { VoiceEvent } from "@/lib/voice";
import { QuoteReview, RefundForm } from "./refund";
import { Empty, ErrorNotice, Loading, PageHeading, Pager, Transcript } from "./ui";
import { VoiceCall } from "./voice-call";

const HISTORY_SIZE = 40;

type PendingTurn = { body: TurnBody; key: string };
type SupportMode = "call" | "chat";

export function Chat({ customerId, callActive, onCallActive, reviewOrder, onReviewOpened }: {
  customerId: string;
  callActive: boolean;
  onCallActive: (active: boolean) => void;
  reviewOrder?: OrderDetail;
  onReviewOpened: () => void;
}) {
  const [selected, setSelected] = useState<Conversation>();
  const [offset, setOffset] = useState(0);
  const [blocked, setBlocked] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const modeKey = `canopy:support-mode:${customerId}`;
  const [mode, setMode] = useState<SupportMode | undefined>(() => {
    try {
      const saved = window.sessionStorage.getItem(modeKey);
      return saved === "call" || saved === "chat" ? saved : undefined;
    } catch {
      return undefined;
    }
  });
  const [selectedCall, setSelectedCall] = useState<string>();
  const voice = useResource<{ enabled: boolean }>("/voice");
  const canCall = voice.resource.state === "ready" && voice.resource.data.enabled;
  const activeMode = callActive ? "call" : reviewOrder ? "chat" : mode ?? (canCall ? "call" : "chat");
  const threads = useResource<Conversation[]>(`/conversations?channel=chat&limit=${PAGE_SIZE}&offset=${offset}`);
  const calls = useResource<Conversation[]>(canCall ? "/conversations?channel=call&limit=10" : null);
  const recentCalls = calls.resource.state === "ready" ? calls.resource.data : [];
  const currentCall = selectedCall ?? recentCalls[0]?.id;
  const active = selected ?? (threads.resource.state === "ready" ? threads.resource.data[0] : undefined);
  // Keep the auto-selected thread stable as history is refreshed or paged.
  useEffect(() => {
    if (active && !selected) setSelected(active);
  }, [active, selected]);

  function selectMode(next: SupportMode) {
    setMode(next);
    try { window.sessionStorage.setItem(modeKey, next); } catch { /* Keep the in-memory preference. */ }
  }

  async function create() {
    if (creating || blocked) return;
    setCreateError("");
    setCreating(true);
    try {
      const thread = await api<{ id: string; status: string; created_at: string }>("/conversations", { method: "POST" });
      setSelected({ ...thread, updated_at: thread.created_at, primary_intent: null, channel: "chat" });
      setOffset(0);
      threads.reload();
      return thread.id;
    } catch (error) {
      setCreateError(describeError(error));
    } finally {
      setCreating(false);
    }
  }

  async function createCall() {
    const call = await api<{ id: string }>("/conversations", jsonBody({ channel: "call" }));
    setSelectedCall(call.id);
    calls.reload();
    return call.id;
  }

  function voiceEvent(event: VoiceEvent) {
    if (event.type === "ended" || event.type === "error" || event.type === "ticket") calls.reload();
  }

  return <>
    <PageHeading title="Customer support">Get help with orders, returns, and policy questions.</PageHeading>
    {canCall && <div className="support-mode" role="group" aria-label="Support channel">
      <button type="button" className="button" aria-pressed={activeMode === "call"} disabled={!!reviewOrder || blocked}
        onClick={() => selectMode("call")}><Phone size={16} aria-hidden /> Voice call</button>
      <button type="button" className="button" aria-pressed={activeMode === "chat"} disabled={callActive}
        onClick={() => selectMode("chat")}><MessageCircle size={16} aria-hidden /> Text chat</button>
    </div>}
    {canCall && activeMode === "call" ? <div className="call-layout">
      <VoiceCall disabled={blocked}
        conversationId={currentCall}
        prepare={createCall} onActive={onCallActive} onEvent={voiceEvent} />
      {!callActive && recentCalls.length > 0 && <section className="panel call-history" aria-label="Recent calls">
        <h2>Recent calls</h2>
        <ul className="record-list">
          {recentCalls.map((call, index) => <li key={call.id}>
            <button type="button" className="record-button" aria-pressed={call.id === currentCall}
              onClick={() => setSelectedCall(call.id)}>
              <span><strong>{index === 0 ? "Latest call" : "Support call"}</strong><small>{dateTime(call.updated_at)}</small></span>
            </button>
          </li>)}
        </ul>
      </section>}
    </div> : <>
    {createError && <ErrorNotice message={createError} />}
    <div className="chat-layout">
      <section className="panel history-panel" aria-labelledby="history-title">
        <div className="panel-header"><h2 id="history-title"><History size={17} aria-hidden /> History</h2></div>
        <button type="button" className="button secondary new-conversation" disabled={blocked || creating} onClick={() => void create()}>
          <Plus size={17} aria-hidden /> {creating ? "Starting…" : "New conversation"}
        </button>
        {threads.resource.state === "loading" && <Loading />}
        {threads.resource.state === "error" && <ErrorNotice message={threads.resource.error} retry={threads.reload} />}
        {threads.resource.state === "ready" && <>
          {!threads.resource.data.length && <p className="muted">No conversations on this page.</p>}
          <ul className="record-list history-list">
            {threads.resource.data.map((thread) => <li key={thread.id}>
              <button type="button" className="record-button" aria-pressed={active?.id === thread.id} disabled={blocked || creating} onClick={() => setSelected(thread)}>
                <span><strong>{thread.primary_intent ? label(thread.primary_intent) : "Support conversation"}</strong><small>{dateTime(thread.updated_at)}</small></span>
              </button>
            </li>)}
          </ul>
          <Pager count={threads.resource.data.length} offset={offset} onChange={setOffset} />
        </>}
      </section>
      <section className="panel chat-panel" aria-labelledby="conversation-title">
        <div className="panel-header"><h2 id="conversation-title"><MessageCircle size={18} aria-hidden /> Conversation</h2></div>
        {active ? <ConversationPanel key={active.id} conversation={active} reviewOrder={reviewOrder} onReviewOpened={() => {
          selectMode("chat");
          onReviewOpened();
        }} switching={creating} onBlocked={setBlocked} onUpdated={threads.reload} /> :
          <Empty title={reviewOrder ? `Start a conversation to review ${reviewOrder.order_number}` : "Start a support conversation"}>
            Select New conversation to begin.
          </Empty>}
      </section>
    </div>
    </>}
  </>;
}

function ConversationPanel({ conversation, reviewOrder, onReviewOpened, onBlocked, onUpdated, switching }: {
  conversation: Conversation;
  reviewOrder?: OrderDetail;
  onReviewOpened: () => void;
  onBlocked: (blocked: boolean) => void;
  onUpdated: () => void;
  switching: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [showRefund, setShowRefund] = useState(false);
  const [offset, setOffset] = useState(0);
  const [pending, setPending] = useState<PendingTurn>();
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [completed, setCompleted] = useState<TurnResult>();
  const request = useRef<AbortController | null>(null);
  const transcriptEnd = useRef<HTMLDivElement>(null);
  const path = `/conversations/${conversation.id}`;
  const history = useResource<Message[]>(`${path}/history?limit=${HISTORY_SIZE}&offset=${offset}`);
  const quote = useResource<Quote | null>(`${path}/quote`);
  const closed = conversation.status === "closed";
  const busy = !!pending || switching;
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (completed && history.resource.state === "ready" && offset === 0) {
      transcriptEnd.current?.scrollIntoView({ block: "nearest" });
    }
  }, [completed, history.resource, offset]);

  function refreshHistory() {
    setOffset(0);
    history.reload();
    quote.reload();
    onUpdated();
  }

  async function run(turn: PendingTurn) {
    if (request.current || closed) return;
    const controller = new AbortController();
    request.current = controller;
    setPending(turn);
    onBlocked(true);
    setError("");
    setStage("Sending request…");
    try {
      const result = await streamTurn(conversation.id, turn.body, turn.key, (name) =>
        setStage(stageLabels[name] ?? "Working on your request…"), controller.signal);
      if (controller.signal.aborted) return;
      setCompleted(result);
      setPending(undefined);
      onBlocked(false);
      refreshHistory();
    } catch (failure) {
      if (!controller.signal.aborted) setError(describeError(failure));
    } finally {
      if (!controller.signal.aborted) setStage("");
      request.current = null;
    }
  }

  function begin(body: TurnBody) {
    if (!busy) void run({ body, key: crypto.randomUUID() });
  }

  function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim() || busy) return;
    begin({ action: "message", message: draft.trim() });
    setDraft("");
  }

  const messages = history.resource.state === "ready" ? history.resource.data : [];
  const savedReply = completed && messages.some((message) => message.id === completed.message_id);
  const displayMessages = offset === 0 && completed && !savedReply
    ? [...messages, { id: completed.message_id, role: "assistant", content: completed.text,
      citations: completed.citations, created_at: new Date().toISOString() }]
    : messages;

  return <>
    <div className="conversation-stream">
      {history.resource.state === "loading" && <Loading>Loading conversation…</Loading>}
      {history.resource.state === "error" && <ErrorNotice message={history.resource.error} retry={history.reload} />}
      {history.resource.state === "ready" && !displayMessages.length && <Empty title="How can we help?">
        Ask about an order, a return, or an Amazora policy.
      </Empty>}
      {history.resource.state === "ready" && (offset > 0 || messages.length === HISTORY_SIZE) && <nav className="pager" aria-label="Conversation message pages">
        <button type="button" className="button secondary small" disabled={messages.length < HISTORY_SIZE || offset >= 10000} onClick={() => setOffset(offset + HISTORY_SIZE)}>Older messages</button>
        <button type="button" className="button secondary small" disabled={!offset} onClick={() => setOffset(offset - HISTORY_SIZE)}>Newer <ArrowDown size={14} aria-hidden /></button>
      </nav>}
      {displayMessages.length > 0 && <Transcript messages={displayMessages} />}
      <div ref={transcriptEnd} />
    </div>
    <div className="chat-controls">
      <div className="turn-status" role="status">
        {stage || (completed?.refund ? "Refund request submitted."
          : completed?.escalation ? "Your request was sent to the support team." : "")}
      </div>
      {error && pending && <div className="form-stack">
        <ErrorNotice message={error} />
        <div className="actions">
          <button type="button" className="button secondary" onClick={() => void run(pending)}>Retry request</button>
          <button type="button" className="button secondary" onClick={() => {
            if (pending.body.action === "message" && "message" in pending.body) setDraft(pending.body.message);
            setPending(undefined);
            setError("");
            onBlocked(false);
            refreshHistory();
          }}>Reload conversation</button>
        </div>
      </div>}
      {quote.resource.state === "error" && <ErrorNotice message={quote.resource.error} retry={quote.reload} />}
      {quote.resource.state === "ready" && quote.resource.data && <QuoteReview key={quote.resource.data.id}
        quote={quote.resource.data} disabled={busy || closed}
        onCancel={() => begin({ action: "cancel_refund" })}
        onConfirm={() => {
          if (quote.resource.state !== "ready" || !quote.resource.data) return;
          const current = quote.resource.data;
          begin({ action: "confirm_refund", confirmation: {
            quote_id: current.id, policy_version: current.policy_version, confirmed: true,
          } });
        }} />}
      {closed ? <p className="muted">This conversation is closed. Start a new conversation for more help.</p> : <>
        {(showRefund || reviewOrder) && !busy && <RefundForm key={reviewOrder?.id ?? "search"}
          initialOrder={reviewOrder} onPreview={(selection) => {
          begin({ action: "message", selection });
          setShowRefund(false);
          onReviewOpened();
        }} />}
        <div className="actions">
          <button type="button" className="button quiet small" disabled={busy} aria-expanded={showRefund || !!reviewOrder} onClick={() => {
            setShowRefund(!(showRefund || reviewOrder));
            onReviewOpened();
          }}>Review a refund</button>
          <button type="button" className="button quiet small" disabled={busy} onClick={() => begin({ action: "escalate" })}>
            Contact support <ArrowUpRight size={14} aria-hidden />
          </button>
        </div>
        <form className="composer" onSubmit={sendMessage}>
          <label className="sr-only" htmlFor="support-message">Message Canopy</label>
          <textarea id="support-message" rows={2} value={draft} maxLength={2000} onChange={(event) => setDraft(event.target.value)} placeholder="Ask about your order…" disabled={busy} />
          <button type="submit" className="button primary" disabled={busy || !draft.trim()}><Send size={17} aria-hidden /> Send</button>
        </form>
      </>}
    </div>
  </>;
}
