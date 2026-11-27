import { useEffect, useState, type FormEvent } from "react";
import type { Order, OrderDetail, Quote, RefundReason, Selection } from "@/lib/contracts";
import { dateTime, label, money } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { ErrorNotice, Loading } from "./ui";

const reasons: RefundReason[] = ["change_of_mind", "damaged", "wrong_item", "other"];

export function RefundForm({ initialOrder, initialOrderId, onPreview }: {
  initialOrder?: OrderDetail;
  initialOrderId?: string;
  onPreview: (selection: Selection) => void;
}) {
  const [number, setNumber] = useState(initialOrder?.order_number ?? "");
  const selectedId = initialOrder?.id ?? initialOrderId;
  const [orderPath, setOrderPath] = useState(selectedId ? `/orders/${selectedId}` : null);
  const orders = useResource<Order[]>(selectedId ? null : "/orders?limit=20");
  const order = useResource<OrderDetail>(orderPath);

  return <section className="inline-panel refund-form" aria-label="Refund selection">
    <h3>Choose items to review</h3>
    {!selectedId && <>
      {orders.resource.state === "loading" && <Loading>Loading recent orders…</Loading>}
      {orders.resource.state === "error" && <ErrorNotice message={orders.resource.error} retry={orders.reload} />}
      <form className="search-form" onSubmit={(event) => {
        event.preventDefault();
        if (number.trim()) setOrderPath(`/orders/lookup/${encodeURIComponent(number.trim())}`);
      }}>
        <label>Order number<input list="recent-refund-orders" value={number} onChange={(event) => setNumber(event.target.value)} maxLength={40} placeholder="AMZ-1024" required /></label>
        <datalist id="recent-refund-orders">
          {orders.resource.state === "ready" && orders.resource.data.map((entry) => <option key={entry.id} value={entry.order_number} />)}
        </datalist>
        <button type="submit" className="button secondary">Load order</button>
      </form>
    </>}
    {orderPath && (
      order.resource.state === "loading" ? <Loading>Loading order…</Loading>
      : order.resource.state === "error" ? <ErrorNotice message={order.resource.error} retry={order.reload} />
      : <ItemSelection key={order.resource.data.id} order={order.resource.data} onPreview={onPreview} />
    )}
  </section>;
}

function ItemSelection({ order, onPreview }: { order: OrderDetail; onPreview: (selection: Selection) => void }) {
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const available = order.items.filter((item) => item.remaining_refundable_quantity > 0);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const items = available.map((item) => ({
      order_item_id: item.order_item_id,
      quantity: Number(quantities[item.order_item_id] || 0),
    })).filter((item) => item.quantity > 0);
    if (!items.length || items.length > 50) {
      setError("Choose between 1 and 50 items to preview.");
      return;
    }
    onPreview({
      order_id: order.id, items, reason: fields.get("reason") as RefundReason,
      details: String(fields.get("details")).trim(),
    });
  }

  if (!available.length) return <p className="muted">No quantities are available to review. You can contact support about this order.</p>;

  return <form className="form-stack" onSubmit={submit}>
    <fieldset className="quantities">
      <legend>{order.order_number} · Quantities to return</legend>
      {available.map((item) => <label key={item.order_item_id}>
        <span>{item.product_name}<small>{item.remaining_refundable_quantity} available</small></span>
        <input type="number" min={0} max={Math.min(item.remaining_refundable_quantity, 10000)} step={1}
          value={quantities[item.order_item_id] ?? "0"} onChange={(event) => {
            const value = event.target.value;
            setQuantities((previous) => ({ ...previous, [item.order_item_id]: value }));
          }} />
      </label>)}
    </fieldset>
    <label>Return reason<select name="reason">{reasons.map((reason) => <option key={reason} value={reason}>{label(reason)}</option>)}</select></label>
    <label>Details (optional)<textarea name="details" maxLength={1000} rows={2} /></label>
    {error && <ErrorNotice message={error} />}
    <div><button type="submit" className="button primary">Preview refund</button></div>
  </form>;
}

export function QuoteReview({ quote, disabled, onConfirm, onCancel }: {
  quote: Quote;
  disabled: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const order = useResource<OrderDetail>(`/orders/${quote.selection.order_id}`);
  const expiresAt = Date.parse(quote.expires_at);
  const [expired, setExpired] = useState(() => expiresAt <= Date.now());
  useEffect(() => {
    const timer = window.setTimeout(() => setExpired(true), Math.max(0, expiresAt - Date.now()));
    return () => window.clearTimeout(timer);
  }, [expiresAt]);

  return <section className="quote inline-panel" aria-labelledby="quote-title">
    <div className="row-between"><h3 id="quote-title">Review refund preview</h3><strong className="quote-amount">{money(quote.amount, quote.currency)}</strong></div>
    <p>Check the items and amount, then confirm to submit your request.</p>
    {order.resource.state === "loading" && <Loading>Loading selected items…</Loading>}
    {order.resource.state === "error" && <ErrorNotice message={order.resource.error} retry={order.reload} />}
    {order.resource.state === "ready" && <ul className="item-list">
      {quote.selection.items.map((item) => {
        const detail = order.resource.state === "ready" && order.resource.data.items.find((line) => line.order_item_id === item.order_item_id);
        return <li key={item.order_item_id}><span>{detail ? detail.product_name : "Item details unavailable"}</span><strong>×{item.quantity}</strong></li>;
      })}
    </ul>}
    <dl className="facts">
      <div><dt>Reason</dt><dd>{label(quote.selection.reason)}</dd></div>
      <div><dt>Expires</dt><dd>{dateTime(quote.expires_at)}</dd></div>
    </dl>
    {expired && <p role="status">This preview expired. Request a new preview to confirm.</p>}
    <div className="actions">
      <button type="button" className="button primary" disabled={disabled || expired || order.resource.state !== "ready"} onClick={onConfirm}>Submit refund request</button>
      <button type="button" className="button secondary" disabled={disabled} onClick={onCancel}>Cancel preview</button>
    </div>
  </section>;
}
