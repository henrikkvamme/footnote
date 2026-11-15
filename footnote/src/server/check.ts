import { Clock, Context, Effect, Layer } from "effect";
import { FetchHttpClient, HttpClient, HttpClientRequest, HttpClientResponse } from "effect/http";

import { CheckFailed, CheckOutput } from "#/domain/check.ts";
import type { CheckInput } from "#/domain/check.ts";

const agentUrl = (): string =>
  process.env.FOOTNOTE_AGENT_URL?.trim() ?? "https://footnote-agent.srv2159196.hstgr.cloud";

const agentKey = (): string => process.env.FOOTNOTE_AGENT_KEY?.trim() ?? "";

const windowMs = 60_000;

const callsPerWindow = 10;

const timeoutMs = 28_000;

const recent: number[] = [];

const answers = new Map<string, CheckOutput>();

const keyOf = (text: string): string => text.trim().toLowerCase().replaceAll(/\s+/gu, " ");

const takeSlot = Effect.fn("Check.takeSlot")(function* takeSlot() {
  const now = yield* Clock.currentTimeMillis;

  while (recent.length > 0 && now - (recent[0] ?? now) > windowMs) {
    recent.shift();
  }

  if (recent.length >= callsPerWindow) {
    return yield* new CheckFailed({ reason: "busy" });
  }

  recent.push(now);
});

const ask = Effect.fn("Check.ask")(function* ask(text: string) {
  const client = yield* HttpClient.HttpClient;

  const response = yield* HttpClientRequest.post(`${agentUrl()}/api/check`).pipe(
    HttpClientRequest.acceptJson,
    HttpClientRequest.bearerToken(agentKey()),
    HttpClientRequest.bodyJsonUnsafe({ claim: text }),
    client.execute,
    Effect.timeout(timeoutMs),
    Effect.mapError(() => new CheckFailed({ reason: "unavailable" })),
  );

  if (response.status !== 200) {
    return yield* new CheckFailed({ reason: "unavailable" });
  }

  return yield* HttpClientResponse.schemaBodyJson(CheckOutput)(response).pipe(
    Effect.mapError(() => new CheckFailed({ reason: "unavailable" })),
  );
});

export class Check extends Context.Service<
  Check,
  {
    readonly run: (input: CheckInput) => Effect.Effect<CheckOutput, CheckFailed>;
  }
>()("footnote/server/check") {
  static readonly layer = Layer.effect(
    Check,
    Effect.gen(function* make() {
      const client = yield* HttpClient.HttpClient;

      return Check.of({
        run: Effect.fn("Check.run")(function* run(input: CheckInput) {
          const key = keyOf(input.text);
          const known = answers.get(key);

          if (known !== undefined) {
            return known;
          }

          yield* takeSlot();

          const answer = yield* ask(input.text.trim()).pipe(
            Effect.provideService(HttpClient.HttpClient, client),
          );

          answers.set(key, answer);

          return answer;
        }),
      });
    }),
  ).pipe(Layer.provide(FetchHttpClient.layer));
}
