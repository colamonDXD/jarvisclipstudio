import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { locales } from "./config";

// N5: the combine hint must disclose what combining drops — a source
// clip with hidden captions contributes no words, hidden audio
// contributes silence, and hidden B-roll/overlays are not carried
// (the D-C1 behaviour, correct, previously undisclosed). The clause is
// English in every locale: the other locales keep the English clause
// rather than carrying an invented translation.
const HIDDEN_LANE_CLAUSE =
  "Hidden lanes do not carry over: hidden captions contribute no words, " +
  "hidden audio contributes silence, and hidden B-roll and overlays are left out.";

const messagesRoot = join(process.cwd(), "messages");

function combineHint(locale: string): string {
  const catalog = JSON.parse(
    readFileSync(join(messagesRoot, locale, "editor.json"), "utf8"),
  ) as { workspace: { combineHint: string } };
  return catalog.workspace.combineHint;
}

describe("combine hint hidden-lane disclosure", () => {
  it("discloses the hidden-lane drops in English", () => {
    expect(combineHint("en")).toContain(HIDDEN_LANE_CLAUSE);
  });

  it("carries the same English clause in every other locale", () => {
    for (const locale of locales) {
      if (locale === "en") continue;
      expect(combineHint(locale), locale).toContain(HIDDEN_LANE_CLAUSE);
    }
  });
});
