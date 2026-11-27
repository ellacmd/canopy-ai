import { useState, type FormEvent } from "react";
import { Package, Search } from "lucide-react";
import { useResource } from "@/lib/use-resource";
import type { Order, OrderDetail, Refund } from "@/lib/contracts";
import { dateTime, label, money, PAGE_SIZE } from "@/lib/format";
import { Badge, Empty, ErrorNotice, Loading, OrderContext, PageHeading, Pager } from "./ui";

export function CustomerOrders({ onRefund }: { onRefund: (order: OrderDetail) => void }) {
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [detailPath, setDetailPath] = useState<string | null>(null);
  const orders = useResource<Order[]>(`/orders?limit=${PAGE_SIZE}&offset=${offset}`);
  const detail = useResource<OrderDetail>(detailPath);
  function lookup(event: FormEvent) {
    event.preventDefault();
    const number = query.trim();
    if (number) setDetailPath(`/orders/lookup/${encodeURIComponent(number)}`);
  }

  return <>
    <PageHeading title="My orders">Review purchases and check delivery or return options.</PageHeading>
    <div className="split">
      <section className="panel" aria-labelledby="recent-orders-title">
        <div className="panel-header"><h2 id="recent-orders-title">Recent orders</h2><Package size={20} aria-hidden /></div>
        <form onSubmit={lookup} className="search-form">
          <label>Order number<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="AMZ-1024" maxLength={40} required /></label>
          <button type="submit" className="button secondary"><Search size={17} aria-hidden /> Find order</button>
        </form>
        {orders.resource.state === "loading" && <Loading />}
        {orders.resource.state === "error" && <ErrorNotice message={orders.resource.error} retry={orders.reload} />}
        {orders.resource.state === "ready" && <>
          {orders.resource.data.length === 0 && <Empty title="No orders on this page" />}
          <ul className="record-list">
            {orders.resource.data.map((order) => <li key={order.id}>
              <button type="button" className="record-button" aria-pressed={detailPath === `/orders/${order.id}`} onClick={() => setDetailPath(`/orders/${order.id}`)}>
                <span><strong>{order.order_number}</strong><small>{dateTime(order.created_at)}</small></span>
                <span className="align-end"><Badge value={order.status} /><small>{money(order.total_amount, order.currency)}</small></span>
              </button>
            </li>)}
          </ul>
          <Pager count={orders.resource.data.length} offset={offset} onChange={setOffset} />
        </>}
      </section>
      <section className="panel" aria-labelledby="order-detail-title">
        <div className="panel-header"><h2 id="order-detail-title">Order details</h2></div>
        {!detailPath ? <Empty title="Choose an order">Select a recent order or use the order number.</Empty>
          : detail.resource.state === "loading" ? <Loading />
          : detail.resource.state === "error" ? <ErrorNotice message={detail.resource.error} retry={detail.reload} />
          : <>
            <OrderContext order={detail.resource.data} />
            <button type="button" className="button primary" onClick={() => {
              if (detail.resource.state === "ready") onRefund(detail.resource.data);
            }}>Review a refund</button>
          </>}
      </section>
    </div>
  </>;
}

export function CustomerRefunds() {
  const [offset, setOffset] = useState(0);
  const { resource, reload } = useResource<Refund[]>(`/refunds?limit=${PAGE_SIZE}&offset=${offset}`);
  return <>
    <PageHeading title="Refund requests">Track the refund requests you’ve submitted.</PageHeading>
    <section className="panel" aria-label="Your refund requests">
      <div className="panel-header"><h2>Your requests</h2><button className="button secondary small" type="button" onClick={reload}>Refresh</button></div>
      {resource.state === "loading" && <Loading />}
      {resource.state === "error" && <ErrorNotice message={resource.error} retry={reload} />}
      {resource.state === "ready" && <>
        {resource.data.length === 0 && <Empty title="No refund requests on this page" />}
        <ul className="record-list">
          {resource.data.map((refund) => <li key={refund.id} className="refund-history-row">
            <div className="row-between"><strong>{money(refund.amount, refund.currency)} · {label(refund.reason)}</strong><Badge value={refund.status} /></div>
            <p>Submitted {dateTime(refund.created_at)}</p>
          </li>)}
        </ul>
        <Pager count={resource.data.length} offset={offset} onChange={setOffset} />
      </>}
    </section>
  </>;
}
