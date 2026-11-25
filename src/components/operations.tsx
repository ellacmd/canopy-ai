import { useState, type FormEvent } from "react";
import { Activity, FileCheck2, Inbox, TriangleAlert } from "lucide-react";
import { api, describeError, jsonBody } from "@/lib/api";
import type { CaseContext, Execution, PolicyIndex, Principal, QueueMetrics, Refund, RefundContext, Ticket, TicketStatus } from "@/lib/contracts";
import { dateTime, label, money, PAGE_SIZE, stageLabels } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { Badge, Empty, ErrorNotice, Loading, OrderContext, PageHeading, Pager, Transcript } from "./ui";

const ticketStatuses: TicketStatus[] = ["open", "in_progress", "resolved", "closed"];
type StaffUpdate = Exclude<TicketStatus, "open">;
const statusChoices: Record<TicketStatus, StaffUpdate[]> = {
  open: ["in_progress", "resolved", "closed"],
  in_progress: ["in_progress", "resolved", "closed"],
  resolved: ["resolved", "closed"],
  closed: [],
};

export function StaffInbox({ principal }: { principal: Principal }) {
  const [filter, setFilter] = useState<TicketStatus | "all">("all");
  const [offset, setOffset] = useState(0);
  const [ticketId, setTicketId] = useState<string>();
  const tickets = useResource<Ticket[]>(`/admin/escalations?limit=${PAGE_SIZE}&offset=${offset}${filter === "all" ? "" : `&status=${filter}`}`);
  return <>
    <PageHeading title="Support inbox">Review customer escalations and update ticket status.</PageHeading>
    <div className="split">
      <section className="panel" aria-labelledby="ticket-list-title">
        <div className="panel-header"><h2 id="ticket-list-title">Tickets</h2><Inbox size={20} aria-hidden /></div>
        <label className="filter">Status<select value={filter} onChange={(event) => {
          setFilter(event.target.value as TicketStatus | "all");
          setOffset(0);
          setTicketId(undefined);
        }}>
          <option value="all">All statuses</option>
          {ticketStatuses.map((status) => <option key={status} value={status}>{label(status)}</option>)}
        </select></label>
        {tickets.resource.state === "loading" && <Loading />}
        {tickets.resource.state === "error" && <ErrorNotice message={tickets.resource.error} retry={tickets.reload} />}
        {tickets.resource.state === "ready" && <>
          {!tickets.resource.data.length && <Empty title="No matching tickets on this page" />}
          <ul className="record-list">
            {tickets.resource.data.map((ticket) => <li key={ticket.id}>
              <button type="button" className="record-button" aria-pressed={ticketId === ticket.id} onClick={() => setTicketId(ticket.id)}>
                <span><strong>{label(ticket.reason)}</strong><small>{dateTime(ticket.created_at)}</small></span><Badge value={ticket.status} />
              </button>
            </li>)}
          </ul>
          <Pager count={tickets.resource.data.length} offset={offset} onChange={setOffset} />
        </>}
      </section>
      <section className="panel" aria-labelledby="ticket-detail-title">
        <div className="panel-header"><h2 id="ticket-detail-title">Ticket details</h2></div>
        {ticketId ? <TicketDetail key={ticketId} id={ticketId} principal={principal} onUpdated={tickets.reload} />
          : <Empty title="Select a ticket">Customer, order, and conversation details appear here.</Empty>}
      </section>
    </div>
  </>;
}

function TicketDetail({ id, principal, onUpdated }: { id: string; principal: Principal; onUpdated: () => void }) {
  const { resource, reload } = useResource<CaseContext>(`/admin/escalations/${id}/context`);
  const [saved, setSaved] = useState<Ticket>();
  if (resource.state === "loading") return <Loading />;
  if (resource.state === "error") return <ErrorNotice message={resource.error} retry={reload} />;
  const { ticket, customer, order, messages, executions } = resource.data;
  const current = saved ?? ticket;
  return <div className="detail-stack">
    <div>
      <div className="row-between"><h3>{label(current.reason)}</h3><Badge value={current.status} /></div>
      <p className="prewrap">{current.summary}</p>
      <code className="identifier">{current.id}</code>
    </div>
    <dl className="facts">
      <div><dt>Customer</dt><dd>{customer.name}<br />{customer.email}</dd></div>
      <div><dt>Assignment</dt><dd>{current.assigned_agent_id === principal.id ? "Assigned to you" : current.assigned_agent_id ? "Assigned to another agent" : "Unassigned"}</dd></div>
      <div><dt>Updated</dt><dd>{dateTime(current.updated_at)}</dd></div>
    </dl>
    {saved && <p className="notice success" role="status">Ticket update saved.</p>}
    {current.resolution_note && <div><h3>Saved resolution note</h3><p className="prewrap">{current.resolution_note}</p></div>}
    {current.status !== "closed" && <TicketUpdate key={current.updated_at} ticket={current} onUpdated={(record) => {
      setSaved(record);
      onUpdated();
    }} />}
    <details><summary>Order context</summary>{order ? <OrderContext order={order} /> : <p className="muted">No order is attached to this ticket.</p>}</details>
    <details><summary>Recent conversation</summary>{messages.length ? <Transcript messages={messages} /> : <p className="muted">No conversation messages.</p>}</details>
    <details><summary>Execution timeline</summary><ExecutionTimeline executions={executions} /></details>
  </div>;
}

