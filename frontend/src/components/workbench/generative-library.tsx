import type { GenerativeUILibrary } from "@assistant-ui/generative-ui/react";
import { styledGenerativeUILibrary } from "../assistant-ui/elements/generative-ui";

// Keep the complete official vocabulary and its schemas; adapt only presentation.
export const workspaceGenerativeLibrary: GenerativeUILibrary = { ...styledGenerativeUILibrary };
for (const name of ["Input", "Select", "DatePicker"]) {
  const entry = styledGenerativeUILibrary[name];
  workspaceGenerativeLibrary[name] = {
    ...entry,
    render: props => <label className="generative-ui-field">
      {typeof props.label === "string" && <span>{props.label}</span>}
      {entry.render(props)}
    </label>,
  };
}
const button = styledGenerativeUILibrary.Button;
workspaceGenerativeLibrary.Button = {
  ...button,
  render: props => button.render({ ...props, buttonStyle: props.buttonStyle ?? "primary" }),
};
