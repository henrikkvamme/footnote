import { createServerFn } from "@tanstack/react-start";
import { getRequest } from "@tanstack/react-start/server";
import { Schema } from "effect";

import { BillingFailed, originOf, SubscriptionQuery } from "#/domain/billing.ts";
import { Billing } from "#/server/billing.ts";
import { runApi } from "#/server/runtime.ts";

export const startCheckout = createServerFn({ method: "POST" }).handler(() => {
  const request = getRequest();

  return runApi(BillingFailed)(
    Billing.use((service) => service.start(originOf(request.url, request.headers))),
  );
});

export const subscription = createServerFn({ method: "GET" })
  .validator(Schema.toStandardSchemaV1(SubscriptionQuery))
  .handler(({ data }) =>
    runApi(BillingFailed)(Billing.use((service) => service.subscription(data))),
  );
