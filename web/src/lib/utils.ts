export { cn } from "cn"

/**
 * A shadcn Button dressed as a rubber stamp. The border and outline are utilities (not only the
 * .stamp component class) so they win over the variant's own border-transparent and outline-none.
 */
export const STAMP_BUTTON =
  "stamp h-auto rounded-[3px] border-[1.5px] border-current text-primary outline outline-1 outline-offset-2 outline-current transition-transform hover:rotate-0 hover:bg-primary hover:text-primary-foreground disabled:opacity-40"
