import { useState, type FormEvent } from "react";
import { api, describeError, jsonBody } from "@/lib/api";
import type { PolicyIndex, Principal, PublishedPolicy } from "@/lib/contracts";
import { dateTime, money } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import { Empty, ErrorNotice, Loading, PageHeading } from "./ui";

const headings: Record<string, string> = {
  "Damage and exceptions": "Damaged items and unusual returns",
  "Discounts, tax, and shipping allocations": "Discounts, tax, and shipping",
  "Exact paid amounts": "How refund amounts are calculated",
  "Partial quantities and reservations": "Returning part of an order",
  "Explicit confirmation": "How customers confirm a request",
  "Simulation and physical returns": "What happens after submission",
  "Human support and policy authority": "When to ask support for help",
};

export function PolicyAdministration({ role }: { role: Principal["role"] }) {
  const { resource, reload } = useResource<PublishedPolicy>("/policies/returns");
  const sources = useResource<PolicyIndex>("/policies/index");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<PublishedPolicy>();
  const history = useResource<PublishedPolicy[]>(role === "admin" ? "/policies/returns/history" : null);
  const fetched = resource.state === "ready" ? resource.data : undefined;
  const policy = saved && (!fetched || saved.revision >= fetched.revision) ? saved : fetched;

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policy || saving) return;
    const fields = new FormData(event.currentTarget);
    const guidance = Object.fromEntries(Array.from(fields.entries())
      .filter(([key]) => key.startsWith("guidance/"))
      .map(([key, value]) => [key.slice("guidance/".length), String(value).trim()]));
    setError("");
    setSaving(true);
    try {
      const updated = await api<PublishedPolicy>("/policies/returns", jsonBody({
        expected_revision: policy.revision,
        rules: {
          return_days: Number(fields.get("days")),
          refund_percent: Number(fields.get("percent")),
          max_item_quantity: fields.get("quantity") ? Number(fields.get("quantity")) : null,
          max_request_amount: fields.get("limit") || null,
          guidance,
        },
      }, "PUT"));
      setSaved(updated);
      setEditing(false);
      reload();
      sources.reload();
      history.reload();
    } catch (failure) {
      setError(describeError(failure));
    } finally {
      setSaving(false);
    }
  }

  return <>
    <PageHeading title="Return policy">See the rules Canopy uses for returns and refund requests.</PageHeading>
    {!policy && resource.state === "loading" && <Loading />}
    {!policy && resource.state === "error" && <ErrorNotice message={resource.error} retry={reload} />}
    {policy && <form className="policy-workspace" key={policy.revision} onSubmit={publish}>
      <section className="panel" aria-labelledby="refund-rules">
        <div className="panel-header"><h2 id="refund-rules">Refund rules</h2>
          {role === "admin" && !editing && <button className="button secondary small" type="button"
            disabled={sources.resource.state !== "ready"} onClick={() => { setSaved(undefined); setEditing(true); }}>Edit policy</button>}
        </div>
        {saved && !editing && <p className="notice success" role="status">Return policy published.</p>}
        {editing ? <fieldset className="policy-fields" disabled={saving}>
          <label>Days to request a return<input name="days" type="number" min={1} max={365} required defaultValue={policy.rules.return_days} /></label>
          <label>Refund percentage of the price paid<input name="percent" type="number" min={1} max={100} required defaultValue={policy.rules.refund_percent} /></label>
          <label>Item limit per product<input name="quantity" type="number" min={1} max={10000} defaultValue={policy.rules.max_item_quantity ?? ""} placeholder="No extra limit" /></label>
          <label>Refund limit per request (USD)<input name="limit" type="number" min={0.01} max={9999999999.99} step={0.01} defaultValue={policy.rules.max_request_amount ?? ""} placeholder="No extra limit" /></label>
        </fieldset> : <dl className="facts">
          <div><dt>Return window</dt><dd>{policy.rules.return_days} days after delivery</dd></div>
          <div><dt>Refund amount</dt><dd>{policy.rules.refund_percent}% of the price paid</dd></div>
          <div><dt>Item limit per product</dt><dd>{policy.rules.max_item_quantity ?? "Up to the number still available"}</dd></div>
          <div><dt>Refund limit per request</dt><dd>{policy.rules.max_request_amount ? money(policy.rules.max_request_amount, "USD") : "Up to the amount still available"}</dd></div>
        </dl>}
        {editing && <p className="muted">Requests above either limit go to support for review. Leave a limit blank to use the remaining quantity or amount on the order.</p>}
      </section>
      <section className="panel" aria-labelledby="policy-guidance">
        <div className="panel-header"><h2 id="policy-guidance">Policy guidance</h2></div>
        {editing && <p className="muted">Add plain-language instructions to any section. Canopy can use this guidance when customers ask about returns.</p>}
        {sources.resource.state === "loading" && <Loading />}
        {sources.resource.state === "error" && <ErrorNotice message={sources.resource.error} retry={sources.reload} />}
        {sources.resource.state === "ready" && <div className="policy-grid">
          {sources.resource.data.sources.map((source) => <article className="inline-panel policy-card" key={source.source}>
            <h3>{source.title === "Simulated submission and support" ? "Submitting a request and getting help" : source.title}</h3>
            {source.status !== "ready" && <p className="muted">Canopy’s policy search needs attention.</p>}
            {source.sections.map((section) => {
              const key = `${source.source}/${section.anchor}`;
              const heading = headings[section.heading] ?? section.heading;
              return <details key={key}><summary>{heading}</summary>
                <p className="prewrap">{section.text.split("\n\n").slice(2).join("\n\n").split("\n\nStore guidance\n")[0]}</p>
                {editing ? <label>Guidance for {heading.toLowerCase()}
                  <textarea name={`guidance/${key}`} rows={3} maxLength={1200} defaultValue={policy.rules.guidance[key] ?? ""} disabled={saving} />
                </label> : policy.rules.guidance[key] && <p className="prewrap">{policy.rules.guidance[key]}</p>}
              </details>;
            })}
          </article>)}
        </div>}
      </section>
      {editing && <section className="panel policy-publish">
        <p>Publishing applies the updated policy to new refund previews. Customers with an open preview will need to review it again.</p>
        {error && <ErrorNotice message={error} />}
        <div className="row-between">
          <button className="button secondary" type="button" disabled={saving} onClick={() => {
            setEditing(false); setError(""); reload(); sources.reload();
          }}>Cancel</button>
          <button className="button primary" type="submit" disabled={saving || sources.resource.state !== "ready"}>
            {saving ? "Publishing…" : "Publish policy"}
          </button>
        </div>
      </section>}
      {role === "admin" && !editing && history.resource.state === "ready" && <section className="panel">
        <details><summary>Published versions</summary>
          {history.resource.data.length ? <ul className="record-list policy-history">{history.resource.data.map((entry) =>
            <li key={entry.revision}>Version {entry.revision} · {dateTime(entry.published_at)}{entry.revision === policy.revision ? " · Current" : ""}</li>
          )}</ul> : <Empty title="No published changes yet" />}
        </details>
      </section>}
    </form>}
  </>;
}
