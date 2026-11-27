import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page, user: "maya" | "jordan" | "amara" | "support" | "admin", mode: "call" | "chat" = "chat") {
  await page.goto("/");
  await page.getByLabel("Email address").fill(`${user}@amazora.example`);
  await page.getByLabel("Password").fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const staff = user === "support" || user === "admin";
  await expect(page.getByRole("heading", { name: staff ? "Support inbox" : "Customer support" })).toBeVisible();
  if (mode === "chat" && !staff) await page.getByRole("button", { name: "Text chat", exact: true }).click();
}

async function message(page: Page, text: string) {
  await page.getByLabel("Message Canopy").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

test.describe("browser voice support", () => {
  test.use({ permissions: ["microphone"] });

  test("a voice call shows work, prepares a refund, and submits after confirmation", async ({ page }) => {
    const fixture = readFileSync(resolve("tests/e2e/fixtures/spoken-interruption.wav"));
    const speechCommands: string[] = [];
    page.on("websocket", (socket) => {
      socket.on("framesent", ({ payload }) => {
        if (payload === "speech_start" || payload === "speech_end") speechCommands.push(payload);
      });
    });
    await page.addInitScript(async (wav) => {
      const original = AudioBufferSourceNode.prototype.stop;
      AudioBufferSourceNode.prototype.stop = function (...args) {
        document.body.dataset.voiceCut = "true";
        return original.apply(this, args);
      };
      MediaDevices.prototype.getUserMedia = async () => {
        const audio = new AudioContext();
        const microphone = audio.createMediaStreamDestination();
        const speech = await audio.decodeAudioData(Uint8Array.from(atob(wav), (byte) => byte.charCodeAt(0)).buffer);
        const speechSamples = speech.getChannelData(0);
        for (let i = 0; i < speechSamples.length; i += 1) speechSamples[i] *= 0.15;
        const noise = audio.createBuffer(1, Math.ceil(audio.sampleRate * 1.4), audio.sampleRate);
        const noiseSamples = noise.getChannelData(0);
        let seed = 86397;
        let previous = 0;
        for (let i = 0; i < noiseSamples.length; i += 1) {
          const time = i / audio.sampleRate;
          if (time < 0.025) noiseSamples[i] = 0.7 * Math.sin(i * i);
          if (time >= 0.06 && time < 0.6) noiseSamples[i] = 0.055 * Math.sin(2 * Math.PI * 180 * time);
          if (time >= 0.65) {
            seed = (1664525 * seed + 1013904223) >>> 0;
            previous = 0.72 * previous + 0.28 * (seed / 2 ** 31 - 1);
            noiseSamples[i] = 0.64 * previous * Math.exp(-(((time - 0.65) % 0.093) * 30));
          }
        }

        function play(buffer: AudioBuffer) {
          const source = audio.createBufferSource();
          source.buffer = buffer;
          source.connect(microphone);
          source.start();
        }

        await audio.resume();
        window.addEventListener("fixture-noise", () => play(noise), { once: true });
        window.addEventListener("fixture-speech", () => play(speech));
        return microphone.stream;
      };
    }, fixture.toString("base64"));
    await signIn(page, "jordan", "call");
    await page.getByRole("button", { name: "Start call" }).click();
    await expect(page.getByText("Canopy is speaking", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "My orders" }).click();
    await expect(page.getByText("Call in progress", { exact: true })).toBeVisible();
    await page.evaluate(async () => {
      window.dispatchEvent(new Event("fixture-noise"));
      await new Promise((resolve) => setTimeout(resolve, 1550));
    });
    await page.getByRole("button", { name: "Back to call" }).click();
    await expect(page.getByText("Canopy is speaking", { exact: true })).toBeVisible();
    await expect(page.locator("body")).not.toHaveAttribute("data-voice-cut", "true");
    expect(speechCommands).toEqual([]);
    await page.evaluate(() => window.dispatchEvent(new Event("fixture-speech")));
    await expect(page.locator("body")).toHaveAttribute("data-voice-cut", "true", { timeout: 1500 });
    await expect.poll(() => speechCommands[0]).toBe("speech_start");
    await expect(page.getByRole("region", { name: "Verified order" })).toContainText("AMZ-1027");
    await expect(page.getByRole("heading", { name: "Call activity" })).toBeVisible();
    await expect(page.getByText("Return reviewed", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Message Canopy")).toHaveCount(0);
    await page.getByRole("button", { name: "Mute", exact: true }).click();
    await expect(page.getByText("Microphone muted")).toBeVisible();
    await page.getByRole("button", { name: "Unmute", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Review refund preview" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit refund request" })).toBeVisible();
    const refunds = await page.request.get("/api/refunds");
    expect(await refunds.json()).toEqual([]);
    await page.getByRole("button", { name: "Submit refund request" }).click();
    await expect(page.getByRole("region", { name: "Refund receipt" })).toContainText("$89.00");
    await page.evaluate(() => window.dispatchEvent(new Event("fixture-speech")));
    await expect(page.getByText("Call ended.")).toBeVisible();
    await page.getByText("Call transcript", { exact: true }).click();
    const transcript = page.getByRole("list", { name: "Call transcript entries" });
    await expect(transcript).toContainText("I changed my mind");
    await expect(transcript).toContainText("Goodbye. Have a great day.");
    await page.getByRole("button", { name: "Text chat", exact: true }).click();
    await expect(page.getByText("I changed my mind", { exact: false })).toHaveCount(0);
    await page.getByRole("button", { name: "New conversation" }).click();
    await expect(page.getByLabel("Message Canopy")).toBeVisible();
  });
});

test("customer reviews and submits once, and staff manages the resulting support case", async ({ page, browser }) => {
  await signIn(page, "maya");
  await page.getByRole("button", { name: "New conversation" }).click();
  await message(page, "Track order AMZ-1024");
  await expect(page.getByText(/tracking number AMZ-1024/, { exact: false })).toBeVisible();
  await message(page, "What is the gift card policy?");
  await expect(page.getByText("Policy sources", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "My orders" }).click();
  await page.getByRole("button", { name: /AMZ-1024/ }).click();
  await expect(page.locator(".shipment").getByText("AMZ-1024", { exact: true })).toBeVisible();
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
  await page.getByRole("button", { name: "Submit refund request" }).click();
  await page.getByRole("button", { name: "Retry request" }).click();
  await expect(page.getByText("Refund request submitted.", { exact: true })).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
  await page.getByRole("button", { name: "Refund requests" }).click();
  await expect(page.getByText("$89.00 · Change of mind")).toHaveCount(1);
  await page.getByRole("button", { name: "Support", exact: true }).click();
  await page.getByRole("button", { name: "Contact support" }).click();
  await expect(page.getByText("Your request was sent to the support team.", { exact: true })).toBeVisible();
  expect((await page.request.get("/api/admin/executions")).status()).toBe(403);
  const conversation = (await (await page.request.get("/api/conversations")).json())[0];

  const staff = await browser.newPage();
  try {
    await signIn(staff, "support");
    await staff.getByRole("button", { name: /Customer asked for help/ }).click();
    await expect(staff.getByText(/Maya Chen/)).toBeVisible();
    await staff.getByText("Order details", { exact: true }).click();
    await expect(staff.locator(".order-context strong").filter({ hasText: /^AMZ-1024$/ })).toBeVisible();
    await staff.getByLabel("New status").selectOption("in_progress");
    await staff.getByLabel("Assign to me").check();
    await staff.getByLabel("Resolution note").fill("Reviewed the synthetic order and shipment.");
    await staff.getByRole("button", { name: "Save changes" }).click();
    await expect(staff.getByText("Assigned to you")).toBeVisible();
    await staff.getByLabel("New status").selectOption("resolved");
    await staff.getByRole("button", { name: "Save changes" }).click();
    await expect(staff.getByRole("region", { name: "Ticket details" }).locator(".badge").filter({ hasText: /^Resolved$/ })).toBeVisible();

    await staff.getByRole("button", { name: "Refund review" }).click();
    await staff.getByRole("button", { name: /\$89\.00/ }).first().click();
    const refundDetails = staff.getByRole("region", { name: "Refund details" });
    await expect(refundDetails.getByText("$89.00 · Refund request")).toBeVisible();
    await expect(refundDetails.getByText(/Maya Chen/)).toBeVisible();
    await staff.getByRole("button", { name: "Return policy", exact: true }).click();
    await expect(staff.getByRole("heading", { name: "Refund rules" })).toBeVisible();
    await expect(staff.getByRole("button", { name: "Edit policy" })).toHaveCount(0);
    await staff.getByRole("button", { name: "Operations", exact: true }).click();
    await expect(staff.getByText("Requests handled")).toBeVisible();
    const activity = staff.getByRole("table", { name: "Request activity", exact: true });
    await expect(activity.getByRole("columnheader", { name: "Outcome" })).toBeVisible();
    await activity.getByText("View steps", { exact: true }).first().click();
    await expect(activity.getByText("Prepare a reply", { exact: true }).first()).toBeVisible();
    await expect(staff.getByText("Trace ID", { exact: true })).toHaveCount(0);
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

test("exhausted AI budget shows a clear error and preserves order access", async ({ page }) => {
  await signIn(page, "amara");
  await page.getByRole("button", { name: "New conversation" }).click();
  await message(page, "Show my orders");
  await expect(page.getByText(/daily AI usage budget has been reached/)).toBeVisible();
  await page.getByRole("button", { name: "My orders" }).click();
  await expect(page.getByRole("button", { name: /AMZ-1032/ })).toBeVisible();
});

test("admins publish return rules and plain-language policy guidance", async ({ page }) => {
  await signIn(page, "admin");
  await page.getByRole("button", { name: "Return policy", exact: true }).click();
  await page.getByRole("button", { name: "Edit policy" }).click();
  await page.getByLabel("Days to request a return").fill("45");
  await page.getByLabel("Refund percentage of the price paid").fill("90");
  await page.getByLabel("Item limit per product").fill("3");
  await page.getByLabel("Refund limit per request (USD)").fill("200");
  await page.getByText("Return window", { exact: true }).click();
  await page.getByLabel("Guidance for return window").fill("Keep your order number handy.");
  await page.getByRole("button", { name: "Publish policy" }).click();
  await expect(page.getByText("Return policy published.", { exact: true })).toBeVisible();
  await expect(page.getByText("45 days after delivery", { exact: true })).toBeVisible();
  await expect(page.getByText("90% of the price paid", { exact: true })).toBeVisible();
  await page.getByText("Published versions", { exact: true }).click();
  await expect(page.getByText(/Version 2.*Current/)).toBeVisible();
  await expect(page.getByText("SHA-256", { exact: true })).toHaveCount(0);
});

test("expired tabs share one refresh, and sign-out clears both workspaces", async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "maya");
  const second = await context.newPage();
  await second.goto("/");
  await expect(second.getByRole("heading", { name: "Customer support" })).toBeVisible();
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
