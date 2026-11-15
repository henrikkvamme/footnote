import { createEffectLogic, setupEffect } from "@xstate/effect";
import { getShortestPaths } from "xstate/graph";
import { Context, Schema } from "effect";
import type { Effect } from "effect";

import type { ApiUnavailable } from "./api.ts";
import type { BillingFailed, CheckoutStarted } from "./billing.ts";
import { actorDone, actorFailed, machineChecks } from "./machine-checks.ts";

export class CheckoutClient extends Context.Service<
  CheckoutClient,
  {
    readonly start: () => Effect.Effect<CheckoutStarted, BillingFailed | ApiUnavailable>;
  }
>()("footnote/domain/checkout.machine/CheckoutClient") {}

const startTask = createEffectLogic({
  effect: () => CheckoutClient.use((client) => client.start()),
});

const sample: CheckoutStarted = { url: "https://checkout.stripe.com/c/pay/cs_test_sample" };

export const checkoutMachine = setupEffect({
  actors: { startTask },
  schemas: {
    context: Schema.Struct({ url: Schema.NullOr(Schema.String) }),
    events: {
      START: Schema.Struct({}),
    },
  },
}).createMachine({
  context: { url: null },
  id: "checkout",
  initial: "idle",
  states: {
    failed: {
      on: { START: { target: "starting" } },
    },
    idle: {
      on: { START: { target: "starting" } },
    },
    redirecting: {},
    starting: {
      invoke: {
        id: "startTask",
        onDone: ({ event }) => ({
          context: { url: event.output.url },
          target: "redirecting",
        }),
        onError: { target: "failed" },
        src: "startTask",
      },
    },
  },
});

export const checkoutChecks = machineChecks({
  invariants: [
    {
      holds: ({ context, value }) => (value === "redirecting") === (context.url !== null),
      name: "only a redirecting checkout has a url",
    },
  ],
  name: "checkout",
  reachable: ["idle", "starting", "redirecting", "failed"],
  states: () =>
    getShortestPaths(checkoutMachine, {
      events: [{ type: "START" }, actorDone("startTask", sample), actorFailed("startTask")],
      limit: 1000,
    }).map((path) => path.state),
  tagOf: ({ value }) => value,
});
