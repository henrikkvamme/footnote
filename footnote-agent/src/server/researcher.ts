import { Clock, Config, DateTime, Effect, Layer, Option, Redacted, Ref, Schema } from "effect";
import { HttpClient, HttpClientRequest } from "effect/http";

import { ResearchFailed, outOfCreditsReason } from "#/domain/research-failed.ts";
import type { Finding, Provider, ResearchPass, Source, Stance } from "#/domain/check.ts";
import { Researcher } from "#/domain/check-flow.machine.ts";

const ModelSource = Schema.Struct({
  date: Schema.optionalKey(Schema.NullOr(Schema.String)),
  title: Schema.optionalKey(Schema.NullOr(Schema.String)),
  url: Schema.String,
});

const ModelFinding = Schema.Struct({
  claim: Schema.optionalKey(Schema.NullOr(Schema.String)),
  line: Schema.optionalKey(Schema.NullOr(Schema.String)),
  sources: Schema.optionalKey(Schema.NullOr(Schema.Array(ModelSource))),
  stance: Schema.Literals(["differ", "support", "mixed", "none"]),
});

type ModelFinding = typeof ModelFinding.Type;

type ModelSource = typeof ModelSource.Type;

interface Reply {
  readonly cited: readonly ModelSource[];
  readonly provider: Provider;
  readonly text: string;
}

const Citation = Schema.Struct({
  type: Schema.String,
  url_citation: Schema.optionalKey(ModelSource),
});

const OpenRouterReply = Schema.Struct({
  usage: Schema.optionalKey(Schema.Struct({ cost: Schema.optionalKey(Schema.Finite) })),
  choices: Schema.Array(
    Schema.Struct({
      message: Schema.Struct({
        annotations: Schema.optionalKey(Schema.NullOr(Schema.Array(Citation))),
        content: Schema.NullOr(Schema.String),
      }),
    }),
  ),
});

const OutputText = Schema.Struct({ text: Schema.String, type: Schema.Literal("output_text") });

const OutputItem = Schema.Struct({
  content: Schema.optionalKey(Schema.Array(Schema.Unknown)),
  type: Schema.String,
});

const RouterReply = Schema.Struct({ output: Schema.Array(OutputItem) });

const socialSites = [
  "tiktok.com",
  "x.com",
  "twitter.com",
  "facebook.com",
  "fb.com",
  "instagram.com",
  "youtube.com",
  "youtu.be",
  "reddit.com",
  "threads.net",
];

const verdictWords =
  /\b(?:true|truly|false|falsely|debunk\w*|hoax\w*|fake\w*|lie|lies|lying|liar\w*|bogus)\b/iu;

const isoDate = /^\d{4}-\d{2}-\d{2}$/u;

const siteOf = (url: string) => {
  try {
    const parsed = new URL(url);

    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
      return Option.none<string>();
    }

    return Option.some(parsed.hostname.toLowerCase().replace(/^www\./u, ""));
  } catch {
    return Option.none<string>();
  }
};

const isSocial = (site: string) =>
  socialSites.some((social) => site === social || site.endsWith(`.${social}`));

const cleanUrl = (url: string) => {
  const parsed = new URL(url);

  parsed.searchParams.delete("utm_source");
  parsed.searchParams.delete("utm_medium");
  parsed.searchParams.delete("utm_campaign");

  return parsed.toString();
};

const withoutSlash = (url: string) => url.replace(/\/+$/u, "");

const candidatesOf = (named: readonly ModelSource[], cited: readonly ModelSource[]) => {
  const read = new Set(cited.map((source) => withoutSlash(source.url)));
  const confirmed = named.filter((source) => read.has(withoutSlash(source.url)));

  return [...confirmed, ...cited, ...(cited.length === 0 ? named : [])];
};

