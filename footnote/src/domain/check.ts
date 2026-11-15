import { Schema } from "effect";

export const CheckInput = Schema.Struct({
  text: Schema.String.check(Schema.isMinLength(8), Schema.isMaxLength(300)),
});

export type CheckInput = typeof CheckInput.Type;

export const Stance = Schema.Literals(["differ", "support", "mixed", "none"]);

export type Stance = typeof Stance.Type;

export const Source = Schema.Struct({
  date: Schema.optional(Schema.String),
  site: Schema.String,
  title: Schema.String,
  url: Schema.String,
});

export type Source = typeof Source.Type;

export const CheckOutput = Schema.Struct({
  claim: Schema.String,
  line: Schema.String,
  ms: Schema.Finite,
  sources: Schema.Array(Source),
  stance: Stance,
});

export type CheckOutput = typeof CheckOutput.Type;

export class CheckFailed extends Schema.TaggedError<CheckFailed>()("CheckFailed", {
  reason: Schema.Literals(["busy", "unavailable"]),
}) {}

export const stanceLine: Record<Stance, string> = {
  differ: "Sources contradict this.",
  mixed: "Sources are mixed.",
  none: "No sources found.",
  support: "Sources support this.",
};
