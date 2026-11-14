import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Each test starts with an empty page: without Vitest's globals, Testing Library can't clean up by itself.
afterEach(() => cleanup());