const sourcesOf = (candidates: readonly ModelSource[], avoidSites: readonly string[]): Source[] => {
  const seen = new Set(avoidSites);
  const sources: Source[] = [];

  for (const source of candidates) {
    const site = siteOf(source.url);

    if (Option.isSome(site) && !seen.has(site.value) && !isSocial(site.value)) {
      seen.add(site.value);
      const date = source.date ?? "";

      const base: Source = {
        site: site.value,
        title: (source.title ?? site.value).trim().slice(0, 200),
        url: cleanUrl(source.url),
      };

      sources.push(isoDate.test(date) ? { ...base, date } : base);
    }
  }

  return sources.slice(0, 4);
};

const shorten = (text: string, max: number) => {
  if (text.length <= max) {
    return text;
  }

  const cut = text.slice(0, max - 1);
  const space = cut.lastIndexOf(" ");

  return `${(space > max / 2 ? cut.slice(0, space) : cut).replace(/[\s,;:]+$/u, "")}…`;
};

const plainText = (text: string) =>
  text
    .replaceAll(/\(\s*\[[^\]]*\]\([^)]*\)\s*\)/gu, "")
    .replaceAll(/\[(?<label>[^\]]*)\]\([^)]*\)/gu, "$<label>")
    .replaceAll(/https?:\/\/\S+/gu, "")
    .replaceAll(/\s+/gu, " ")
    .replaceAll(/\s+(?<mark>[.,;:])/gu, "$<mark>")
    .trim();

const fallbackLine = (stance: Stance, sources: readonly Source[]) => {
  const names = sources.slice(0, 2).map((source) => source.site);
  const who = names.join(" and ");

  if (stance === "differ") {
    return `${who} report facts that contradict this claim.`;
  }

  if (stance === "support") {
    return `${who} report the same as this claim.`;
  }

  if (stance === "mixed") {
    return `${who} give a mixed picture of this claim.`;
  }

  return "No established sources on this claim were found.";
};

const lineOf = (text: string, stance: Stance, sources: readonly Source[]) => {
  const plain = plainText(text);

  const first = plain
    .split(/(?<=[.!?])(?<!(?:^|\s)\p{Lu}\.)\s+/u)
    .map((sentence) =>
      sentence.replace(/^[^:]{0,40}:\s*/u, (lead) => (verdictWords.test(lead) ? "" : lead)),
    )
    .find((sentence) => sentence !== "" && !verdictWords.test(sentence));

  if (first === undefined) {
    return fallbackLine(stance, sources);
  }

  const capital = `${first.charAt(0).toUpperCase()}${first.slice(1)}`;

  return shorten(capital, 140);
};

const claimOf = (modelClaim: string | null | undefined, claim: string) => {
  const text = plainText(modelClaim ?? "").replace(/[.!?]+$/u, "");

  return shorten(text === "" ? plainText(claim).replace(/[.!?]+$/u, "") : text, 60);
};

const findingOf = (model: ModelFinding, reply: Reply, pass: ResearchPass): Finding => {
  const sources = sourcesOf(candidatesOf(model.sources ?? [], reply.cited), pass.avoidSites);
  const stance: Stance = sources.length === 0 ? "none" : model.stance;

  return {
    claim: claimOf(model.claim, pass.request.claim),
    line: lineOf(model.line ?? "", stance, sources),
    provider: reply.provider,
    sources,
    stance,
  };
};

const jsonObjectIn = (text: string) => {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");

  return start === -1 || end <= start ? text : text.slice(start, end + 1);
};

const textOf = (reply: typeof RouterReply.Type) =>
  reply.output
    .filter((item) => item.type === "message")
    .flatMap((item) => item.content ?? [])
    .flatMap((part) => Option.toArray(Schema.decodeUnknownOption(OutputText)(part)))
    .map((part) => part.text)
    .join("\n");

