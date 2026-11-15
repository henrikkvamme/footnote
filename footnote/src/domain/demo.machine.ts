import { setupEffect } from "@xstate/effect";
import { Schema } from "effect";
import { getShortestPaths } from "xstate/graph";

import { afterDelay, machineChecks } from "./machine-checks.ts";

export const demoPhases = [
  "listening",
  "listeningOpen",
  "checking",
  "checkingOpen",
  "arrived",
  "held",
  "noted",
  "sources",
] as const;

export type DemoPhase = (typeof demoPhases)[number];

export const demoDelays = {
  arrived: 8000,
  checking: 6500,
  listening: 3000,
  noted: 2200,
  open: 4500,
} as const;

const jumps = {
  SHOW_NEXT: { target: "arrived" },
  SHOW_SOURCES: { target: "sources" },
  SHOW_VIDEO: { target: "listening" },
} as const;

export const demoMachine = setupEffect({
  schemas: {
    context: Schema.Struct({}),
    events: {
      CLOSE: Schema.Struct({}),
      HOVER_IN: Schema.Struct({}),
      HOVER_OUT: Schema.Struct({}),
      SHOW_NEXT: Schema.Struct({}),
      SHOW_SOURCES: Schema.Struct({}),
      SHOW_VIDEO: Schema.Struct({}),
      SWIPE: Schema.Struct({}),
      TAP_ISLAND: Schema.Struct({}),
    },
  },
}).createMachine({
  context: {},
  id: "demo",
  initial: "listening",
  states: {
    arrived: {
      after: { [demoDelays.arrived]: { target: "noted" } },
      on: {
        ...jumps,
        CLOSE: { target: "noted" },
        HOVER_IN: { target: "held" },
        SWIPE: { target: "listening" },
        TAP_ISLAND: { target: "sources" },
      },
    },
    checking: {
      after: { [demoDelays.checking]: { target: "arrived" } },
      on: {
        ...jumps,
        SWIPE: { target: "arrived" },
        TAP_ISLAND: { target: "checkingOpen" },
      },
    },
    checkingOpen: {
      after: { [demoDelays.open]: { target: "arrived" } },
      on: {
        ...jumps,
        CLOSE: { target: "checking" },
        SWIPE: { target: "arrived" },
        TAP_ISLAND: { target: "checking" },
      },
    },
    held: {
      on: {
        ...jumps,
        CLOSE: { target: "noted" },
        HOVER_OUT: { target: "arrived" },
        SWIPE: { target: "listening" },
        TAP_ISLAND: { target: "sources" },
      },
    },
    listening: {
      after: { [demoDelays.listening]: { target: "checking" } },
      on: {
        ...jumps,
        SWIPE: { target: "arrived" },
        TAP_ISLAND: { target: "listeningOpen" },
      },
    },
    listeningOpen: {
      after: { [demoDelays.open]: { target: "checking" } },
      on: {
        ...jumps,
        CLOSE: { target: "listening" },
        SWIPE: { target: "arrived" },
        TAP_ISLAND: { target: "listening" },
      },
    },
    noted: {
      after: { [demoDelays.noted]: { target: "listening" } },
      on: {
        ...jumps,
        SWIPE: { target: "listening" },
        TAP_ISLAND: { target: "held" },
      },
    },
    sources: {
      on: {
        ...jumps,
        CLOSE: { target: "noted" },
      },
    },
  },
});

export const demoChecks = machineChecks({
  invariants: [],
  name: "demo",
  reachable: [...demoPhases],
  states: () =>
    getShortestPaths(demoMachine, {
      events: [
        { type: "SWIPE" },
        { type: "TAP_ISLAND" },
        { type: "CLOSE" },
        { type: "HOVER_IN" },
        { type: "HOVER_OUT" },
        { type: "SHOW_VIDEO" },
        { type: "SHOW_NEXT" },
        { type: "SHOW_SOURCES" },
        afterDelay("demo.listening", demoDelays.listening),
        afterDelay("demo.checking", demoDelays.checking),
        afterDelay("demo.arrived", demoDelays.arrived),
        afterDelay("demo.noted", demoDelays.noted),
        afterDelay("demo.listeningOpen", demoDelays.open),
        afterDelay("demo.checkingOpen", demoDelays.open),
      ],
      limit: 1000,
    }).map((path) => path.state),
  tagOf: ({ value }) => value,
});
