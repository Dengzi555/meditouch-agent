import { useEffect, useRef, useState } from 'react';
import type { Card } from './protocol';
import { cardText, revealCard } from './streamedCard';

export function useStreamedCard(item: Card, loading: boolean, animate = loading) {
  const text = cardText(item);
  const length = Array.from(text).length;
  // Stored completed conversations render immediately; only a live response animates.
  const live = useRef(animate);
  const [visible, setVisible] = useState(animate ? 0 : length);
  const target = useRef(length);
  const shown = useRef(animate ? 0 : length);
  const pending = useRef(loading);
  target.current = length;
  pending.current = loading;
  const previous = useRef(text);
  useEffect(() => {
    if (!live.current) return;
    if (!text.startsWith(previous.current)) { shown.current = 0; setVisible(0); }
    previous.current = text;
  }, [text]);
  useEffect(() => {
    if (!live.current) return;
    let frame = 0, last = performance.now(), credit = 0;
    const tick = (now: number) => {
      // One persistent clock: incoming chunks must not reset the timer or accelerate paragraphs.
      credit = Math.min(4, credit + Math.min(now - last, 80) * 0.055);
      last = now;
      const step = Math.floor(credit);
      if (step > 0 && shown.current < target.current) {
        shown.current = Math.min(target.current, shown.current + step);
        credit -= step;
        setVisible(shown.current);
      } else if (shown.current >= target.current) credit = 0;
      if (shown.current < target.current || pending.current) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);
  return { displayed: live.current ? revealCard(item, visible) : item,
    writing: live.current && visible < length };
}
