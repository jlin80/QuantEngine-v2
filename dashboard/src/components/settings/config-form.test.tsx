import "@testing-library/jest-dom/vitest";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Field } from "./config-form";
import type { ConfigEntry } from "@/lib/api/types";

function entry(over: Partial<ConfigEntry>): ConfigEntry {
  return { value: null, default: null, overridden: false, kind: "string", live: true, ...over };
}

describe("Field", () => {
  it("renders a dict as editable JSON, never as [object Object]", () => {
    // El fallo real: el control se elegía con `typeof entry.default`, y un dict
    // caía al input de texto con `String(value)`. Se mostraba "[object Object]"
    // y guardarlo escribía esa cadena sobre `execution.symbols_enabled`.
    render(
      <Field
        path="execution.symbols_enabled"
        entry={entry({ kind: "dict", value: { XAUUSDM: true }, default: {} })}
        value={{ XAUUSDM: true }}
        onChange={vi.fn()}
      />,
    );
    const box = screen.getByRole("textbox");
    expect(box).toHaveValue('{\n  "XAUUSDM": true\n}');
    expect(screen.queryByDisplayValue("[object Object]")).not.toBeInTheDocument();
  });

  it("only propagates a dict edit once it parses", () => {
    const onChange = vi.fn();
    render(
      <Field
        path="execution.symbols_enabled"
        entry={entry({ kind: "dict", value: {}, default: {} })}
        value={{}}
        onChange={onChange}
      />,
    );
    const box = screen.getByRole("textbox");

    fireEvent.change(box, { target: { value: '{"XAUUSDM": ' } });
    expect(onChange).not.toHaveBeenCalled();

    fireEvent.change(box, { target: { value: '{"XAUUSDM": true}' } });
    expect(onChange).toHaveBeenCalledWith({ XAUUSDM: true });
  });

  it("flags a field the engine only reads at startup", () => {
    render(
      <Field
        path="execution.trailing_enabled"
        entry={entry({ kind: "bool", value: true, default: true, live: false })}
        value={true}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText("restart")).toBeInTheDocument();
  });

  it("does not flag a field that applies live", () => {
    render(
      <Field
        path="execution.risk.max_open_positions"
        entry={entry({ kind: "number", value: 5, default: 5, live: true })}
        value={5}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByText("restart")).not.toBeInTheDocument();
  });
});
