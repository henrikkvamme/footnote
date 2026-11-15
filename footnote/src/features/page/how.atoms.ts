import { createActorAtoms } from "@xstate/effect/atom";
import { Layer } from "effect";
import { Atom } from "effect/reactivity";

import { howMachine } from "#/domain/how.machine.ts";

export const how = createActorAtoms(Atom.runtime(Layer.empty), howMachine);
