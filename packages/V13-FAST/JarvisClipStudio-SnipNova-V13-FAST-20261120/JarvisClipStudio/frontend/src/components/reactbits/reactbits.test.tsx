import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppBackground } from "./AppBackground";
import { GradientText } from "./GradientText";
import { ShinyText } from "./ShinyText";
import { SpotlightCard } from "./SpotlightCard";

describe("AppBackground", () => {
  it("renders a decorative, non-interactive layer", () => {
    const { container } = render(<AppBackground />);
    const layer = container.querySelector("[data-app-background]");
    expect(layer).not.toBeNull();
    expect(layer).toHaveAttribute("aria-hidden", "true");
    expect(layer).toHaveClass("app-bg");
    // Galaxy starfield: exactly one decorative canvas, no aurora blobs.
    expect(layer?.querySelectorAll("canvas.app-bg__galaxy")).toHaveLength(1);
    expect(layer?.querySelectorAll(".app-bg__blob")).toHaveLength(0);
  });
});

describe("GradientText", () => {
  it("renders its children with the gradient class", () => {
    render(<GradientText>Nova</GradientText>);
    const el = screen.getByText("Nova");
    expect(el).toHaveClass("rb-gradient-text");
  });
});

describe("ShinyText", () => {
  it("renders its children with the shiny class", () => {
    render(<ShinyText>Snip</ShinyText>);
    const el = screen.getByText("Snip");
    expect(el).toHaveClass("rb-shiny-text");
  });
});

describe("SpotlightCard", () => {
  it("renders children and tracks the pointer via CSS variables", () => {
    const { container } = render(
      <SpotlightCard>
        <p>Card body</p>
      </SpotlightCard>,
    );
    expect(screen.getByText("Card body")).toBeInTheDocument();
    const el = container.firstElementChild as HTMLElement;
    expect(el).toHaveClass("rb-spotlight");

    fireEvent.mouseMove(el, { clientX: 24, clientY: 12 });
    expect(el.style.getPropertyValue("--spot-x")).toBe("24px");
    expect(el.style.getPropertyValue("--spot-y")).toBe("12px");
    expect(el.style.getPropertyValue("--spot-opacity")).toBe("1");

    fireEvent.mouseLeave(el);
    expect(el.style.getPropertyValue("--spot-opacity")).toBe("0");
  });
});
