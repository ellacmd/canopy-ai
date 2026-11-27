const dateFormat = new Intl.DateTimeFormat("en", {
  dateStyle: "medium",
  timeStyle: "short",
});

export function dateTime(value: string) {
  const timestamp = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? value : dateFormat.format(date);
}

export function money(amount: string, currency: string) {
  // Prices are formatted from server-calculated amounts only.
  if (!/^[A-Za-z]{3}$/.test(currency)) return `${amount} ${currency}`;
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(Number(amount));
}

export function trackingNumber(value: string) {
  return value.replace(/^SYNTHETIC-/, "");
}

const labels: Record<string, string> = {
  refund_exception: "Return needs review",
  human_requested: "Customer asked for help",
  unsupported_request: "Request needs a specialist",
  missing_information: "More information needed",
  wrong_item: "Received the wrong item",
  damaged: "Item arrived damaged",
  other: "Another reason",
  needs_review: "Needs attention",
  succeeded: "Completed",
  failed: "Couldn’t finish",
};

export function label(value: string) {
  return labels[value] ?? value.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase());
}

export const stageLabels: Record<string, string> = {
  classify: "Read the request",
  context: "Look up information",
  action: "Check what Canopy can do",
  respond: "Prepare a reply",
};

export const PAGE_SIZE = 20;
