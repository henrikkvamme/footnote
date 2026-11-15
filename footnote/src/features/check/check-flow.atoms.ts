import { createActorAtoms } from "@xstate/effect/atom";
import { Effect, Layer } from "effect";
import { Atom } from "effect/reactivity";

import { check } from "#/api/check.ts";
import { fromApi } from "#/domain/api.ts";
import { CheckFailed } from "#/domain/check.ts";
import type { CheckInput } from "#/domain/check.ts";
import { CheckClient, checkFlowMachine } from "#/domain/check-flow.machine.ts";

const CheckClientLive = Layer.succeed(CheckClient, {
  check: Effect.fn("CheckClient.check")((input: CheckInput) =>
    fromApi(CheckFailed)(() => check({ data: input })),
  ),
});

export const checkFlow = createActorAtoms(Atom.runtime(CheckClientLive), checkFlowMachine);
