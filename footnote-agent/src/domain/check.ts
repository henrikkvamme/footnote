import { Schema } from "effect";

export const Stance = Schema.Literals(["differ", "support", "mixed", "none"]);

export type Stance = typeof Stance.Type;

const optionalText = (max: number) =>
  Schema.optionalKey(Schema.String.check(Schema.isMaxLength(max)));

export const Provider = Schema.Literals(["hostinger", "openrouter"]);

export type Provider = typeof Provider.Type;

export const CheckRequest = Schema.Struct({
  caption: optionalText(4000),
  claim: Schema.String.check(Schema.isMinLength(3), Schema.isMaxLength(1000)),
  context: optionalText(8000),
  frames: Schema.optionalKey(Schema.Array(Schema.String).check(Schema.isMaxLength(2))),
  handle: optionalText(200),
  lang: optionalText(20),
});

export type CheckRequest = typeof CheckRequest.Type;

export const Source = Schema.Struct({
  date: Schema.optionalKey(Schema.String),
  site: Schema.String,
  title: Schema.String,
  url: Schema.String,
});

export type Source = typeof Source.Type;

export const Finding = Schema.Struct({
  claim: Schema.String,
  line: Schema.String,
  provider: Provider,
  sources: Schema.Array(Source),
  stance: Stance,
});

export type Finding = typeof Finding.Type;

export const CheckResponse = Schema.Struct({
  claim: Schema.String,
  line: Schema.String,
  model: Schema.String,
  ms: Schema.Finite,
  provider: Provider,
  searches: Schema.Finite,
  sources: Schema.Array(Source),
  stance: Stance,
});

export type CheckResponse = typeof CheckResponse.Type;

export const RecentCheck = Schema.Struct({
  at: Schema.String,
  claim: Schema.String,
  ms: Schema.Finite,
  provider: Provider,
  stance: Stance,
});

export type RecentCheck = typeof RecentCheck.Type;

export const ResearchPass = Schema.Struct({
  avoidSites: Schema.Array(Schema.String),
  pass: Schema.Literals([1, 2]),
  request: CheckRequest,
});

export type ResearchPass = typeof ResearchPass.Type;

export class CheckFailed extends Schema.TaggedError<CheckFailed>()("CheckFailed", {
  reason: Schema.String,
}) {}

export const siteCount = (sources: readonly Source[]) =>
  new Set(sources.map((source) => source.site)).size;

export const mergeSources = (first: readonly Source[], second: readonly Source[]) => {
  const seen = new Set<string>();

  return [...first, ...second]
    .filter((source) => {
      if (seen.has(source.site)) {
        return false;
      }

      seen.add(source.site);

      return true;
    })
    .slice(0, 4);
};

export const emptyFinding = (claim: string): Finding => ({
  claim,
  line: "No established sources on this claim were found.",
  provider: "hostinger",
  sources: [],
  stance: "none",
});
