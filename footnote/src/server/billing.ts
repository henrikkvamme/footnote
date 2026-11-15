import { Clock, Context, Effect, Layer, Result, Schema } from "effect";
import { Mac, SecureRandom } from "effect/crypto";
import { Hex } from "effect/encoding";
import { FetchHttpClient, HttpClient, HttpClientRequest, HttpClientResponse } from "effect/http";

import { BillingFailed, WebhookEvent } from "#/domain/billing.ts";
import type {
  CheckoutStarted,
  Subscription,
  SubscriptionQuery,
  WebhookResult,
} from "#/domain/billing.ts";

const stripeApi = "https://api.stripe.com/v1";

const timeoutMs = 15_000;

const toleranceSeconds = 300;

const env = (name: string): string => process.env[name]?.trim() ?? "";

const publicUrl = (): string => env("PUBLIC_URL") || "https://footnotecheck.com";

const StripeSession = Schema.Struct({
  customer_details: Schema.optional(
    Schema.NullOr(Schema.Struct({ email: Schema.optional(Schema.NullOr(Schema.String)) })),
  ),
  id: Schema.String,
  status: Schema.optional(Schema.NullOr(Schema.String)),
  subscription: Schema.optional(
    Schema.NullOr(Schema.Struct({ id: Schema.String, status: Schema.String })),
  ),
  url: Schema.optional(Schema.NullOr(Schema.String)),
});

const liveStatuses = new Set(["active", "trialing"]);

const unavailable = () => new BillingFailed({ reason: "unavailable" });

const secretKey = Effect.fn("Billing.secretKey")(function* secretKey() {
  const key = env("STRIPE_SECRET_KEY");

  if (key === "") {
    return yield* new BillingFailed({ reason: "not-configured" });
  }

  return key;
});

const send = Effect.fn("Billing.send")(function* send(
  request: HttpClientRequest.HttpClientRequest,
  missing: BillingFailed,
) {
  const client = yield* HttpClient.HttpClient;
  const key = yield* secretKey();

  const response = yield* request.pipe(
    HttpClientRequest.acceptJson,
    HttpClientRequest.bearerToken(key),
    client.execute,
    Effect.timeout(timeoutMs),
    Effect.mapError(unavailable),
  );

  if (response.status === 404) {
    return yield* missing;
  }

  if (response.status !== 200) {
    yield* Effect.logWarning("Stripe refused a request", { status: response.status });

    return yield* unavailable();
  }

  return yield* HttpClientResponse.schemaBodyJson(StripeSession)(response).pipe(
    Effect.mapError(unavailable),
  );
});

const signatureParts = (header: string) => {
  const pairs = header.split(",").map((part) => part.trim().split("="));
  const time = Number(pairs.find(([name]) => name === "t")?.[1] ?? "");
  const signatures = pairs.filter(([name]) => name === "v1").map(([, value]) => value ?? "");

  return { signatures, time };
};

const encoder = new TextEncoder();

const matches = (signature: string, expected: Uint8Array): boolean =>
  Result.match(Hex.decode(signature), {
    onFailure: () => false,
    onSuccess: (bytes) =>
      bytes.length === expected.length && bytes.length > 0 && Mac.timingSafeEqual(bytes, expected),
  });

const verify = Effect.fn("Billing.verify")(function* verify(payload: string, header: string) {
  const secret = env("STRIPE_WEBHOOK_SECRET");

  if (secret === "") {
    return yield* new BillingFailed({ reason: "not-configured" });
  }

  const { signatures, time } = signatureParts(header);
  const nowSeconds = (yield* Clock.currentTimeMillis) / 1000;

  if (!Number.isFinite(time) || Math.abs(nowSeconds - time) > toleranceSeconds) {
    return yield* new BillingFailed({ reason: "bad-signature" });
  }

  const mac = yield* Mac.Mac;

  const key = yield* Mac.key(Mac.hmacSha256, encoder.encode(secret)).pipe(
    Effect.mapError(unavailable),
  );

  const expected = yield* mac
    .sign(key, encoder.encode(`${time}.${payload}`))
    .pipe(Effect.mapError(unavailable));

  if (!signatures.some((signature) => matches(signature, expected))) {
    return yield* new BillingFailed({ reason: "bad-signature" });
  }

  return yield* Schema.decodeEffect(Schema.fromJsonString(WebhookEvent))(payload).pipe(
    Effect.mapError(() => new BillingFailed({ reason: "bad-signature" })),
  );
});

