import { createEffectActor, waitFor } from "@xstate/effect";
import { Clock, Config, Context, DateTime, Effect, Layer, Option, Redacted, Ref } from "effect";

import { CheckFailed } from "#/domain/check.ts";
import type { CheckRequest, CheckResponse, RecentCheck } from "#/domain/check.ts";
import type { Researcher } from "#/domain/check-flow.machine.ts";
import { checkFlowMachine } from "#/domain/check-flow.machine.ts";

import { ResearcherLive, researcherModel } from "./researcher.ts";

const keep = 12;

export class Checker extends Context.Service<
  Checker,
  {
    readonly authorized: (header: string | null) => Effect.Effect<boolean>;
    readonly check: (request: CheckRequest) => Effect.Effect<CheckResponse, CheckFailed>;
    readonly model: string;
    readonly recent: Effect.Effect<readonly RecentCheck[]>;
  }
>()("footnote-agent/server/checker") {
  static readonly layer = Layer.effect(
    Checker,
    Effect.gen(function* checker() {
      const key = yield* Config.option(Config.Redacted("FOOTNOTE_AGENT_KEY"));
      const model = yield* researcherModel;
      const recent = yield* Ref.make<readonly RecentCheck[]>([]);
      const context = yield* Effect.context<Researcher>();

      const run = Effect.fn("Checker.run")(function* run(request: CheckRequest) {
        const actor = yield* createEffectActor(checkFlowMachine, { input: request });

        return yield* waitFor(actor, (snapshot) => snapshot.status === "done", {
          timeout: "26 seconds",
        });
      }, Effect.scoped);

      return {
        authorized: Effect.fn("Checker.authorized")(function* authorized(header: string | null) {
          return yield* Effect.sync(() => {
            if (Option.isNone(key) || header === null) {
              return false;
            }

            const given = header.replace(/^Bearer\s+/iu, "").trim();

            return given !== "" && given === Redacted.value(key.value);
          });
        }),
        check: Effect.fn("Checker.check")(function* check(request: CheckRequest) {
          const started = yield* Clock.currentTimeMillis;

          const snapshot = yield* run(request).pipe(
            Effect.provideContext(context),
            Effect.mapError((error) => new CheckFailed({ reason: String(error) })),
          );

          const ms = (yield* Clock.currentTimeMillis) - started;

          if (snapshot.value === "failed") {
            yield* Effect.logWarning(
              `check failed after ${String(ms)} ms: ${snapshot.context.failure}`,
            );

            return yield* new CheckFailed({ reason: snapshot.context.failure });
          }

          const { finding, searches } = snapshot.context;

          const entry: RecentCheck = {
            at: DateTime.formatIso(DateTime.makeUnsafe(started)),
            claim: finding.claim,
            ms,
            provider: finding.provider,
            stance: finding.stance,
          };

          yield* Ref.update(recent, (list) => [entry, ...list].slice(0, keep));
          yield* Effect.logInfo(`check ${finding.stance} in ${String(ms)} ms: ${finding.claim}`);

          return {
            claim: finding.claim,
            line: finding.line,
            model,
            ms,
            provider: finding.provider,
            searches,
            sources: finding.sources,
            stance: finding.stance,
          };
        }),
        model,
        recent: Ref.get(recent),
      };
    }),
  ).pipe(Layer.provide(ResearcherLive));
}