const instructionsFor = (pass: ResearchPass, today: string) =>
  [
    `You check one claim heard in a short video. Today is ${today}.`,
    "Search the web, then judge the claim only by what established sources report: news outlets, fact-check sites, official and reference sites. Never use social media (TikTok, X, Facebook, Instagram, YouTube, Reddit) as a source.",
    pass.pass === 2
      ? `Find independent established sources on other sites than: ${pass.avoidSites.join(", ")}.`
      : "Search once. Search a second time only when the first search gives nothing usable.",
    "Reply with one JSON object only, no markdown and no other text:",
    '{"claim": string, "stance": "differ"|"support"|"mixed"|"none", "line": string, "sources": [{"title": string, "url": string, "date": "YYYY-MM-DD" or null}]}',
    'claim: the claim as one short, complete, neutral statement of at most 60 characters, such as "Zelenskyy owns a $20 million mansion in Florida".',
    "stance: differ when established sources contradict the claim; support when they confirm it; mixed when they disagree with each other or the claim is partly right; none when there are no usable sources, or the claim is an opinion or cannot be checked.",
    'line: one sentence, at most 140 characters, that says what the sources report and names them, such as "USA TODAY and Lead Stories report that the house belongs to other owners." Never use the words true, false, debunked, hoax, fake, lie, or any other verdict word. No links in the line.',
    "sources: 1 to 4 pages you found with the web search, each on a different site, with the exact URL. A differ stance needs at least 2 different sites. date: the publication date when you know it, else null.",
    pass.request.lang !== undefined && pass.request.lang !== "en"
      ? `Write claim and line in the language with the code "${pass.request.lang}".`
      : "Write claim and line in English.",
  ].join("\n");

const inputFor = (pass: ResearchPass) =>
  [
    `Claim: ${pass.request.claim}`,
    pass.request.handle === undefined ? "" : `Account: ${pass.request.handle}`,
    pass.request.caption === undefined
      ? ""
      : `Video caption: ${pass.request.caption.slice(0, 800)}`,
    pass.request.context === undefined
      ? ""
      : `Transcript around the claim: ${pass.request.context.slice(0, 2000)}`,
  ]
    .filter((line) => line !== "")
    .join("\n");

const failed = (reason: string) => new ResearchFailed({ outOfCredits: false, reason });

const outOfCredits = () => new ResearchFailed({ outOfCredits: true, reason: outOfCreditsReason });

const hostingerPauseMs = 10 * 60 * 1000;

const userAgent = "footnote-agent/1";

