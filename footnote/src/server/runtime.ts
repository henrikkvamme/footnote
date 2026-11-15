import { Effect, Layer, ManagedRuntime, Schema } from "effect";

import type { ApiResult } from "#/domain/api.ts";

import { Beta } from "./beta.ts";
import { Billing } from "./billing.ts";
import { Check } from "./check.ts";

const AppLayer = Layer.mergeAll(Layer.empty, Check.layer, Beta.layer, Billing.layer);

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
