import { BillingFailed } from "#/domain/billing.ts";
import { Billing } from "#/server/billing.ts";
import { runApi } from "#/server/runtime.ts";

const noStore = { "Cache-Control": "no-store" };

export const stripeWebhook = async (request: Request): Promise<Response> => {
  const payload = await request.text();
  const signature = request.headers.get("stripe-signature") ?? "";

  const result = await runApi(BillingFailed)(
    Billing.use((service) => service.webhook(payload, signature)),
  );

  if (result.ok) {
    return Response.json({ received: true, ...result.value }, { headers: noStore });
  }

  return Response.json(
    { error: result.error.reason },
    { headers: noStore, status: result.error.reason === "bad-signature" ? 400 : 500 },
  );
};