export const ResearcherLive = Layer.effect(
  Researcher,
  Effect.gen(function* researcher() {
    const http = yield* HttpClient.HttpClient;
    const key = yield* Config.option(Config.Redacted("AI_ROUTER_API_KEY"));

    const baseUrl = yield* Config.String("AI_ROUTER_BASE_URL").pipe(
      Config.withDefault("https://router.hostinger.com/v1"),
    );

    const model = yield* Config.String("AGENT_MODEL").pipe(Config.withDefault("gpt-6-luna"));
    const effort = yield* Config.String("AGENT_REASONING").pipe(Config.withDefault("none"));
    const openRouterKey = yield* Config.option(Config.Redacted("OPENROUTER_API_KEY"));

    const openRouterModel = yield* Config.String("OPENROUTER_MODEL").pipe(
      Config.withDefault("openai/gpt-6-luna"),
    );

    const hostingerPausedUntil = yield* Ref.make(0);

    const post = Effect.fn("Researcher.post")(function* post(
      url: string,
      token: string,
      body: string,
      label: string,
    ) {
      const response = yield* http
        .execute(
          HttpClientRequest.post(url).pipe(
            HttpClientRequest.bearerToken(token),
            HttpClientRequest.setHeader("User-Agent", userAgent),
            HttpClientRequest.bodyText(body, "application/json"),
          ),
        )
        .pipe(
          Effect.timeout("22 seconds"),
          Effect.mapError((error) => failed(`${label} call failed: ${String(error)}`)),
        );

      const reply = yield* response.text.pipe(
        Effect.mapError(() => failed(`${label} reply could not be read.`)),
      );

      if (response.status === 402 || response.status === 429) {
        return yield* outOfCredits();
      }

      if (response.status >= 300) {
        return yield* failed(
          `${label} answered HTTP ${String(response.status)}: ${reply.slice(0, 300)}`,
        );
      }

      return reply;
    });

    const askHostinger = Effect.fn("Researcher.askHostinger")(function* askHostinger(
      pass: ResearchPass,
      today: string,
    ) {
      if (Option.isNone(key)) {
        return yield* failed("AI_ROUTER_API_KEY is not set.");
      }

      const body = JSON.stringify({
        input: inputFor(pass),
        instructions: instructionsFor(pass, today),
        model,
        reasoning: { effort },
        tools: [{ type: "web_search" }],
      });

      const reply = yield* post(
        `${baseUrl}/responses`,
        Redacted.value(key.value),
        body,
        "The AI Router",
      );

      const decoded = yield* Schema.decodeEffect(Schema.fromJsonString(RouterReply))(reply).pipe(
        Effect.mapError(() => failed("The AI Router reply is not in the Responses format.")),
      );

      const answer: Reply = { cited: [], provider: "hostinger", text: textOf(decoded) };

      return answer;
    });

    const askOpenRouter = Effect.fn("Researcher.askOpenRouter")(function* askOpenRouter(
      pass: ResearchPass,
      today: string,
    ) {
      if (Option.isNone(openRouterKey)) {
        return yield* outOfCredits();
      }

      const body = JSON.stringify({
        max_tokens: 1200,
        messages: [
          { content: instructionsFor(pass, today), role: "system" },
          { content: inputFor(pass), role: "user" },
        ],
        model: openRouterModel,
        plugins: [{ engine: "exa", id: "web", max_results: 3 }],
        reasoning: { effort, exclude: true },
        response_format: { type: "json_object" },
        usage: { include: true },
      });

      const reply = yield* post(
        "https://openrouter.ai/api/v1/chat/completions",
        Redacted.value(openRouterKey.value),
        body,
        "OpenRouter",
      );

      const decoded = yield* Schema.decodeEffect(Schema.fromJsonString(OpenRouterReply))(
        reply,
      ).pipe(Effect.mapError(() => failed("The OpenRouter reply is not a chat completion.")));

      const message = decoded.choices[0]?.message;

      yield* Effect.logInfo(
        `OpenRouter pass ${String(pass.pass)} cost ${String(decoded.usage?.cost ?? "unknown")} USD`,
      );

      const cited = (message?.annotations ?? []).flatMap((annotation) =>
        annotation.type === "url_citation" && annotation.url_citation !== undefined
          ? [annotation.url_citation]
          : [],
      );

      const answer: Reply = { cited, provider: "openrouter", text: message?.content ?? "" };

      return answer;
    });

    const ask = Effect.fn("Researcher.ask")(function* ask(pass: ResearchPass, today: string) {
      const now = yield* Clock.currentTimeMillis;

      if (now < (yield* Ref.get(hostingerPausedUntil))) {
        return yield* askOpenRouter(pass, today);
      }

      return yield* askHostinger(pass, today).pipe(
        Effect.catchIf(
          (error) => error.outOfCredits,
          () =>
            Effect.logWarning(
              "Hostinger AI Router has no credits; OpenRouter answers for 10 min",
            ).pipe(
              Effect.andThen(Ref.set(hostingerPausedUntil, now + hostingerPauseMs)),
              Effect.andThen(askOpenRouter(pass, today)),
            ),
        ),
      );
    });

    return {
      research: Effect.fn("Researcher.research")(
        function* research(pass: ResearchPass) {
          const reply = yield* ask(pass, DateTime.formatIsoDateUtc(yield* DateTime.now));

          const finding = yield* Schema.decodeEffect(Schema.fromJsonString(ModelFinding))(
            jsonObjectIn(reply.text),
          ).pipe(
            Effect.mapError(() =>
              failed(`The model reply is not the JSON finding: ${reply.text.slice(0, 200)}`),
            ),
          );

          return findingOf(finding, reply, pass);
        },
        Effect.tapError((error) => Effect.logWarning(`research pass failed: ${error.reason}`)),
      ),
    };
  }),
);

export const researcherModel = Config.String("AGENT_MODEL").pipe(Config.withDefault("gpt-6-luna"));
