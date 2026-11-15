import { Schema } from "effect";

export const priceLine = "20 USD each month";

export const CheckoutStarted = Schema.Struct({
  url: Schema.String,
});

export type CheckoutStarted = typeof CheckoutStarted.Type;

export const SessionId = Schema.String.check(
  Schema.isPattern(/^cs_(?:test|live)_[A-Za-z0-9]+$/u),
  Schema.isMaxLength(255),
);

export const SubscriptionQuery = Schema.Struct({
  sessionId: SessionId,
});

export type SubscriptionQuery = typeof SubscriptionQuery.Type;

export const Subscription = Schema.Struct({
  active: Schema.Boolean,
  email: Schema.NullOr(Schema.String),
  status: Schema.String,
});

export type Subscription = typeof Subscription.Type;

export const WebhookEvent = Schema.Struct({
  data: Schema.Struct({
    object: Schema.Struct({
      customer: Schema.optional(Schema.NullOr(Schema.String)),
      id: Schema.String,
      subscription: Schema.optional(Schema.NullOr(Schema.String)),
    }),
  }),
  id: Schema.String,
  type: Schema.String,
});

export type WebhookEvent = typeof WebhookEvent.Type;

export const WebhookResult = Schema.Struct({
  handled: Schema.Boolean,
  type: Schema.String,
});

export type WebhookResult = typeof WebhookResult.Type;

export class BillingFailed extends Schema.TaggedError<BillingFailed>()("BillingFailed", {
  reason: Schema.Literals(["unavailable", "not-configured", "bad-signature", "not-found"]),
}) {}

const firstValue = (value: string | null): string => value?.split(",")[0]?.trim() ?? "";

export const originOf = (url: string, headers: Headers): string | null => {
  const host = firstValue(headers.get("x-forwarded-host")) || firstValue(headers.get("host"));
  const proto = firstValue(headers.get("x-forwarded-proto")) || new URL(url).protocol.slice(0, -1);

  if (host === "" || (proto !== "http" && proto !== "https")) {
    return null;
  }

  return URL.canParse(`${proto}://${host}`) ? new URL(`${proto}://${host}`).origin : null;
};
