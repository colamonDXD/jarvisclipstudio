import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createTranslator } from "next-intl";
import { describe, expect, it } from "vitest";

import { NAMESPACES, locales } from "./config";
import { loadMessages } from "./load-messages";

describe("loadMessages", () => {
  it("loads every namespace for every locale", async () => {
    for (const locale of locales) {
      const messages = await loadMessages(locale);
      expect(Object.keys(messages).sort()).toEqual([...NAMESPACES].sort());
    }
  });

  // Regression: the Music menu rendered a MISSING_MESSAGE error for
  // `home.music.auto` because its keys never reached the running app
  // (see the compose bind-mount test below). The catalog keys must
  // resolve through the real loader for every locale.
  it("resolves the home.music keys for every locale", async () => {
    for (const locale of locales) {
      const messages = await loadMessages(locale);
      const errors: Error[] = [];
      const t = createTranslator({
        locale,
        messages,
        namespace: "home",
        onError: (error) => errors.push(error),
      });
      const auto = t("music.auto");
      expect(errors, `${locale}: home.music.auto`).toEqual([]);
      expect(auto).toBeTruthy();
      expect(auto).not.toBe("music.auto");
    }
  });

  // Root-cause guard: the development frontend container bind-mounts
  // the live source trees (src, public, prisma) but historically not
  // messages/, so the dev server ran components from disk against a
  // stale catalog baked into the image. Any message-file change then
  // failed at runtime with MISSING_MESSAGE until a full image rebuild.
  it("is bind-mounted into the development frontend container", () => {
    const compose = readFileSync(join(process.cwd(), "..", "docker-compose.yml"), "utf8");
    expect(compose).toContain("./frontend/messages:/app/messages");
  });
});
