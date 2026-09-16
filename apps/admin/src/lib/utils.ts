import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** shadcn/ui 标准工具：合并 className，后者覆盖前者。 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
