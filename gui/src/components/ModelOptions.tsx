// The <option>s of a model choice: the same list wherever a model is picked (Settings › General,
// a crew member's page), each with its provider in brackets.

import type { ModelList } from "../api/types";
import { choiceText, modelChoices } from "../store/models";

export function ModelOptions({ models }: { models: ModelList | null | undefined }) {
  return (
    <>
      {modelChoices(models).map((m) => (
        <option key={m.name} value={m.name}>
          {choiceText(m)}
        </option>
      ))}
    </>
  );
}
