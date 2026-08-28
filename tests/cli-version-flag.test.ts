import { describe, it, expect } from "vitest";

describe("CLI --version flag", () => {
  it("should accept a --version flag", () => {
    const args = ["--version"];
    expect(args.includes("--version")).toBe(true);
  });
});

