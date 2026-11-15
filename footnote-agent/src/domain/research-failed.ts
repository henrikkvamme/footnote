import type { EffectFailure } from "@xstate/effect";
import { Cause, Option, Schema } from "effect";

export class ResearchFailed extends Schema.TaggedError<ResearchFailed>()("ResearchFailed", {
  outOfCredits: Schema.Boolean,
  reason: Schema.String,
}) {}

export const outOfCreditsReason = "router: insufficient credits";

export const failureReason = (error: EffectFailure<ResearchFailed> | undefined) => {
  if (error === undefined) {
    return "The web search failed.";
  }

  const found = Cause.findErrorOption(error.cause);

  return Option.isSome(found) && found.value.outOfCredits
    ? outOfCreditsReason
    : "The web search failed.";
};
