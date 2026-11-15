import { Effect, Option, Schema } from "effect";

import { CheckFailed, CheckRequest } from "#/domain/check.ts";
import { outOfCreditsReason } from "#/domain/research-failed.ts";
import { Checker } from "#/server/checker.ts";
import { runApi } from "#/server/runtime.ts";

const noStore = { "Cache-Control": "no-store" };

const NoError = Schema.Never;

const bodyOf = (request: Request) => request.json().catch(() => null);

export const health = async () => {
  const result = await runApi(NoError)(Checker.use((checker) => Effect.succeed(checker.model)));

  return Response.json(
    { model: result.ok ? result.value : "", ok: result.ok },
    { headers: noStore },
  );
};

export const check = async (request: Request) => {
  const allowed = await runApi(NoError)(
    Checker.use((checker) => checker.authorized(request.headers.get("authorization"))),
  );

  if (!allowed.ok || !allowed.value) {
    return Response.json({ error: "unauthorized" }, { headers: noStore, status: 401 });
  }

  const input = Schema.decodeUnknownOption(CheckRequest)(await bodyOf(request));

  if (Option.isNone(input)) {
    return Response.json(
      {
        error: "bad_request",
        message: "Expected {claim, context?, handle?, caption?, frames? (at most 2), lang?}.",
      },
      { headers: noStore, status: 400 },
    );
  }

  const result = await runApi(CheckFailed)(Checker.use((checker) => checker.check(input.value)));

  if (result.ok) {
    return Response.json(result.value, { headers: noStore });
  }

  return result.error.reason === outOfCreditsReason
    ? Response.json({ error: outOfCreditsReason }, { headers: noStore, status: 503 })
    : Response.json(
        { error: "check_failed", message: result.error.reason },
        { headers: noStore, status: 502 },
      );
};