function TicketUpdate({ ticket, onUpdated }: { ticket: Ticket; onUpdated: (ticket: Ticket) => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const fields = new FormData(event.currentTarget);
    setError("");
    setPending(true);
    try {
      onUpdated(await api<Ticket>(`/admin/escalations/${ticket.id}`, jsonBody({
        status: fields.get("status"), assign_to_me: fields.has("assign"),
        resolution_note: String(fields.get("note")).trim(),
      }, "PATCH")));
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setPending(false);
    }
  }
  return <form className="form-stack inline-panel" onSubmit={submit}>
    <h3>Update ticket</h3>
    <label>New status<select name="status" disabled={pending}>{statusChoices[ticket.status].map((status) => <option key={status} value={status}>{label(status)}</option>)}</select></label>
    <label className="checkbox"><input type="checkbox" name="assign" disabled={pending} /> Assign to me</label>
    <label>Resolution note<textarea name="note" rows={3} maxLength={2000} disabled={pending} /></label>
    {error && <ErrorNotice message={error} />}
    <div><button type="submit" className="button primary" disabled={pending}>{pending ? "Saving…" : "Save ticket update"}</button></div>
  </form>;
}

export function StaffRefunds() {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string>();
  const refunds = useResource<Refund[]>(`/admin/refunds?limit=${PAGE_SIZE}&offset=${offset}`);
  const detail = useResource<RefundContext>(selected ? `/admin/refunds/${selected}/context` : null);
  return <>
    <PageHeading title="Refund review">Inspect simulated submissions, item amounts, and recorded confirmation.</PageHeading>
    <div className="split">
      <section className="panel" aria-labelledby="refund-list-title">
        <div className="panel-header"><h2 id="refund-list-title">Requests</h2><button type="button" className="button small secondary" onClick={refunds.reload}>Refresh</button></div>
        {refunds.resource.state === "loading" && <Loading />}
        {refunds.resource.state === "error" && <ErrorNotice message={refunds.resource.error} retry={refunds.reload} />}
        {refunds.resource.state === "ready" && <>
          {!refunds.resource.data.length && <Empty title="No requests on this page" />}
          <ul className="record-list">
            {refunds.resource.data.map((refund) => <li key={refund.id}>
              <button className="record-button" type="button" aria-pressed={selected === refund.id} onClick={() => setSelected(refund.id)}>
                <span><strong>{money(refund.amount, refund.currency)} · {label(refund.reason)}</strong><small>{dateTime(refund.created_at)}</small></span>
                <Badge value={refund.status} />
              </button>
            </li>)}
          </ul>
          <Pager count={refunds.resource.data.length} offset={offset} onChange={setOffset} />
        </>}
      </section>
      <section className="panel" aria-labelledby="refund-detail-title">
        <div className="panel-header"><h2 id="refund-detail-title">Refund details</h2></div>
        {!selected ? <Empty title="Select a refund request" />
          : detail.resource.state === "loading" ? <Loading />
          : detail.resource.state === "error" ? <ErrorNotice message={detail.resource.error} retry={detail.reload} />
          : <RefundDetail context={detail.resource.data} />}
      </section>
    </div>
  </>;
}

function RefundDetail({ context: { refund, customer, order } }: { context: RefundContext }) {
  return <div className="detail-stack">
    <div>
      <div className="row-between"><h3>{money(refund.amount, refund.currency)} · Simulated</h3><Badge value={refund.status} /></div>
      <code className="identifier">{refund.id}</code>
    </div>
    <dl className="facts">
      <div><dt>Customer</dt><dd>{customer.name}<br />{customer.email}</dd></div>
      <div><dt>Reason</dt><dd>{label(refund.reason)}</dd></div>
      <div><dt>Policy</dt><dd>{refund.policy_version}</dd></div>
      <div><dt>Confirmation</dt><dd>{refund.confirmed_at ? dateTime(refund.confirmed_at) : "No confirmation recorded"}</dd></div>
    </dl>
    <div>
      <h3>Refund items</h3>
      {refund.items.length ? <ul className="item-list">
        {refund.items.map((item) => <li key={item.order_item_id}>
          <span>{order.items.find((record) => record.order_item_id === item.order_item_id)?.product_name ?? item.order_item_id} ×{item.quantity}</span>
          <strong>{money(item.amount, refund.currency)}</strong>
        </li>)}
      </ul> : <p className="muted">No item allocations recorded.</p>}
    </div>
    <details><summary>Order context</summary><OrderContext order={order} /></details>
  </div>;
}

