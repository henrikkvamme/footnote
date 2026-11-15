import { setupEffect } from "@xstate/effect";
import { Schema } from "effect";
import { getShortestPaths } from "xstate/graph";

import { afterDelay, machineChecks } from "./machine-checks.ts";

export const howPhases = [
  "sound",
  "sentences",
  "gate",
  "check",
  "footnote",
  "footnoteOpen",
] as const;

export type HowPhase = (typeof howPhases)[number];

export const howDelays = {
  check: 7000,
  footnote: 2200,
  footnoteOpen: 6000,
  gate: 7500,
  sentences: 5500,
  sound: 5500,
} as const;

const jumps = {
  SHOW_CHECK: { target: "check" },
  SHOW_FOOTNOTE: { target: "footnote" },
  SHOW_GATE: { target: "gate" },
  SHOW_SENTENCES: { target: "sentences" },
  SHOW_SOUND: { target: "sound" },
} as const;

export const howMachine = setupEffect({
  schemas: {
    context: Schema.Struct({}),
    events: {
      SHOW_CHECK: Schema.Struct({}),
      SHOW_FOOTNOTE: Schema.Struct({}),
      SHOW_GATE: Schema.Struct({}),
      SHOW_SENTENCES: Schema.Struct({}),
      SHOW_SOUND: Schema.Struct({}),
      TAP_ISLAND: Schema.Struct({}),
    },
  },
}).createMachine({
  context: {},
  id: "how",
  initial: "sound",
  states: {
    check: {
      after: { [howDelays.check]: { target: "footnote" } },
      on: jumps,
    },
    footnote: {
      after: { [howDelays.footnote]: { target: "footnoteOpen" } },
      on: { ...jumps, TAP_ISLAND: { target: "footnoteOpen" } },
    },
    footnoteOpen: {
      after: { [howDelays.footnoteOpen]: { target: "sound" } },
      on: { ...jumps, TAP_ISLAND: { target: "footnote" } },
    },
    gate: {
      after: { [howDelays.gate]: { target: "check" } },
      on: jumps,
    },
    sentences: {
      after: { [howDelays.sentences]: { target: "gate" } },
      on: jumps,
    },
    sound: {
      after: { [howDelays.sound]: { target: "sentences" } },
      on: jumps,
    },
  },
});

export const howChecks = machineChecks({
  invariants: [],
  name: "how",
  reachable: [...howPhases],
  states: () =>
    getShortestPaths(howMachine, {
      events: [
        { type: "SHOW_SOUND" },
        { type: "SHOW_SENTENCES" },
        { type: "SHOW_GATE" },
        { type: "SHOW_CHECK" },
        { type: "SHOW_FOOTNOTE" },
        { type: "TAP_ISLAND" },
        afterDelay("how.sound", howDelays.sound),
        afterDelay("how.sentences", howDelays.sentences),
        afterDelay("how.gate", howDelays.gate),
        afterDelay("how.check", howDelays.check),
        afterDelay("how.footnote", howDelays.footnote),
        afterDelay("how.footnoteOpen", howDelays.footnoteOpen),
      ],
      limit: 1000,
    }).map((path) => path.state),
  tagOf: ({ value }) => value,
});
