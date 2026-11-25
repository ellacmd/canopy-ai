import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page, user: "maya" | "jordan" | "support") {
  await page.goto("/");
  await page.getByLabel("Email address").fill(`${user}@amazora.example`);
  await page.getByLabel("Password").fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: user === "support" ? "Support inbox" : "Support chat" })).toBeVisible();
}

async function message(page: Page, text: string) {
  await page.getByLabel("Message Canopy").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

test("customer reviews and submits once, and staff manages the resulting support case", async ({ page, browser }) => {
  await signIn(page, "maya");
  await page.getByRole("button", { name: "New conversation" }).click();
  await message(page, "Track order AMZ-1024");
  await expect(page.getByText(/SYNTHETIC-AMZ-1024/, { exact: false })).toBeVisible();
  await message(page, "What is the gift card policy?");
  await expect(page.getByText("Policy sources", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "My orders" }).click();
  await page.getByRole("button", { name: /AMZ-1024/ }).click();
  await expect(page.getByText("SYNTHETIC-AMZ-1024", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review a refund" }).click();
  await page.getByRole("spinbutton").fill("1");
  await page.getByRole("button", { name: "Preview refund" }).click();
  await expect(page.getByRole("heading", { name: "Review refund preview" })).toBeVisible();
  const preflight = await page.request.get("/api/refunds");
  expect(await preflight.json()).toEqual([]);

  await page.reload();
  await expect(page.getByText("Policy sources", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review refund preview" })).toBeVisible();

  const keys: string[] = [];
  await page.route("**/turns/stream", async (route) => {
    if (route.request().postDataJSON().action !== "confirm_refund") return route.continue();
    keys.push(route.request().headers()["idempotency-key"]);
    if (keys.length === 1) {
      const result = await route.fetch();
      expect(await result.text()).toContain("event: completed");
      await route.abort();
      return;
    }
    await route.continue();
  });
  await page.getByRole("button", { name: "Confirm simulated refund" }).click();
  await page.getByRole("button", { name: "Retry request" }).click();
  await expect(page.getByText(/Simulated refund submitted\. Reference/)).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  await page.getByRole("button", { name: "Refund requests" }).click();
  await expect(page.getByText("$89.00 · Change of mind")).toHaveCount(1);
  await page.getByRole("button", { name: "Support chat" }).click();
  await page.getByRole("button", { name: "Contact support" }).click();
  await expect(page.getByText(/Support ticket created\. Reference/)).toBeVisible();
  expect((await page.request.get("/api/admin/executions")).status()).toBe(403);
  const conversation = (await (await page.request.get("/api/conversations")).json())[0];

  const staff = await browser.newPage();
  try {
    await signIn(staff, "support");
    await staff.getByRole("button", { name: /Human requested/ }).click();
    await expect(staff.getByText(/Maya Chen/)).toBeVisible();
    await staff.getByText("Order context", { exact: true }).click();
    await expect(staff.getByText("AMZ-1024", { exact: true })).toBeVisible();
    await staff.getByLabel("New status").selectOption("in_progress");
    await staff.getByLabel("Assign to me").check();
    await staff.getByLabel("Resolution note").fill("Reviewed the synthetic order and shipment.");
    await staff.getByRole("button", { name: "Save ticket update" }).click();
    await expect(staff.getByText("Assigned to you")).toBeVisible();
    await staff.getByLabel("New status").selectOption("resolved");
    await staff.getByRole("button", { name: "Save ticket update" }).click();
    await expect(staff.getByRole("region", { name: "Ticket details" }).locator(".badge").filter({ hasText: /^Resolved$/ })).toBeVisible();

    await staff.getByRole("button", { name: "Refund review" }).click();
    await staff.getByRole("button", { name: /\$89\.00/ }).click();
    await expect(staff.getByText("$89.00 · Simulated")).toBeVisible();
    await staff.getByRole("button", { name: "Policy administration", exact: true }).click();
    await expect(staff.getByText("Ready", { exact: true })).toHaveCount(3);
    await staff.getByRole("button", { name: "Operations", exact: true }).click();
    await expect(staff.getByText("Recorded executions")).toBeVisible();
    const latestExecution = staff.locator(".timeline details").first();
    await latestExecution.locator("summary").click();
    await expect(latestExecution.getByRole("term").filter({ hasText: /^Trace ID$/ })).toBeVisible();
    expect((await staff.request.get(`/api/conversations/${conversation.id}/history`)).status()).toBe(403);
  } finally {
    await staff.close();
  }

  const other = await browser.newPage();
  try {
    await signIn(other, "jordan");
    expect((await other.request.get(`/api/conversations/${conversation.id}/history`)).status()).toBe(404);
    expect((await other.request.get(`/api/conversations/${conversation.id}/quote`)).status()).toBe(404);
  } finally {
    await other.close();
  }
});

test("expired tabs share one refresh, and sign-out clears both workspaces", async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "maya");
  const second = await context.newPage();
  await second.goto("/");
  await expect(second.getByRole("heading", { name: "Support chat" })).toBeVisible();
  const cookies = await context.cookies();
  expect(cookies.filter((cookie) => cookie.name.startsWith("canopy_"))).toHaveLength(2);
  expect(cookies.filter((cookie) => cookie.name.startsWith("canopy_")).every((cookie) => cookie.httpOnly)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);

  await context.clearCookies({ name: "canopy_access" });
  const refreshes: string[] = [];
  context.on("request", (request) => {
    if (request.url().endsWith("/api/auth/refresh") && request.method() === "POST") refreshes.push(request.url());
  });
  await Promise.all([
    page.getByRole("button", { name: "My orders" }).click(),
    second.getByRole("button", { name: "My orders" }).click(),
  ]);
  await expect(page.getByRole("button", { name: /AMZ-1024/ })).toBeVisible();
  await expect(second.getByRole("button", { name: /AMZ-1024/ })).toBeVisible();
  expect(refreshes).toHaveLength(1);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to Canopy" })).toBeVisible();
  await expect(second.getByRole("heading", { name: "Sign in to Canopy" })).toBeVisible();
});