export function PolicyAdministration() {
  const { resource, reload } = useResource<PolicyIndex>("/policies/index");
  return <>
    <PageHeading title="Policy administration">Inspect the approved Amazora policy sources and their retrieval index.</PageHeading>
    <section className="panel" aria-label="Approved policy sources">
      <div className="panel-header"><h2>Approved sources</h2><button type="button" className="button secondary small" onClick={reload}>Refresh index status</button></div>
      {resource.state === "loading" && <Loading />}
      {resource.state === "error" && <ErrorNotice message={resource.error} retry={reload} />}
      {resource.state === "ready" && <>
        <p className="muted">Rules version {resource.data.rules_version}</p>
        <div className="policy-grid">
          {resource.data.sources.map((source) => <article className="inline-panel policy-card" key={source.source}>
            <div className="row-between"><FileCheck2 size={21} aria-hidden /><Badge value={source.status} /></div>
            <h3>{source.title}</h3>
            <p className="muted">Version {source.version} · {source.sections.length} sections</p>
            <details><summary>View approved source</summary>
              <p className="muted">SHA-256 <code className="identifier">{source.sha256}</code></p>
              {source.sections.map((section) => <details key={section.anchor}>
                <summary>{section.heading}</summary>
                <p className="prewrap">{section.text.split("\n\n").slice(2).join("\n\n")}</p>
              </details>)}
            </details>
          </article>)}
        </div>
      </>}
    </section>
  </>;
}

export function ExecutionTimeline({ executions }: { executions: Execution[] }) {
  if (!executions.length) return <Empty title="No executions on this page" />;
  return <ol className="timeline">
    {executions.map((execution) => <li key={execution.id}>
      <details>
        <summary><span>{dateTime(execution.created_at)}</span><Badge value={execution.status} /><span>{execution.latency_ms.toLocaleString()} ms</span></summary>
        <dl className="facts">
          <div><dt>Trace ID</dt><dd><code className="identifier">{execution.trace_id}</code></dd></div>
          <div><dt>Tokens</dt><dd>{execution.input_tokens.toLocaleString()} input · {execution.output_tokens.toLocaleString()} output</dd></div>
        </dl>
        {execution.stages.length ? <ol className="execution-stages">
          {execution.stages.map((stage, index) => <li key={`${stage}-${index}`}>{stageLabels[stage] ?? label(stage)}</li>)}
        </ol> : <p className="muted">No stages recorded.</p>}
      </details>
    </li>)}
  </ol>;
}

export function Dashboard() {
  const [offset, setOffset] = useState(0);
  const metrics = useResource<QueueMetrics>("/admin/queue-metrics");
  const executions = useResource<Execution[]>(`/admin/executions?limit=${PAGE_SIZE}&offset=${offset}`);
  return <>
    <PageHeading title="Operations">Monitor support queues and inspect workflow executions.</PageHeading>
    {metrics.resource.state === "loading" && <Loading>Loading metrics…</Loading>}
    {metrics.resource.state === "error" && <ErrorNotice message={metrics.resource.error} retry={metrics.reload} />}
    {metrics.resource.state === "ready" && <dl className="metric-grid">
      {([
        ["Active tickets", metrics.resource.data.active_tickets, Inbox],
        ["Submitted refunds", metrics.resource.data.submitted_refunds, FileCheck2],
        ["Recorded executions", metrics.resource.data.executions, Activity],
        ["Failed executions", metrics.resource.data.failed_executions, TriangleAlert],
      ] as const).map(([title, value, Icon]) => <div className="panel metric" key={title}>
        <Icon size={20} aria-hidden /><dt>{title}</dt><dd>{value.toLocaleString()}</dd>
      </div>)}
    </dl>}
    <section className="panel" aria-label="Workflow execution timeline">
      <div className="panel-header"><h2>Execution timeline</h2><button type="button" className="button small secondary" onClick={() => { metrics.reload(); executions.reload(); }}>Refresh</button></div>
      {executions.resource.state === "loading" && <Loading />}
      {executions.resource.state === "error" && <ErrorNotice message={executions.resource.error} retry={executions.reload} />}
      {executions.resource.state === "ready" && <>
        <ExecutionTimeline executions={executions.resource.data} />
        <Pager count={executions.resource.data.length} offset={offset} onChange={setOffset} />
      </>}
    </section>
  </>;
}
