import { createEffectLogic, setupEffect } from "@xstate/effect";
import { getShortestPaths } from "xstate/graph";
import { Context, Schema } from "effect";
import type { Effect } from "effect";

import type { ApiUnavailable } from "./api.ts";
import { CheckInput, CheckOutput } from "./check.ts";
import type { CheckFailed } from "./check.ts";
import { actorDone, actorFailed, machineChecks } from "./machine-checks.ts";

export class CheckClient extends Context.Service<
  CheckClient,
  {
    readonly check: (input: CheckInput) => Effect.Effect<CheckOutput, CheckFailed | ApiUnavailable>;
  }
>()("footnote/domain/check-flow.machine/CheckClient") {}

const checkTask = createEffectLogic({
  effect: ({ input }) => CheckClient.use((client) => client.check(input)),
  schemas: { input: CheckInput },
});

const sample: CheckOutput = {
  claim: "A claim",
  line: "What sources report.",
  ms: 5000,
  sources: [],
  stance: "differ",
};

export const checkFlowMachine = setupEffect({
  actors: { checkTask },
  schemas: {
    context: Schema.Struct({ answer: Schema.NullOr(CheckOutput), text: Schema.String }),
    events: {
      SUBMIT: Schema.Struct({ text: Schema.String }),
    },
  },
}).createMachine({
  context: { answer: null, text: "" },
  id: "checkFlow",
  initial: "editing",
  states: {
    answered: {
      on: {
        SUBMIT: ({ event }) => ({
          context: { answer: null, text: event.text },
          target: "checking",
        }),
      },
    },
    checking: {
      invoke: {
        id: "checkTask",
        input: ({ context }) => ({ text: context.text }),
        onDone: ({ context, event }) => ({
          context: { answer: event.output, text: context.text },
          target: "answered",
        }),
        onError: { target: "failed" },
        src: "checkTask",
      },
    },
    editing: {
      on: {
        SUBMIT: ({ event }) => ({
          context: { answer: null, text: event.text },
          target: "checking",
        }),
      },
    },
    failed: {
      on: {
        SUBMIT: ({ event }) => ({
          context: { answer: null, text: event.text },
          target: "checking",
        }),
      },
    },
  },
});

export const checkFlowChecks = machineChecks({
  invariants: [
    {
      holds: ({ context, value }) => (value === "answered") === (context.answer !== null),
      name: "only an answered flow has an answer",
    },
    {
      holds: ({ context, value }) => value === "editing" || context.text !== "",
      name: "only the editing state can have an empty text",
    },
  ],
  name: "checkFlow",
  reachable: ["editing", "checking", "answered", "failed"],
  states: () =>
    getShortestPaths(checkFlowMachine, {
      events: [
        { text: "The moon is made of cheese.", type: "SUBMIT" },
        actorDone("checkTask", sample),
        actorFailed("checkTask"),
      ],
      limit: 1000,
    }).map((path) => path.state),
  tagOf: ({ value }) => value,
});
