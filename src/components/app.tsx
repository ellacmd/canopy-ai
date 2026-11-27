"use client";

import { Activity, ArrowRight, BookOpen, Inbox, LogOut, MessageCircle, Package, Phone, ReceiptText, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api, describeError, SESSION_EXPIRED, SESSION_REVISION, signIn, signOut } from "@/lib/api";
import type { OrderDetail, Principal } from "@/lib/contracts";
import { Chat } from "./chat";
import { CustomerOrders, CustomerRefunds } from "./customer";
import { Dashboard, StaffInbox, StaffRefunds } from "./operations";
import { PolicyAdministration } from "./policy-administration";
import { Brand, ErrorNotice, Loading } from "./ui";

const customerNavigation = [
  { id: "chat", text: "Support", icon: MessageCircle },
  { id: "orders", text: "My orders", icon: Package },
  { id: "customer-refunds", text: "Refund requests", icon: ReceiptText },
] as const;
const staffNavigation = [
  { id: "inbox", text: "Support inbox", icon: Inbox },
  { id: "staff-refunds", text: "Refund review", icon: ReceiptText },
  { id: "policies", text: "Return policy", icon: BookOpen },
  { id: "operations", text: "Operations", icon: Activity },
] as const;
type Section = (typeof customerNavigation[number] | typeof staffNavigation[number])["id"];
type Session =
  | { state: "loading" | "anonymous" }
  | { state: "signed-in"; principal: Principal }
  | { state: "error"; error: string };

function SignIn({ onSignedIn }: { onSignedIn: (principal: Principal) => void }) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    setPending(true);
    setError("");
    try {
      onSignedIn(await signIn(String(fields.get("email")), String(fields.get("password"))));
    } catch (failure) {
      setError(describeError(failure));
      setPending(false);
    }
  }
  return <main className="sign-in">
    <section className="sign-in-cover">
      <Brand />
      <div className="cover-content">
        <p>Amazora customer support</p>
        <h1>How can we help?</h1>
        <p className="cover-description">Track an order, check a return, or get in touch with a support specialist.</p>
      </div>
      <div className="cover-foot"><ShieldCheck size={17} aria-hidden /> Amazora support portal</div>
    </section>
    <section className="sign-in-panel" aria-labelledby="sign-in-title">
      <form className="sign-in-form" onSubmit={submit}>
        <h2 id="sign-in-title">Sign in to Canopy</h2>
        <p>Use your Amazora account to continue.</p>
        {error && <ErrorNotice message={error} />}
        <label>Email address<input name="email" type="email" autoComplete="username" required maxLength={254} disabled={pending} /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" required maxLength={256} disabled={pending} /></label>
        <button className="button primary" type="submit" disabled={pending}>
          {pending ? "Signing in…" : "Sign in"} <ArrowRight size={18} aria-hidden />
        </button>
      </form>
    </section>
  </main>;
}

function Workspace({ principal, onSignedOut }: { principal: Principal; onSignedOut: () => void }) {
  const isCustomer = principal.role === "customer";
  const navigation = isCustomer ? customerNavigation : staffNavigation;
  const [section, setSection] = useState<Section>(navigation[0].id);
  const [refundOrder, setRefundOrder] = useState<OrderDetail>();
  const [callActive, setCallActive] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState("");

  function navigate(next: Section) {
    setSection(next);
    requestAnimationFrame(() => {
      const headings = document.querySelectorAll<HTMLElement>("#main-content h1");
      Array.from(headings).find((heading) => heading.getClientRects().length > 0)?.focus();
    });
  }

  async function logout() {
    setSigningOut(true);
    setError("");
    try {
      await signOut();
      onSignedOut();
    } catch (failure) {
      setError(describeError(failure));
      setSigningOut(false);
    }
  }

  return <div className="app-shell">
    <a href="#main-content" className="skip-link">Skip to content</a>
    <aside className="sidebar">
      <div className="brand-block"><Brand /><p>Amazora · {isCustomer ? "Customer support" : "Support workspace"}</p></div>
      <nav className="main-nav" aria-label="Workspace">
        {navigation.map(({ id, text, icon: Icon }) =>
          <button key={id} type="button" aria-current={section === id ? "page" : undefined} onClick={() => navigate(id)}>
            <Icon size={19} aria-hidden /> <span>{text}</span>
          </button>)}
      </nav>
      <div className="sidebar-account">
        <span className="avatar" aria-hidden>{principal.email.slice(0, 1).toUpperCase() || "A"}</span>
        <div className="account-label"><strong>{principal.email}</strong><span>{isCustomer ? "Customer" : principal.role === "admin" ? "Administrator" : "Support agent"}</span></div>
        <button type="button" className="button icon-button" onClick={() => void logout()} aria-label="Sign out" title="Sign out" disabled={signingOut}>
          <LogOut size={19} aria-hidden />
        </button>
      </div>
    </aside>
    <main id="main-content" className="workspace" tabIndex={-1}>
      {error && <ErrorNotice message={error} />}
      {callActive && section !== "chat" && <aside className="ongoing-call">
        <span role="status"><Phone size={16} aria-hidden /> Call in progress</span>
        <button type="button" className="button secondary small" onClick={() => navigate("chat")}>Back to call</button>
      </aside>}
      {isCustomer && <div hidden={section !== "chat"}><Chat key={principal.id} customerId={principal.id}
        callActive={callActive} onCallActive={setCallActive} reviewOrder={refundOrder}
        onReviewOpened={() => setRefundOrder(undefined)} /></div>}
      {section === "orders" && <CustomerOrders onRefund={(order) => { setRefundOrder(order); navigate("chat"); }} />}
      {section === "customer-refunds" && <CustomerRefunds />}
      {section === "inbox" && <StaffInbox principal={principal} />}
      {section === "staff-refunds" && <StaffRefunds />}
      {section === "policies" && <PolicyAdministration role={principal.role} />}
      {section === "operations" && <Dashboard />}
    </main>
  </div>;
}

export function CanopyApp() {
  const [session, setSession] = useState<Session>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let controller: AbortController | undefined;
    function load() {
      controller?.abort();
      const active = new AbortController();
      controller = active;
      api<Principal>("/auth/me", { signal: active.signal }).then(
        (principal) => {
          if (!active.signal.aborted) setSession({ state: "signed-in", principal });
        },
        (error) => {
          if (active.signal.aborted) return;
          setSession(error instanceof ApiError && error.status === 401
            ? { state: "anonymous" }
            : { state: "error", error: describeError(error) });
        },
      );
    }
    function onStorage(event: StorageEvent) {
      if (event.key !== SESSION_REVISION && event.key !== null) return;
      setSession({ state: "loading" });
      load();
    }
    function onExpired() {
      controller?.abort();
      setSession({ state: "anonymous" });
    }
    window.addEventListener("storage", onStorage);
    window.addEventListener(SESSION_EXPIRED, onExpired);
    load();
    return () => {
      controller?.abort();
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(SESSION_EXPIRED, onExpired);
    };
  }, [attempt]);

  if (session.state === "loading") return <main className="session-state"><Brand /><Loading>Checking your session…</Loading></main>;
  if (session.state === "error") return <main className="session-state"><Brand /><ErrorNotice message={session.error} retry={() => {
    setSession({ state: "loading" });
    setAttempt((previous) => previous + 1);
  }} /></main>;
  if (session.state === "signed-in") return <Workspace key={session.principal.id} principal={session.principal} onSignedOut={() => setSession({ state: "anonymous" })} />;
  return <SignIn onSignedIn={(principal) => setSession({ state: "signed-in", principal })} />;
}
