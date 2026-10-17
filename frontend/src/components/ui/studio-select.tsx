import { Children, isValidElement, type ReactNode } from "react";
import { SelectField } from "./select-field";

type OptionProps = {
  value?: string | number;
  children?: ReactNode;
  disabled?: boolean;
  label?: string;
};

function optionText(node: ReactNode): string {
  return Children.toArray(node)
    .map((child) =>
      isValidElement<OptionProps>(child)
        ? optionText(child.props.children)
        : String(child),
    )
    .join("");
}

// Accept declarative options so existing conditional lists keep their data logic.
function choices(
  children: ReactNode,
  disabled = false,
): Array<{ value: string; label: string; disabled: boolean }> {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement<OptionProps>(child)) return [];
    if (child.type === "option") {
      const text = optionText(child.props.children);
      return [
        {
          value: String(child.props.value ?? text),
          label: child.props.label ?? text,
          disabled: disabled || !!child.props.disabled,
        },
      ];
    }
    return choices(child.props.children, disabled || !!child.props.disabled);
  });
}

/** App-wide single selection. No browser/OS menu, and no synthetic change events. */
export function StudioSelect({
  children,
  value,
  onValueChange,
  disabled,
  className,
  title,
  "aria-label": label,
}: {
  children: ReactNode;
  value: string | number | undefined;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  title?: string;
  "aria-label"?: string;
}) {
  return (
    <SelectField
      label={label ?? title}
      title={title}
      value={String(value ?? "")}
      options={choices(children)}
      onChange={onValueChange}
      disabled={disabled}
      className={className}
    />
  );
}