const handle = Effect.fn("Billing.handle")(function* handle(event: WebhookEvent) {
  const { customer, id, subscription } = event.data.object;

  if (event.type === "checkout.session.completed") {
    yield* Effect.logInfo("Subscription started", { customer, session: id, subscription });

    return { handled: true, type: event.type };
  }

  if (event.type === "customer.subscription.deleted") {
    yield* Effect.logInfo("Subscription ended", { customer, subscription: id });

    return { handled: true, type: event.type };
  }

  return { handled: false, type: event.type };
});

export class Billing extends Context.Service<
  Billing,
  {
    readonly start: (origin: string | null) => Effect.Effect<CheckoutStarted, BillingFailed>;
    readonly subscription: (query: SubscriptionQuery) => Effect.Effect<Subscription, BillingFailed>;
    readonly webhook: (
      payload: string,
      signature: string,
    ) => Effect.Effect<WebhookResult, BillingFailed>;
  }
>()("footnote/server/billing") {
  static readonly layer = Layer.effect(
    Billing,
    Effect.gen(function* make() {
      const client = yield* HttpClient.HttpClient;
      const mac = yield* Mac.Mac;

      return Billing.of({
        start: Effect.fn("Billing.start")(function* start(origin: string | null) {
          const price = env("STRIPE_PRICE_ID");

          if (price === "") {
            return yield* new BillingFailed({ reason: "not-configured" });
          }

          const base = origin ?? publicUrl();

          const session = yield* send(
            HttpClientRequest.post(`${stripeApi}/checkout/sessions`).pipe(
              HttpClientRequest.bodyUrlParams({
                "adaptive_pricing[enabled]": "false",
                cancel_url: `${base}/?checkout=cancelled#pricing`,
                "line_items[0][price]": price,
                "line_items[0][quantity]": "1",
                mode: "subscription",
                success_url: `${base}/subscribed?session_id={CHECKOUT_SESSION_ID}`,
              }),
            ),
            unavailable(),
          ).pipe(Effect.provideService(HttpClient.HttpClient, client));

          if (session.url === undefined || session.url === null) {
            return yield* unavailable();
          }

          return { url: session.url };
        }),
        subscription: Effect.fn("Billing.subscription")(function* subscription(
          query: SubscriptionQuery,
        ) {
          const session = yield* send(
            HttpClientRequest.get(`${stripeApi}/checkout/sessions/${query.sessionId}`).pipe(
              HttpClientRequest.setUrlParams({ "expand[]": "subscription" }),
            ),
            new BillingFailed({ reason: "not-found" }),
          ).pipe(Effect.provideService(HttpClient.HttpClient, client));

          const status = session.subscription?.status ?? session.status ?? "unknown";

          return {
            active: session.status === "complete" && liveStatuses.has(status),
            email: session.customer_details?.email ?? null,
            status,
          };
        }),
        webhook: Effect.fn("Billing.webhook")(function* webhook(
          payload: string,
          signature: string,
        ) {
          const event = yield* verify(payload, signature).pipe(Effect.provideService(Mac.Mac, mac));

          return yield* handle(event);
        }),
      });
    }),
  ).pipe(
    Layer.provide(
      Layer.mergeAll(
        FetchHttpClient.layer,
        Mac.layerWebCrypto.pipe(Layer.provide(SecureRandom.layerWebCrypto)),
      ),
    ),
  );
}
