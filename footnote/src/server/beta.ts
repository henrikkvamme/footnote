import { Clock, Context, Effect, Layer } from "effect";
import { FetchHttpClient, HttpClient, HttpClientRequest } from "effect/http";

import { betaUrl } from "#/domain/beta.ts";
import type { BetaFailed, BetaState } from "#/domain/beta.ts";

const recheckMs = 30 * 60_000;

const timeoutMs = 10_000;

const closedText = "accepting any new testers";

const appName = "Footnote";

const browser =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";

const last = { active: false, at: 0 };

const read = Effect.fn("Beta.read")(function* read() {
  const client = yield* HttpClient.HttpClient;

  const response = yield* HttpClientRequest.get(betaUrl).pipe(
    HttpClientRequest.setHeader("user-agent", browser),
    client.execute,
    Effect.timeout(timeoutMs),
  );

  const page = yield* response.text;

  return response.status === 200 && page.includes(appName) && !page.includes(closedText);
});

export class Beta extends Context.Service<
  Beta,
  {
    readonly state: () => Effect.Effect<BetaState, BetaFailed>;
  }
>()("footnote/server/beta") {
  static readonly layer = Layer.effect(
    Beta,
    Effect.gen(function* make() {
      const client = yield* HttpClient.HttpClient;

      return Beta.of({
        state: Effect.fn("Beta.state")(function* state() {
          const now = yield* Clock.currentTimeMillis;

          if (now - last.at >= recheckMs) {
            last.at = now;
            last.active = yield* read().pipe(
              Effect.provideService(HttpClient.HttpClient, client),
              Effect.orElseSucceed(() => last.active),
            );
          }

          return { active: last.active };
        }),
      });
    }),
  ).pipe(Layer.provide(FetchHttpClient.layer));
}
