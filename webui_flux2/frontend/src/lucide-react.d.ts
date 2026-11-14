declare module "lucide-react" {
  import type { ForwardRefExoticComponent, RefAttributes, SVGProps } from "react";

  type IconProps = SVGProps<SVGSVGElement> & {
    size?: string | number;
    color?: string;
    strokeWidth?: string | number;
  };
  type LucideIcon = ForwardRefExoticComponent<IconProps & RefAttributes<SVGSVGElement>>;

  export const Activity: LucideIcon;
  export const ArrowUpRight: LucideIcon;
  export const CircleAlert: LucideIcon;
  export const Layers3: LucideIcon;
  export const RefreshCw: LucideIcon;
  export const Server: LucideIcon;
}
