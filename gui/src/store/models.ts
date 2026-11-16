// A model as people read it: the model's own name, not the name Ombro keeps its connection under.

import type { ModelList } from "../api/types";

export function modelLabel(model: string | null | undefined, models: ModelList | null): string {
  if (!model) return "";
  const all = models?.connections.flatMap((c) => c.models) ?? [];
  const named = all.find((m) => m.name === model);
  if (named) return named.model;
  const def = all.find((m) => m.default);
  if (model === "default") return def ? def.model : "The default";
  if (model === "cheap") return def ? `${def.model} (helpers)` : "The helpers' model";
  return model;
}
