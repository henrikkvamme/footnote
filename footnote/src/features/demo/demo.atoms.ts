import { createActorAtoms } from "@xstate/effect/atom";
import { Layer } from "effect";
import { Atom } from "effect/reactivity";

import { demoMachine } from "#/domain/demo.machine.ts";

const runtime = Atom.runtime(Layer.empty);

export const demo = createActorAtoms(runtime, demoMachine);
