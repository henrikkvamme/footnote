import { createActorAtoms } from "@xstate/effect/atom";
import { Effect, Layer } from "effect";
import { Atom } from "effect/reactivity";

import { startCheckout } from "#/api/billing.ts";
import { fromApi } from "#/domain/api.ts";
import { BillingFailed } from "#/domain/billing.ts";
import { CheckoutClient, checkoutMachine } from "#/domain/checkout.machine.ts";

const CheckoutClientLive = Layer.succeed(CheckoutClient, {
  start: Effect.fn("CheckoutClient.start")(() => fromApi(BillingFailed)(() => startCheckout())),
});

export const checkout = createActorAtoms(Atom.runtime(CheckoutClientLive), checkoutMachine);
