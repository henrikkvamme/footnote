import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

export const InView = ({
  children,
  className,
  id,
  onSeen,
}: {
  readonly children: ReactNode;
  readonly className?: string;
  readonly id?: string;
  readonly onSeen?: () => void;
}) => {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);

  useEffect(() => {
    const node = ref.current;

    if (node === null) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          onSeen?.();
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -18% 0px", threshold: 0.12 },
    );

    observer.observe(node);

    return () => {
      observer.disconnect();
    };
  }, [onSeen]);

  return (
    <div className={className} data-in={seen} id={id} ref={ref}>
      {children}
    </div>
  );
};

export const Ref = ({ n }: { readonly n: number }) => (
  <a aria-label={`Footnote ${n}`} className="ref" href={`#note-${n}`}>
    {n}
  </a>
);
