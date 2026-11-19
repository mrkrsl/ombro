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

/** Every model you can choose, each with its provider's label: the one list Settings › General
 * and the crew member page both show, as "anthropic/claude-opus-5.5 (OpenRouter)". The value
 * kept is `name`, which is unique across providers, so two providers' Claudes stay apart. */
export function modelChoices(models: ModelList | null | undefined) {
  return models?.connections.flatMap((c) => c.models.map((m) => ({ ...m, label: c.label }))) ?? [];
}

export function choiceText(m: { model: string; label: string }): string {
  return `${m.model} (${m.label})`;
}
