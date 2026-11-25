import { ArrowDown, ArrowUpRight, History, MessageCircle, Plus, Send } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, describeError, streamTurn } from "@/lib/api";
import type { Conversation, Message, OrderDetail, Quote, TurnBody, TurnResult } from "@/lib/contracts";
import { dateTime, label, PAGE_SIZE, stageLabels } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { QuoteReview, RefundForm } from "./refund";
import { Empty, ErrorNotice, Loading, PageHeading, Pager, Transcript } from "./ui";

const HISTORY_SIZE = 40;

type PendingTurn = { body: TurnBody; key: string };

export function Chat({ reviewOrder, onReviewOpened }: {
  reviewOrder?: OrderDetail;
  onReviewOpened: () => void;
}) {
  const [selected, setSelected] = useState<Conversation>();
  const [offset, setOffset] = useState(0);
  const [blocked, setBlocked] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const threads = useResource<Conversation[]>(`/conversations?limit=${PAGE_SIZE}&offset=${offset}`);
  const active = selected ?? (threads.resource.state === "ready" ? threads.resource.data[0] : undefined);
  // Keep the auto-selected thread stable as history is refreshed or paged.
  useEffect(() => {
    if (active && !selected) setSelected(active);
  }, [active, selected]);

  async function create() {
    if (creating || blocked) return;
    setCreateError("");
    setCreating(true);
    try {
      const thread = await api<{ id: string; status: string; created_at: string }>("/conversations", { method: "POST" });
      setSelected({ ...thread, updated_at: thread.created_at, primary_intent: null });
      setOffset(0);
      threads.reload();
    } catch (error) {
      setCreateError(describeError(error));
    } finally {
      setCreating(false);
    }
  }

  return <>
    <PageHeading title="Support chat">Get help with orders, returns, and policy questions.</PageHeading>
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
        {active ? <ConversationPanel key={active.id} conversation={active} reviewOrder={reviewOrder} onReviewOpened={onReviewOpened}
          onBlocked={setBlocked} onUpdated={threads.reload} /> :
          <Empty title={reviewOrder ? `Start a conversation to review ${reviewOrder.order_number}` : "Start a support conversation"}>
            Select New conversation to begin.
          </Empty>}
      </section>
    </div>
  </>;
}

function ConversationPanel({ conversation, reviewOrder, onReviewOpened, onBlocked, onUpdated }: {
  conversation: Conversation;
  reviewOrder?: OrderDetail;
  onReviewOpened: () => void;
  onBlocked: (blocked: boolean) => void;
  onUpdated: () => void;
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
    if (!pending) void run({ body, key: crypto.randomUUID() });
  }

  function sendMessage(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim() || pending) return;
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
        {stage || (completed?.refund ? `Simulated refund submitted. Reference ${completed.refund.id}.`
          : completed?.escalation ? `Support ticket created. Reference ${completed.escalation.id}.` : "")}
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
        quote={quote.resource.data} disabled={!!pending || closed}
        onCancel={() => begin({ action: "cancel_refund" })}
        onConfirm={() => {
          if (quote.resource.state !== "ready" || !quote.resource.data) return;
          const current = quote.resource.data;
          begin({ action: "confirm_refund", confirmation: {
            quote_id: current.id, policy_version: current.policy_version, confirmed: true,
          } });
        }} />}
      {closed ? <p className="muted">This conversation is closed. Start a new conversation for more help.</p> : <>
        {(showRefund || reviewOrder) && !pending && <RefundForm key={reviewOrder?.id ?? "search"} initialOrder={reviewOrder} onPreview={(selection) => {
          begin({ action: "message", selection });
          setShowRefund(false);
          onReviewOpened();
        }} />}
        <div className="actions">
          <button type="button" className="button quiet small" disabled={!!pending} aria-expanded={showRefund || !!reviewOrder} onClick={() => {
            setShowRefund(!(showRefund || reviewOrder));
            onReviewOpened();
          }}>Review a refund</button>
          <button type="button" className="button quiet small" disabled={!!pending} onClick={() => begin({ action: "escalate" })}>
            Contact support <ArrowUpRight size={14} aria-hidden />
          </button>
        </div>
        <form className="composer" onSubmit={sendMessage}>
          <label className="sr-only" htmlFor="support-message">Message Canopy</label>
          <textarea id="support-message" rows={2} value={draft} maxLength={2000} onChange={(event) => setDraft(event.target.value)} placeholder="Ask about your order…" disabled={!!pending} />
          <button type="submit" className="button primary" disabled={!!pending || !draft.trim()}><Send size={17} aria-hidden /> Send</button>
        </form>
      </>}
    </div>
  </>;
}
