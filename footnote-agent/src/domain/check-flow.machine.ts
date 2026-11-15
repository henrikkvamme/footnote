import { EffectFailure, createEffectLogic, setupEffect } from "@xstate/effect";
import { getShortestPaths } from "xstate/graph";
import { Cause, Context, Schema } from "effect";
import type { Effect } from "effect";

import {
  CheckRequest,
  Finding,
  ResearchPass,
  emptyFinding,
  mergeSources,
  siteCount,
} from "./check.ts";
import { ResearchFailed, failureReason } from "./research-failed.ts";
import { actorDone, actorFailed, afterDelay, machineChecks } from "./machine-checks.ts";

export class Researcher extends Context.Service<
  Researcher,
  {
    readonly research: (pass: ResearchPass) => Effect.Effect<Finding, ResearchFailed>;
  }
>()("footnote-agent/domain/check-flow.machine/Researcher") {}

export const firstPassMs = 17_000;

export const secondPassMs = 7000;

const research = createEffectLogic({
  effect: ({ input }) => Researcher.use((researcher) => researcher.research(input)),
  schemas: { input: ResearchPass },
});

const needsMoreSites = (finding: Finding) =>
  finding.stance === "differ" && siteCount(finding.sources) < 2;

const settled = (finding: Finding): Finding =>
  needsMoreSites(finding) ? { ...finding, stance: "mixed" } : finding;

export const checkFlowMachine = setupEffect({
  actors: { research },
  schemas: {
    context: Schema.Struct({
      failure: Schema.String,
      finding: Finding,
      request: CheckRequest,
      searches: Schema.Finite,
    }),
    input: CheckRequest,
  },
}).createMachine({
  context: ({ input }) => ({
    failure: "",
    finding: emptyFinding(input.claim),
    request: input,
    searches: 0,
  }),
  id: "checkFlow",
  initial: "searching",
  states: {
    done: { type: "final" },
    failed: { type: "final" },
    searching: {
      after: {
        [firstPassMs]: ({ context }) => ({
          context: { ...context, failure: "The web search took too long." },
          target: "failed",
        }),
      },
      invoke: {
        id: "firstPass",
        input: ({ context }) => ({ avoidSites: [], pass: 1, request: context.request }),
        onDone: ({ context, event }) => ({
          context: { ...context, finding: event.output, searches: 1 },
          target: needsMoreSites(event.output) ? "secondPass" : "done",
        }),
        onError: ({ context, event }) => ({
          context: { ...context, failure: failureReason(event.error) },
          target: "failed",
        }),
        src: "research",
      },
    },
    secondPass: {
      after: {
        [secondPassMs]: ({ context }) => ({
          context: { ...context, finding: settled(context.finding) },
          target: "done",
        }),
      },
      invoke: {
        id: "secondPass",
        input: ({ context }) => ({
          avoidSites: context.finding.sources.map((source) => source.site),
          pass: 2,
          request: context.request,
        }),
        onDone: ({ context, event }) => ({
          context: {
            ...context,
            finding: settled({
              ...context.finding,
              sources: mergeSources(context.finding.sources, event.output.sources),
            }),
            searches: 2,
          },
          target: "done",
        }),
        onError: ({ context }) => ({
          context: { ...context, finding: settled(context.finding), searches: 2 },
          target: "done",
        }),
        src: "research",
      },
    },
  },
});

const site = (name: string) => ({
  site: name,
  title: name,
  url: `https://${name}/a`,
});

const sampleRequest: CheckRequest = { claim: "A sample claim." };

export const checkFlowChecks = machineChecks({
  invariants: [
    {
      holds: ({ context, value }) =>
        value !== "done" ||
        context.finding.stance !== "differ" ||
        siteCount(context.finding.sources) >= 2,
      name: "a differ finding has at least two sites",
    },
    {
      holds: ({ context, value }) => value !== "failed" || context.failure !== "",
      name: "a failed check has a reason",
    },
    {
      holds: ({ context }) => context.finding.sources.length <= 4,
      name: "a finding has at most four sources",
    },
  ],
  name: "checkFlow",
  reachable: ["searching", "secondPass", "done", "failed"],
  states: () =>
    getShortestPaths(checkFlowMachine, {
      events: [
        actorDone("firstPass", {
          claim: "c",
          line: "l",
          provider: "hostinger",
          sources: [site("a.com"), site("b.com")],
          stance: "differ",
        }),
        actorDone("firstPass", {
          claim: "c",
          line: "l",
          provider: "hostinger",
          sources: [site("a.com")],
          stance: "differ",
        }),
        actorDone("firstPass", {
          claim: "c",
          line: "l",
          provider: "openrouter",
          sources: [],
          stance: "none",
        }),
        actorFailed(
          "firstPass",
          new EffectFailure(Cause.fail(new ResearchFailed({ outOfCredits: true, reason: "402" }))),
        ),
        actorFailed(
          "firstPass",
          new EffectFailure(Cause.fail(new ResearchFailed({ outOfCredits: false, reason: "500" }))),
        ),
        actorDone("secondPass", {
          claim: "c",
          line: "l",
          provider: "hostinger",
          sources: [site("b.com")],
          stance: "differ",
        }),
        actorDone("secondPass", {
          claim: "c",
          line: "l",
          provider: "openrouter",
          sources: [],
          stance: "none",
        }),
        actorFailed("secondPass"),
        afterDelay("checkFlow.searching", firstPassMs),
        afterDelay("checkFlow.secondPass", secondPassMs),
      ],
      input: sampleRequest,
      limit: 1000,
    }).map((path) => path.state),
  tagOf: ({ value }) => value,
});
