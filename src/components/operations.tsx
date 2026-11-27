import { useState, type FormEvent } from "react";
import { Activity, FileCheck2, Inbox, TriangleAlert } from "lucide-react";
import { api, describeError, jsonBody } from "@/lib/api";
import type { CaseContext, Execution, Principal, QueueMetrics, Refund, RefundContext, Ticket, TicketStatus } from "@/lib/contracts";
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
    <PageHeading title="Support inbox">Follow up on requests that need a person.</PageHeading>
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
          {!tickets.resource.data.length && <Empty title="No tickets match this filter" />}
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
          : <Empty title="Choose a ticket to see what happened" />}
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
      <p className="prewrap">{current.summary === "The return policy requires a specialist to review this voice refund."
        ? "Canopy handed this return to your team during a call." : current.summary}</p>

    </div>
    <dl className="facts">
      <div><dt>Customer</dt><dd>{customer.name}<br />{customer.email}</dd></div>
      <div><dt>Assignment</dt><dd>{current.assigned_agent_id === principal.id ? "Assigned to you" : current.assigned_agent_id ? "Assigned to another agent" : "Unassigned"}</dd></div>
      <div><dt>Updated</dt><dd>{dateTime(current.updated_at)}</dd></div>
    </dl>
    {saved && <p className="notice success" role="status">Ticket update saved.</p>}
    {current.resolution_note && <div><h3>Resolution note</h3><p className="prewrap">{current.resolution_note}</p></div>}
    {current.status !== "closed" && <TicketUpdate key={current.updated_at} ticket={current} onUpdated={(record) => {
      setSaved(record);
      onUpdated();
    }} />}
    <details><summary>Order details</summary>{order ? <OrderContext order={order} /> : <p className="muted">This ticket has no linked order.</p>}</details>
    <details><summary>Conversation</summary>{messages.some((message) => message.role !== "system") ? <Transcript messages={messages.filter((message) => message.role !== "system")} /> : <p className="muted">There’s no conversation on this ticket yet.</p>}</details>
    <details><summary>Request activity</summary><ActivityTable executions={executions} /></details>
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
    <div><button type="submit" className="button primary" disabled={pending}>{pending ? "Saving…" : "Save changes"}</button></div>
  </form>;
}

export function StaffRefunds() {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string>();
  const refunds = useResource<Refund[]>(`/admin/refunds?limit=${PAGE_SIZE}&offset=${offset}`);
  const detail = useResource<RefundContext>(selected ? `/admin/refunds/${selected}/context` : null);
  return <>
    <PageHeading title="Refund review">See what customers asked to return and what they confirmed.</PageHeading>
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
      <div className="row-between"><h3>{money(refund.amount, refund.currency)} · Refund request</h3><Badge value={refund.status} /></div>

    </div>
    <dl className="facts">
      <div><dt>Customer</dt><dd>{customer.name}<br />{customer.email}</dd></div>
      <div><dt>Reason</dt><dd>{label(refund.reason)}</dd></div>
      <div><dt>Customer confirmed</dt><dd>{refund.confirmed_at ? dateTime(refund.confirmed_at) : "Confirmation unavailable"}</dd></div>
    </dl>
    <div>
      <h3>Refund items</h3>
      {refund.items.length ? <ul className="item-list">
        {refund.items.map((item) => <li key={item.order_item_id}>
          <span>{order.items.find((record) => record.order_item_id === item.order_item_id)?.product_name ?? "Item details unavailable"} ×{item.quantity}</span>
          <strong>{money(item.amount, refund.currency)}</strong>
        </li>)}
      </ul> : <p className="muted">Item details are unavailable for this refund.</p>}
    </div>
    <details><summary>Order details</summary><OrderContext order={order} /></details>
  </div>;
}

function ActivityTable({ executions }: { executions: Execution[] }) {
  if (!executions.length) return <Empty title="No request activity yet" />;
  return <div className="table-scroll" tabIndex={0} role="region" aria-label="Request activity table">
    <table className="activity-table" aria-label="Request activity">
      <thead><tr><th scope="col">Time</th><th scope="col">Outcome</th><th scope="col">Time taken</th><th scope="col">What happened</th></tr></thead>
      <tbody>{executions.map((execution) => <tr key={execution.id}>
        <td><time dateTime={execution.created_at}>{dateTime(execution.created_at)}</time></td>
        <td><Badge value={execution.status} /></td>
        <td>{execution.latency_ms < 1000 ? "Under a second" : `${(execution.latency_ms / 1000).toFixed(1)} sec`}</td>
        <td>{execution.stages.length ? <details>
          <summary>View steps</summary>
          <ol className="execution-stages">{execution.stages.map((stage, index) =>
            <li key={`${stage}-${index}`}>{stageLabels[stage] ?? "Processed the request"}</li>)}</ol>
        </details> : <span className="muted">Steps unavailable</span>}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

export function Dashboard() {
  const [offset, setOffset] = useState(0);
  const metrics = useResource<QueueMetrics>("/admin/queue-metrics");
  const executions = useResource<Execution[]>(`/admin/executions?limit=${PAGE_SIZE}&offset=${offset}`);
  return <>
    <PageHeading title="Operations">See how many requests are waiting and how Canopy handled them.</PageHeading>
    {metrics.resource.state === "loading" && <Loading>Loading metrics…</Loading>}
    {metrics.resource.state === "error" && <ErrorNotice message={metrics.resource.error} retry={metrics.reload} />}
    {metrics.resource.state === "ready" && <dl className="metric-grid">
      {([
        ["Tickets to follow up", metrics.resource.data.active_tickets, Inbox],
        ["Submitted refunds", metrics.resource.data.submitted_refunds, FileCheck2],
        ["Requests handled", metrics.resource.data.executions, Activity],
        ["Requests that couldn’t finish", metrics.resource.data.failed_executions, TriangleAlert],
      ] as const).map(([title, value, Icon]) => <div className="panel metric" key={title}>
        <Icon size={20} aria-hidden /><dt>{title}</dt><dd>{value.toLocaleString()}</dd>
      </div>)}
    </dl>}
    <section className="panel" aria-labelledby="request-activity-title">
      <div className="panel-header"><h2 id="request-activity-title">Request activity</h2><button type="button" className="button small secondary" onClick={() => { metrics.reload(); executions.reload(); }}>Refresh</button></div>
      {executions.resource.state === "loading" && <Loading />}
      {executions.resource.state === "error" && <ErrorNotice message={executions.resource.error} retry={executions.reload} />}
      {executions.resource.state === "ready" && <>
        <ActivityTable executions={executions.resource.data} />
        <Pager count={executions.resource.data.length} offset={offset} onChange={setOffset} />
      </>}
    </section>
  </>;
}
