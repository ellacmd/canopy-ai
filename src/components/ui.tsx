import { AlertCircle, ArrowLeft, ArrowRight, Layers3, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { Message, OrderDetail } from "@/lib/contracts";
import { dateTime, label, money, PAGE_SIZE, trackingNumber } from "@/lib/format";

export function Brand() {
  return <div className="brand"><span className="brand-mark"><Layers3 size={21} aria-hidden /></span> Canopy<span className="brand-ai">AI</span></div>;
}

export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return <p className="loading" role="status"><LoaderCircle size={17} aria-hidden />{children}</p>;
}

export function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="notice error" role="alert">
    <AlertCircle size={18} aria-hidden /><span>{message}</span>
    {retry && <button type="button" className="button small secondary" onClick={retry}>Try again</button>}
  </div>;
}

export function Badge({ value }: { value: string }) {
  const tone = ["delivered", "paid", "ready", "resolved", "succeeded"].includes(value)
    ? "positive" : ["failed", "needs_review", "missing"].includes(value)
      ? "negative" : ["open", "in_progress", "submitted", "escalated"].includes(value)
        ? "pending" : "neutral";
  return <span className={`badge ${tone}`}>{label(value)}</span>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

export function Pager({ offset, count, onChange }: {
  offset: number;
  count: number;
  onChange: (offset: number) => void;
}) {
  if (offset === 0 && count < PAGE_SIZE) return null;
  return <nav className="pager" aria-label="Results pages">
    <button type="button" className="button secondary small" disabled={!offset} onClick={() => onChange(offset - PAGE_SIZE)}>
      <ArrowLeft size={15} aria-hidden /> Previous
    </button>
    <span>Page {1 + offset / PAGE_SIZE}</span>
    <button type="button" className="button secondary small" disabled={count < PAGE_SIZE || offset >= 10000} onClick={() => onChange(offset + PAGE_SIZE)}>
      Next <ArrowRight size={15} aria-hidden />
    </button>
  </nav>;
}

export function PageHeading({ title, children }: { title: string; children?: ReactNode }) {
  return <header className="page-heading"><h1 tabIndex={-1}>{title}</h1>{children && <p>{children}</p>}</header>;
}

export function OrderContext({ order }: { order: OrderDetail }) {
  return <div className="order-context">
    <div className="row-between"><strong>{order.order_number}</strong><Badge value={order.status} /></div>
    <dl className="facts">
      <div><dt>Order total</dt><dd>{money(order.total_amount, order.currency)}</dd></div>
      <div><dt>Payment</dt><dd>{label(order.payment_status)}</dd></div>
      {order.delivered_at && <div><dt>Delivered</dt><dd>{dateTime(order.delivered_at)}</dd></div>}
    </dl>
    <ul className="item-list">
      {order.items.map((item) => <li key={item.order_item_id}><span>{item.product_name}</span><strong>×{item.quantity}</strong></li>)}
    </ul>
    {order.shipment ? <div className="shipment">
      <strong>{order.shipment.carrier} · {label(order.shipment.status)}</strong>
      <span className="identifier">{trackingNumber(order.shipment.tracking_number)}</span>
      {order.shipment.estimated_delivery && order.status !== "delivered" && <span>Estimated {dateTime(order.shipment.estimated_delivery)}</span>}
    </div> : <p className="muted">Tracking details are unavailable.</p>}
  </div>;
}

const ACTION_LABELS: Record<string, string> = {
  "Customer action: message": "Requested a refund preview.",
  "Customer action: confirm_refund": "Confirmed the refund preview.",
  "Customer action: cancel_refund": "Cancelled the refund preview.",
  "Customer action: escalate": "Requested human support.",
};

export function Transcript({ messages }: { messages: Message[] }) {
  return <ol className="transcript" aria-label="Conversation messages">
    {messages.map((message) => <li key={message.id} className={`message ${message.role === "customer" ? "from-customer" : ""}`}>
      <div className="message-meta">
        <strong>{message.role === "assistant" ? "Canopy" : label(message.role)}</strong>
        <time dateTime={message.created_at}>{dateTime(message.created_at)}</time>
      </div>
      <div className="message-body">{ACTION_LABELS[message.content] ?? message.content}</div>
      {message.citations.length > 0 && <div className="citations">
        <strong>Policy sources</strong>
        {message.citations.map((citation) => <details key={citation.locator}>
          <summary>{citation.title} · {citation.section}</summary>
          <p className="prewrap">{citation.excerpt}</p>
        </details>)}
      </div>}
    </li>)}
  </ol>;
}
