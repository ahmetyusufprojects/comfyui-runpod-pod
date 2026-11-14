import type { ButtonHTMLAttributes, PropsWithChildren } from "react";
import { cn } from "../../lib/cn";

type ButtonProps = PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & {
  variant?: "primary" | "secondary" | "ghost";
  size?: "default" | "icon";
};

export function Button({
  children,
  className,
  variant = "secondary",
  size = "default",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn("button", `button--${variant}`, size === "icon" && "button--icon", className)}
      {...props}
    >
      {children}
    </button>
  );
}
