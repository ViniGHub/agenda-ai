// Adapted from Skiper UI, component "skiper40" (CssLink): https://skiper-ui.com
// Free version, used with attribution. Changes: a <button> instead of a Next.js link, no arrow icon,
// and the underline also draws on keyboard focus.
import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

/** Text button whose underline draws itself from the left on hover, like a pen stroke. */
export function InkLink({ className, children, type = 'button', ...props }: ComponentProps<'button'>) {
  return (
    <button
      type={type}
      className={cn(
        'relative inline text-left outline-none',
        "before:pointer-events-none before:absolute before:-bottom-0.5 before:left-0 before:h-[0.09em] before:w-full before:bg-current before:content-['']",
        'before:origin-right before:scale-x-0 before:transition-transform before:duration-300 before:ease-[cubic-bezier(0.4,0,0.2,1)]',
        'hover:before:origin-left hover:before:scale-x-100 focus-visible:before:origin-left focus-visible:before:scale-x-100',
        'disabled:pointer-events-none disabled:opacity-50 motion-reduce:before:transition-none',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}
