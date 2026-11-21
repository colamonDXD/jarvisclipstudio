// layout-picker.test.tsx — NEW: unit tests for the LayoutPicker component.
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { LayoutPicker } from "./layout-picker";

it("renders all five layout option cards", () => {
  render(<LayoutPicker value="auto" onChange={vi.fn()} />);

  expect(screen.getByRole("radio", { name: "Auto · Picks per clip" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Face on top · Red divider" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Face on top · Gold divider" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Full screen · Whole picture" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Crop · Zoom on the face" })).toBeInTheDocument();
});

it("marks the current value as checked", () => {
  render(<LayoutPicker value="facecam_gold" onChange={vi.fn()} />);

  expect(screen.getByRole("radio", { name: "Face on top · Gold divider" })).toHaveAttribute(
    "aria-checked",
    "true"
  );
  expect(screen.getByRole("radio", { name: "Auto · Picks per clip" })).toHaveAttribute(
    "aria-checked",
    "false"
  );
});

it("calls onChange with the picked value when a card is clicked", () => {
  const onChange = vi.fn();
  render(<LayoutPicker value="auto" onChange={onChange} />);

  fireEvent.click(screen.getByRole("radio", { name: "Full screen · Whole picture" }));
  expect(onChange).toHaveBeenCalledWith("fit");

  fireEvent.click(screen.getByRole("radio", { name: "Crop · Zoom on the face" }));
  expect(onChange).toHaveBeenCalledWith("crop");

  fireEvent.click(screen.getByRole("radio", { name: "Face on top · Red divider" }));
  expect(onChange).toHaveBeenCalledWith("facecam_red");

  fireEvent.click(screen.getByRole("radio", { name: "Face on top · Gold divider" }));
  expect(onChange).toHaveBeenCalledWith("facecam_gold");
});

it("calls onChange with 'auto' when Auto is clicked", () => {
  const onChange = vi.fn();
  render(<LayoutPicker value="fit" onChange={onChange} />);

  fireEvent.click(screen.getByRole("radio", { name: "Auto · Picks per clip" }));
  expect(onChange).toHaveBeenCalledWith("auto");
});

it("disables all cards when disabled prop is set", () => {
  render(<LayoutPicker value="auto" onChange={vi.fn()} disabled />);

  const cards = screen.getAllByRole("radio");
  expect(cards).toHaveLength(5);
  cards.forEach((card) => expect(card).toBeDisabled());
});

it("does not call onChange when disabled and clicked", () => {
  const onChange = vi.fn();
  render(<LayoutPicker value="auto" onChange={onChange} disabled />);

  fireEvent.click(screen.getByRole("radio", { name: "Full screen · Whole picture" }));
  expect(onChange).not.toHaveBeenCalled();
});

it("has a radiogroup container labelled 'Layout'", () => {
  render(<LayoutPicker value="auto" onChange={vi.fn()} />);
  expect(screen.getByRole("radiogroup", { name: "Layout" })).toBeInTheDocument();
});
