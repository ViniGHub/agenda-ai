// Adapted from Skiper UI, component "skiper106" (Smooth caret input) by gxuri: https://skiper-ui.com
// Free version, used with attribution. Changes: removed the DialKit tuning panel (fixed spring as a
// constant), text-only, focus handling, and styling hooks for the notebook theme.
import { motion, useMotionValue, useReducedMotion, useSpring } from 'framer-motion'
import { type ComponentPropsWithoutRef, useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'

const SPRING = { stiffness: 500, damping: 30, mass: 0.5 }
const INSTANT = { stiffness: 10000, damping: 100, mass: 0.1 }

type SmoothInputProps = Omit<ComponentPropsWithoutRef<'input'>, 'type'> & {
  wrapperClassName?: string
  caretClassName?: string
}

/** A text input whose caret glides to the typing position instead of jumping. */
export function SmoothInput({
  className,
  wrapperClassName,
  caretClassName,
  value,
  defaultValue,
  onChange,
  onBlur,
  onFocus,
  style,
  ...props
}: SmoothInputProps) {
  const [internalValue, setInternalValue] = useState(defaultValue ?? '')
  const caretX = useMotionValue(0)
  const caretOpacity = useMotionValue(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const measureRef = useRef<HTMLSpanElement>(null)
  const springCaretX = useSpring(caretX, useReducedMotion() ? INSTANT : SPRING)
  const inputValue = value !== undefined ? String(value) : String(internalValue)

  // Measures the text before the caret with an invisible span that copies the input's font.
  const measurePrefixWidth = (text: string) => {
    const input = inputRef.current
    const span = measureRef.current
    if (!input || !span) return null
    const styles = window.getComputedStyle(input)
    span.style.font = `${styles.fontStyle} ${styles.fontWeight} ${styles.fontSize} ${styles.fontFamily}`
    span.style.letterSpacing = styles.letterSpacing
    span.style.fontFeatureSettings = styles.fontFeatureSettings
    span.style.fontVariationSettings = styles.fontVariationSettings
    span.textContent = text
    const paddingLeft = parseFloat(styles.paddingLeft) || 0
    return text.length > 0 ? span.offsetWidth + paddingLeft : paddingLeft - 1
  }

  const updateCaret = (target: HTMLInputElement) => {
    const start = target.selectionStart ?? 0
    const end = target.selectionEnd ?? 0
    const caretIndex = start === end ? start : target.selectionDirection === 'backward' ? start : end
    const absolute = measurePrefixWidth(target.value.slice(0, caretIndex))
    if (absolute === null) return

    const styles = window.getComputedStyle(target)
    const paddingLeft = parseFloat(styles.paddingLeft) || 0
    const paddingRight = parseFloat(styles.paddingRight) || 0
    // Keep the caret inside the visible part of a long, horizontally scrolled value.
    if (absolute > target.scrollLeft + target.clientWidth - paddingRight) {
      target.scrollLeft = absolute - target.clientWidth + paddingRight
    } else if (absolute < target.scrollLeft + paddingLeft) {
      target.scrollLeft = Math.max(0, absolute - paddingLeft)
    }
    const position = absolute - target.scrollLeft
    const maxX = target.clientWidth - paddingRight
    caretX.set(Math.min(position, maxX))
    caretOpacity.set(start !== end || position < paddingLeft - 1 || position > maxX + 1 ? 0 : 1)
  }

  const updateRef = useRef(updateCaret)
  updateRef.current = updateCaret

  useEffect(() => {
    const input = inputRef.current
    if (input && document.activeElement === input) updateRef.current(input)
  }, [inputValue])

  useEffect(() => {
    const input = inputRef.current
    const container = containerRef.current
    if (!input || !container) return
    const ifFocused = () => {
      if (document.activeElement === input) updateRef.current(input)
    }
    const onSelection = () => requestAnimationFrame(ifFocused)
    document.addEventListener('selectionchange', onSelection)
    document.fonts.addEventListener('loadingdone', ifFocused)
    void document.fonts.ready.then(ifFocused)
    input.addEventListener('scroll', ifFocused)
    const observer = new ResizeObserver(ifFocused)
    observer.observe(container)
    return () => {
      document.removeEventListener('selectionchange', onSelection)
      document.fonts.removeEventListener('loadingdone', ifFocused)
      input.removeEventListener('scroll', ifFocused)
      observer.disconnect()
    }
  }, [])

  return (
    <div className={cn('relative w-full', wrapperClassName)}>
      <div ref={containerRef} className="relative grid grid-cols-1" style={{ caretColor: 'transparent' }}>
        <input
          {...props}
          ref={inputRef}
          type="text"
          value={inputValue}
          style={style}
          className={cn(
            'col-start-1 row-start-1 w-full bg-transparent outline-none placeholder:text-foreground/40',
            className,
          )}
          onChange={(event) => {
            if (value === undefined) setInternalValue(event.target.value)
            onChange?.(event)
            const target = event.target
            requestAnimationFrame(() => updateRef.current(target))
          }}
          onFocus={(event) => {
            updateRef.current(event.target)
            onFocus?.(event)
          }}
          onBlur={(event) => {
            caretOpacity.set(0)
            onBlur?.(event)
          }}
        />
        <span
          ref={measureRef}
          aria-hidden
          className="pointer-events-none invisible absolute top-0 left-0 whitespace-pre"
        />
        <motion.div
          aria-hidden
          className={cn('pointer-events-none col-start-1 row-start-1 h-[0.9em] w-0.5 self-center bg-primary', caretClassName)}
          style={{ x: springCaretX, opacity: caretOpacity }}
        />
      </div>
    </div>
  )
}
