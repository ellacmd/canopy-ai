export type Principal = { id: string; email: string; role: "customer" | "support" | "admin" };

export type Order = {
  id: string;
  order_number: string;
  status: string;
  payment_status: string;
  total_amount: string;
  currency: string;
  created_at: string;
  delivered_at: string | null;
};

export type OrderDetail = Order & {
  items: {
    order_item_id: string;
    product_name: string;
    sku: string;
    quantity: number;
    unit_price: string;
    remaining_refundable_quantity: number;
  }[];
  shipment: {
    carrier: string;
    tracking_number: string;
    status: string;
    estimated_delivery: string | null;
  } | null;
};

export type RefundReason = "change_of_mind" | "damaged" | "wrong_item" | "other";
export type Selection = {
  order_id: string;
  items: { order_item_id: string; quantity: number }[];
  reason: RefundReason;
  details: string;
};
export type Quote = {
  id: string;
  amount: string;
  currency: string;
  policy_version: string;
  expires_at: string;
  selection: Selection;
};

export type Refund = {
  id: string;
  order_id: string;
  status: string;
  amount: string;
  currency: string;
  reason: string;
  policy_version: string;
  confirmed_at: string | null;
  created_at: string;
  simulated: true;
  items: { order_item_id: string; quantity: number; amount: string }[];
};

export type TicketStatus = "open" | "in_progress" | "resolved" | "closed";
export type Ticket = {
  id: string;
  conversation_id: string;
  order_id: string | null;
  reason: string;
  summary: string;
  status: TicketStatus;
  assigned_agent_id: string | null;
  resolution_note: string | null;
  created_at: string;
  updated_at: string;
};
export type Conversation = { id: string; status: string; updated_at: string; primary_intent: string | null };
export type Citation = {
  title: string;
  section: string;
  source_version: string;
  rules_version: string;
  locator: string;
  excerpt: string;
};
export type Message = { id: string; role: string; content: string; created_at: string; citations: Citation[] };

export type TurnBody =
  | { action: "message"; message: string }
  | { action: "message"; selection: Selection }
  | { action: "confirm_refund"; confirmation: { quote_id: string; policy_version: string; confirmed: true } }
  | { action: "cancel_refund" | "escalate" };

export type TurnResult = {
  conversation_id: string;
  message_id: string;
  text: string;
  state: string;
  citations: Citation[];
  quote: Quote | null;
  refund: Refund | null;
  escalation: Ticket | null;
};

export type Execution = {
  id: string;
  trace_id: string;
  conversation_id: string;
  status: string;
  created_at: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  stages: string[];
};
type Customer = { name: string; email: string };
export type CaseContext = {
  ticket: Ticket;
  customer: Customer;
  order: OrderDetail | null;
  messages: Message[];
  executions: Execution[];
};
export type RefundContext = { refund: Refund; customer: Customer; order: OrderDetail };
export type QueueMetrics = {
  active_tickets: number;
  submitted_refunds: number;
  executions: number;
  failed_executions: number;
};
export type PolicyIndex = {
  rules_version: string;
  sources: {
    source: string;
    title: string;
    version: string;
    sha256: string;
    status: "ready" | "missing" | "needs_review";
    sections: { heading: string; anchor: string; text: string }[];
  }[];
};
