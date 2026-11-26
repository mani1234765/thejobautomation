import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./button";
import { ChipGroup, TagInput } from "./controls";
import { ScoreRing } from "./score";
import { EmptyState, ErrorState } from "./states";
import { Search } from "lucide-react";

describe("ScoreRing", () => {
  it("labels a real score", () => {
    render(<ScoreRing score={82} />);
    expect(screen.getByRole("img", { name: "Match score 82 out of 100" })).toBeInTheDocument();
  });
  it("shows an explicit not-scored state instead of 0", () => {
    render(<ScoreRing score={null} />);
    expect(screen.getByRole("img", { name: "Not scored" })).toHaveTextContent("–");
  });
});

describe("Button", () => {
  it("is disabled and busy while loading", async () => {
    const onClick = vi.fn();
    render(<Button loading onClick={onClick}>Save</Button>);
    const btn = screen.getByRole("button", { name: "Save" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("aria-busy", "true");
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });
});

function TagHarness() {
  const [v, setV] = useState<string[]>(["Python"]);
  return <TagInput value={v} onChange={setV} placeholder="Add" id="t" />;
}

describe("TagInput", () => {
  it("adds on Enter, ignores duplicates and removes tags", async () => {
    render(<TagHarness />);
    const input = document.getElementById("t")!;
    await userEvent.type(input, "Go{Enter}python{Enter}");
    expect(screen.getByText("Go")).toBeInTheDocument();
    expect(screen.getAllByText(/python/i)).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Remove Go" }));
    expect(screen.queryByText("Go")).not.toBeInTheDocument();
  });
});

describe("ChipGroup", () => {
  it("toggles with aria-pressed", async () => {
    function H() {
      const [v, setV] = useState<string[]>([]);
      return <ChipGroup ariaLabel="Modes" value={v} onChange={setV} options={[{ value: "remote", label: "Remote" }]} />;
    }
    render(<H />);
    const chip = screen.getByRole("button", { name: "Remote" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
  });
});

describe("states", () => {
  it("renders empty and error states with actions", async () => {
    const retry = vi.fn();
    render(<><EmptyState icon={Search} title="Nothing here" description="Try again" /><ErrorState error={new Error("Boom")} onRetry={retry} /></>);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Boom");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalled();
  });
});
