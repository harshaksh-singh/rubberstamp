import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TaskPicker } from "@/components/task-picker";
import { Notice } from "@/components/ui";

import { FakeApi } from "./fake-api";

describe("notices", () => {
  it("announce failures as alerts and everything else as status", () => {
    render(
      <>
        <Notice tone="bad" title="Could not start" />
        <Notice tone="ok" title="Submitted" />
        <Notice tone="warn" title="Not in this task" />
      </>,
    );
    expect(screen.getByRole("alert").textContent).toBe("Could not start");
    expect(screen.getAllByRole("status").map((node) => node.textContent)).toEqual(["Submitted", "Not in this task"]);
  });
});

describe("TaskPicker", () => {
  it("is the page's main landmark and announces that the API is down", async () => {
    const fake = new FakeApi().install();
    fake.up = false;
    render(<TaskPicker />);

    expect(screen.getByRole("main")).toBeTruthy();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not load the tasks from rs-api");
    expect(alert.textContent).toContain("Nothing answered at");
  });
});
