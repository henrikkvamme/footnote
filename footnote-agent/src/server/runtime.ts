import { NodeServices } from "@effect/platform-node";
import { ConfigProvider, Effect, Layer, ManagedRuntime, Schema } from "effect";
import { FetchHttpClient } from "effect/http";

import type { ApiResult } from "#/domain/api.ts";

import { Checker } from "./checker.ts";

const localEnv = ConfigProvider.layerAdd(
  ConfigProvider.fromDotEnv({ path: ".env.local" }).pipe(
    Effect.orElseSucceed(() => ConfigProvider.fromEnvRecord({})),
  ),
);

const lensEnv = ConfigProvider.layerAdd(
  ConfigProvider.fromDotEnv({ path: "/run/lens/openrouter.env" }).pipe(
    Effect.orElseSucceed(() => ConfigProvider.fromEnvRecord({})),
  ),
);

const AppLayer = Checker.layer.pipe(
  Layer.provide(lensEnv),
  Layer.provide(localEnv),
  Layer.provide([NodeServices.layer, FetchHttpClient.layer]),
);

const runtime = ManagedRuntime.make(AppLayer);

export const runApi =
  <E, EncodedError>(errors: Schema.Codec<E, EncodedError>) =>
  <A>(
    effect: Effect.Effect<A, E, Layer.Success<typeof AppLayer>>,
  ): Promise<ApiResult<A, EncodedError>> =>
    runtime.runPromise(
      effect.pipe(
        Effect.map((value): ApiResult<A, EncodedError> => ({ ok: true, value })),
        Effect.catch((error) =>
          Schema.encodeEffect(errors)(error).pipe(
            Effect.map((encoded): ApiResult<A, EncodedError> => ({ error: encoded, ok: false })),
            Effect.orDie,
          ),
        ),
      ),
    );
