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

export function label(value: string) {
  return value.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase());
}

export const stageLabels: Record<string, string> = {
  classify: "Understand request",
  context: "Find relevant information",
  action: "Check available actions",
  respond: "Prepare response",
};

export const PAGE_SIZE = 20;
