import { Schema } from "effect";

export const betaUrl = "https://testflight.apple.com/join/pxphnZW5";

export const BetaState = Schema.Struct({
  active: Schema.Boolean,
});

export type BetaState = typeof BetaState.Type;

export class BetaFailed extends Schema.TaggedError<BetaFailed>()("BetaFailed", {
  reason: Schema.Literals(["unavailable"]),
}) {}
